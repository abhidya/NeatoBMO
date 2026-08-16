#include "coli_kv_cache.h"

#include <stdbool.h>
#include <float.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/types.h>

typedef enum {
    COLI_KV_BACKEND_RAM = 1,
    COLI_KV_BACKEND_FILE = 2,
} coli_kv_backend_t;

struct coli_kv_cache {
    coli_kv_cache_layout_t layout;
    coli_kv_backend_t backend;
    uint64_t reads;
    uint64_t writes;
    uint64_t flushes;
    union {
        struct {
            uint8_t *memory;
            size_t memory_bytes;
        } ram;
        struct {
            FILE *file;
            uint8_t *page;
            size_t page_bytes;
            uint64_t page_index;
            size_t valid_bytes;
            bool loaded;
            bool dirty;
        } file;
    } storage;
};

static bool mul_u64(uint64_t a, uint64_t b, uint64_t *out)
{
    if (!out) return false;
    if (a != 0 && b > UINT64_MAX / a) return false;
    *out = a * b;
    return true;
}

static bool add_u64(uint64_t a, uint64_t b, uint64_t *out)
{
    if (!out || b > UINT64_MAX - a) return false;
    *out = a + b;
    return true;
}

static coli_status_t seek_file(FILE *file, uint64_t offset)
{
#if defined(_WIN32)
    if (offset > INT64_MAX) return COLI_ERR_RANGE;
    return _fseeki64(file, (int64_t)offset, SEEK_SET) == 0 ? COLI_OK
                                                           : COLI_ERR_IO;
#else
    if (offset > (uint64_t)INT64_MAX) return COLI_ERR_RANGE;
    return fseeko(file, (off_t)offset, SEEK_SET) == 0 ? COLI_OK
                                                      : COLI_ERR_IO;
#endif
}

static coli_status_t validate_layout(const coli_kv_cache_layout_t *layout)
{
    uint64_t expected_key_layer;
    uint64_t expected_value_layer;
    uint64_t expected_stride;
    uint64_t expected_total;
    if (!layout || layout->layers == 0 || layout->max_tokens == 0 ||
        layout->key_token_bytes == 0 || layout->value_token_bytes == 0)
        return COLI_ERR_ARGUMENT;
    if (!mul_u64(layout->key_token_bytes, layout->max_tokens,
                 &expected_key_layer) ||
        !mul_u64(layout->value_token_bytes, layout->max_tokens,
                 &expected_value_layer) ||
        !add_u64(expected_key_layer, expected_value_layer, &expected_stride) ||
        !mul_u64(expected_stride, layout->layers, &expected_total))
        return COLI_ERR_RANGE;
    if (layout->key_layer_bytes != expected_key_layer ||
        layout->value_layer_bytes != expected_value_layer ||
        layout->layer_stride_bytes != expected_stride ||
        layout->total_bytes != expected_total)
        return COLI_ERR_ARGUMENT;
    return COLI_OK;
}

coli_status_t coli_kv_cache_layout_custom(
    uint32_t layers, uint32_t max_tokens, size_t key_token_bytes,
    size_t value_token_bytes, coli_kv_cache_layout_t *out_layout)
{
    if (!out_layout || layers == 0 || max_tokens == 0 ||
        key_token_bytes == 0 || value_token_bytes == 0)
        return COLI_ERR_ARGUMENT;

    uint64_t key_layer_bytes;
    uint64_t value_layer_bytes;
    uint64_t layer_stride;
    uint64_t total_bytes;
    if (!mul_u64(key_token_bytes, max_tokens, &key_layer_bytes) ||
        !mul_u64(value_token_bytes, max_tokens, &value_layer_bytes) ||
        !add_u64(key_layer_bytes, value_layer_bytes, &layer_stride) ||
        !mul_u64(layer_stride, layers, &total_bytes))
        return COLI_ERR_RANGE;

    memset(out_layout, 0, sizeof(*out_layout));
    out_layout->layers = layers;
    out_layout->max_tokens = max_tokens;
    out_layout->key_token_bytes = key_token_bytes;
    out_layout->value_token_bytes = value_token_bytes;
    out_layout->key_layer_bytes = key_layer_bytes;
    out_layout->value_layer_bytes = value_layer_bytes;
    out_layout->layer_stride_bytes = layer_stride;
    out_layout->total_bytes = total_bytes;
    return COLI_OK;
}

coli_status_t coli_kv_cache_layout(uint32_t layers, uint32_t heads,
                                   uint32_t head_dim, uint32_t max_tokens,
                                   size_t bytes_per_value,
                                   coli_kv_cache_layout_t *out_layout)
{
    if (!out_layout || layers == 0 || heads == 0 || head_dim == 0 ||
        max_tokens == 0 || bytes_per_value == 0)
        return COLI_ERR_ARGUMENT;

    uint64_t token_values;
    uint64_t token_bytes;
    coli_status_t status;
    if (!mul_u64(heads, head_dim, &token_values) ||
        !mul_u64(token_values, (uint64_t)bytes_per_value, &token_bytes))
        return COLI_ERR_RANGE;
    if (token_bytes > (uint64_t)SIZE_MAX) return COLI_ERR_RANGE;
    status = coli_kv_cache_layout_custom(layers, max_tokens,
                                         (size_t)token_bytes,
                                         (size_t)token_bytes, out_layout);
    if (status != COLI_OK) return status;
    out_layout->heads = heads;
    out_layout->head_dim = head_dim;
    out_layout->bytes_per_value = bytes_per_value;
    return COLI_OK;
}

static coli_status_t token_offsets(const coli_kv_cache_t *cache,
                                   uint32_t layer, uint32_t token,
                                   uint64_t *out_key_offset,
                                   uint64_t *out_value_offset)
{
    if (!cache || !out_key_offset || !out_value_offset) return COLI_ERR_ARGUMENT;
    const coli_kv_cache_layout_t *layout = &cache->layout;
    if (layer >= layout->layers || token >= layout->max_tokens)
        return COLI_ERR_RANGE;

    uint64_t layer_offset;
    uint64_t key_token_offset;
    uint64_t value_token_offset;
    uint64_t key_offset;
    uint64_t value_base;
    uint64_t value_offset;
    if (!mul_u64((uint64_t)layer, layout->layer_stride_bytes, &layer_offset) ||
        !mul_u64((uint64_t)token, layout->key_token_bytes,
                 &key_token_offset) ||
        !mul_u64((uint64_t)token, layout->value_token_bytes,
                 &value_token_offset) ||
        !add_u64(layer_offset, key_token_offset, &key_offset) ||
        !add_u64(layer_offset, layout->key_layer_bytes, &value_base) ||
        !add_u64(value_base, value_token_offset, &value_offset) ||
        layout->key_token_bytes > UINT64_MAX - key_offset ||
        key_offset + layout->key_token_bytes > layout->total_bytes ||
        layout->value_token_bytes > UINT64_MAX - value_offset ||
        value_offset + layout->value_token_bytes > layout->total_bytes)
        return COLI_ERR_RANGE;

    *out_key_offset = key_offset;
    *out_value_offset = value_offset;
    return COLI_OK;
}

coli_status_t coli_kv_cache_open_ram(const coli_kv_cache_layout_t *layout,
                                     void *memory, size_t memory_bytes,
                                     coli_kv_cache_t **out_cache)
{
    if (!layout || !memory || !out_cache) return COLI_ERR_ARGUMENT;
    if (layout->total_bytes > (uint64_t)SIZE_MAX ||
        memory_bytes < (size_t)layout->total_bytes)
        return COLI_ERR_ARGUMENT;

    coli_status_t status = validate_layout(layout);
    if (status != COLI_OK) return status;

    coli_kv_cache_t *cache = calloc(1, sizeof(*cache));
    if (!cache) return COLI_ERR_NO_MEMORY;
    cache->layout = *layout;
    cache->backend = COLI_KV_BACKEND_RAM;
    cache->storage.ram.memory = memory;
    cache->storage.ram.memory_bytes = memory_bytes;
    *out_cache = cache;
    return COLI_OK;
}

coli_status_t coli_kv_cache_open_file(const coli_kv_cache_layout_t *layout,
                                      const char *path, size_t page_bytes,
                                      coli_kv_cache_t **out_cache)
{
    if (!layout || !path || page_bytes == 0 || !out_cache)
        return COLI_ERR_ARGUMENT;

    coli_status_t status = validate_layout(layout);
    if (status != COLI_OK) return status;

    coli_kv_cache_t *cache = calloc(1, sizeof(*cache));
    if (!cache) return COLI_ERR_NO_MEMORY;
    cache->storage.file.page = calloc(1, page_bytes);
    if (!cache->storage.file.page) {
        free(cache);
        return COLI_ERR_NO_MEMORY;
    }

    FILE *file = fopen(path, "r+b");
    if (!file) file = fopen(path, "w+b");
    if (!file) {
        free(cache->storage.file.page);
        free(cache);
        return COLI_ERR_IO;
    }

    cache->layout = *layout;
    cache->backend = COLI_KV_BACKEND_FILE;
    cache->storage.file.file = file;
    cache->storage.file.page_bytes = page_bytes;
    *out_cache = cache;
    return COLI_OK;
}

static coli_status_t flush_file_page(coli_kv_cache_t *cache)
{
    if (cache->backend != COLI_KV_BACKEND_FILE) return COLI_OK;
    if (!cache->storage.file.loaded || !cache->storage.file.dirty)
        return COLI_OK;

    uint64_t offset;
    if (!mul_u64(cache->storage.file.page_index,
                 (uint64_t)cache->storage.file.page_bytes, &offset))
        return COLI_ERR_RANGE;
    coli_status_t status = seek_file(cache->storage.file.file, offset);
    if (status != COLI_OK) return status;
    if (offset >= cache->layout.total_bytes) return COLI_ERR_RANGE;
    uint64_t remaining = cache->layout.total_bytes - offset;
    size_t write_bytes = cache->storage.file.page_bytes;
    if (remaining < write_bytes) write_bytes = (size_t)remaining;
    if (fwrite(cache->storage.file.page, 1, write_bytes,
               cache->storage.file.file) != write_bytes)
        return COLI_ERR_IO;
    cache->storage.file.dirty = false;
    ++cache->flushes;
    return COLI_OK;
}

static coli_status_t load_file_page(coli_kv_cache_t *cache, uint64_t page_index)
{
    if (cache->storage.file.loaded &&
        cache->storage.file.page_index == page_index)
        return COLI_OK;

    coli_status_t status = flush_file_page(cache);
    if (status != COLI_OK) return status;

    uint64_t offset;
    if (!mul_u64(page_index, (uint64_t)cache->storage.file.page_bytes, &offset))
        return COLI_ERR_RANGE;
    status = seek_file(cache->storage.file.file, offset);
    if (status != COLI_OK) return status;

    memset(cache->storage.file.page, 0, cache->storage.file.page_bytes);
    const size_t got = fread(cache->storage.file.page, 1,
                             cache->storage.file.page_bytes,
                             cache->storage.file.file);
    if (got < cache->storage.file.page_bytes && ferror(cache->storage.file.file))
        return COLI_ERR_IO;
    cache->storage.file.page_index = page_index;
    cache->storage.file.valid_bytes = got;
    cache->storage.file.loaded = true;
    cache->storage.file.dirty = false;
    clearerr(cache->storage.file.file);
    return COLI_OK;
}

static coli_status_t file_transfer(coli_kv_cache_t *cache, uint64_t offset,
                                   void *buffer, size_t length, bool write)
{
    uint8_t *bytes = buffer;
    while (length > 0) {
        const size_t page_bytes = cache->storage.file.page_bytes;
        const uint64_t page_index = offset / page_bytes;
        const size_t page_offset = (size_t)(offset % page_bytes);
        size_t chunk = page_bytes - page_offset;
        if (chunk > length) chunk = length;

        coli_status_t status = load_file_page(cache, page_index);
        if (status != COLI_OK) return status;
        if (write) {
            memcpy(cache->storage.file.page + page_offset, bytes, chunk);
            if (page_offset + chunk > cache->storage.file.valid_bytes)
                cache->storage.file.valid_bytes = page_offset + chunk;
            cache->storage.file.dirty = true;
        } else {
            memcpy(bytes, cache->storage.file.page + page_offset, chunk);
        }

        bytes += chunk;
        offset += chunk;
        length -= chunk;
    }
    return COLI_OK;
}

static coli_status_t transfer_span(coli_kv_cache_t *cache, uint64_t offset,
                                   void *buffer, size_t length, bool write)
{
    if (!cache || !buffer) return COLI_ERR_ARGUMENT;
    if (length == 0) return COLI_ERR_ARGUMENT;
    if (offset > UINT64_MAX - (uint64_t)length ||
        offset + (uint64_t)length > cache->layout.total_bytes)
        return COLI_ERR_RANGE;

    if (cache->backend == COLI_KV_BACKEND_RAM) {
        if (offset + (uint64_t)length > cache->storage.ram.memory_bytes)
            return COLI_ERR_RANGE;
        uint8_t *base = cache->storage.ram.memory + (size_t)offset;
        if (write)
            memcpy(base, buffer, length);
        else
            memcpy(buffer, base, length);
        return COLI_OK;
    }
    return file_transfer(cache, offset, buffer, length, write);
}

coli_status_t coli_kv_cache_write_token(coli_kv_cache_t *cache,
                                        uint32_t layer, uint32_t token,
                                        const void *key, const void *value)
{
    if (!cache || !key || !value) return COLI_ERR_ARGUMENT;
    if (cache->layout.key_token_bytes > (uint64_t)SIZE_MAX ||
        cache->layout.value_token_bytes > (uint64_t)SIZE_MAX)
        return COLI_ERR_RANGE;

    uint64_t key_offset;
    uint64_t value_offset;
    coli_status_t status =
        token_offsets(cache, layer, token, &key_offset, &value_offset);
    if (status != COLI_OK) return status;

    status = transfer_span(cache, key_offset, (void *)key,
                           (size_t)cache->layout.key_token_bytes, true);
    if (status != COLI_OK) return status;
    status = transfer_span(cache, value_offset, (void *)value,
                           (size_t)cache->layout.value_token_bytes, true);
    if (status == COLI_OK) ++cache->writes;
    return status;
}

coli_status_t coli_kv_cache_read_token(coli_kv_cache_t *cache,
                                       uint32_t layer, uint32_t token,
                                       void *key, void *value)
{
    if (!cache || !key || !value) return COLI_ERR_ARGUMENT;
    if (cache->layout.key_token_bytes > (uint64_t)SIZE_MAX ||
        cache->layout.value_token_bytes > (uint64_t)SIZE_MAX)
        return COLI_ERR_RANGE;

    uint64_t key_offset;
    uint64_t value_offset;
    coli_status_t status =
        token_offsets(cache, layer, token, &key_offset, &value_offset);
    if (status != COLI_OK) return status;

    status = transfer_span(cache, key_offset, key,
                           (size_t)cache->layout.key_token_bytes, false);
    if (status != COLI_OK) return status;
    status = transfer_span(cache, value_offset, value,
                           (size_t)cache->layout.value_token_bytes, false);
    if (status == COLI_OK) ++cache->reads;
    return status;
}

static coli_status_t read_plane(coli_kv_cache_t *cache, uint32_t layer,
                                uint32_t token, bool value_plane,
                                void *destination)
{
    if (!cache || !destination) return COLI_ERR_ARGUMENT;
    const uint64_t token_bytes = value_plane
                                     ? cache->layout.value_token_bytes
                                     : cache->layout.key_token_bytes;
    if (token_bytes > (uint64_t)SIZE_MAX) return COLI_ERR_RANGE;
    uint64_t key_offset;
    uint64_t value_offset;
    coli_status_t status =
        token_offsets(cache, layer, token, &key_offset, &value_offset);
    if (status != COLI_OK) return status;
    status = transfer_span(cache, value_plane ? value_offset : key_offset,
                           destination, (size_t)token_bytes, false);
    if (status == COLI_OK) ++cache->reads;
    return status;
}

coli_status_t coli_kv_cache_read_key(coli_kv_cache_t *cache,
                                     uint32_t layer, uint32_t token,
                                     void *key)
{
    return read_plane(cache, layer, token, false, key);
}

coli_status_t coli_kv_cache_read_value(coli_kv_cache_t *cache,
                                       uint32_t layer, uint32_t token,
                                       void *value)
{
    return read_plane(cache, layer, token, true, value);
}

coli_status_t coli_kv_cache_attention_decode(
    coli_kv_cache_t *cache, uint32_t layer, const float *query,
    uint32_t token_count, float *output, float *score_workspace,
    size_t score_count, float *vector_scratch, size_t vector_count)
{
    if (!cache || !query || !output || !score_workspace || !vector_scratch ||
        layer >= cache->layout.layers || token_count == 0 ||
        token_count > cache->layout.max_tokens ||
        cache->layout.bytes_per_value != sizeof(float) ||
        cache->layout.key_token_bytes != cache->layout.value_token_bytes)
        return COLI_ERR_ARGUMENT;

    const size_t heads = cache->layout.heads;
    const size_t head_dim = cache->layout.head_dim;
    if (heads != 0 && head_dim > SIZE_MAX / heads) return COLI_ERR_RANGE;
    const size_t vector_values = heads * head_dim;
    if (vector_values > SIZE_MAX / sizeof(float) ||
        cache->layout.key_token_bytes != vector_values * sizeof(float))
        return COLI_ERR_ARGUMENT;
    if (heads != 0 && token_count > SIZE_MAX / heads) return COLI_ERR_RANGE;
    const size_t required_scores = heads * token_count;
    if (score_count < required_scores || vector_count < vector_values)
        return COLI_ERR_RANGE;

    const float inv_sqrt_dim = 1.0f / sqrtf((float)head_dim);
    for (uint32_t token = 0; token < token_count; ++token) {
        coli_status_t status =
            coli_kv_cache_read_key(cache, layer, token, vector_scratch);
        if (status != COLI_OK) return status;
        for (size_t head = 0; head < heads; ++head) {
            const float *q = query + head * head_dim;
            const float *key = vector_scratch + head * head_dim;
            float score = 0.0f;
            for (size_t d = 0; d < head_dim; ++d) score += q[d] * key[d];
            score_workspace[head * token_count + token] =
                score * inv_sqrt_dim;
        }
    }

    for (size_t head = 0; head < heads; ++head) {
        float *scores = score_workspace + head * token_count;
        float max_score = -FLT_MAX;
        for (uint32_t token = 0; token < token_count; ++token)
            if (scores[token] > max_score) max_score = scores[token];
        float sum = 0.0f;
        for (uint32_t token = 0; token < token_count; ++token) {
            scores[token] = expf(scores[token] - max_score);
            sum += scores[token];
        }
        if (!(sum > 0.0f)) return COLI_ERR_RANGE;
        for (uint32_t token = 0; token < token_count; ++token)
            scores[token] /= sum;
    }

    memset(output, 0, vector_values * sizeof(*output));
    for (uint32_t token = 0; token < token_count; ++token) {
        coli_status_t status =
            coli_kv_cache_read_value(cache, layer, token, vector_scratch);
        if (status != COLI_OK) return status;
        for (size_t head = 0; head < heads; ++head) {
            float *out = output + head * head_dim;
            const float *value = vector_scratch + head * head_dim;
            const float weight = score_workspace[head * token_count + token];
            for (size_t d = 0; d < head_dim; ++d)
                out[d] += weight * value[d];
        }
    }
    return COLI_OK;
}

coli_status_t coli_kv_cache_flush(coli_kv_cache_t *cache)
{
    if (!cache) return COLI_ERR_ARGUMENT;
    coli_status_t status = flush_file_page(cache);
    if (status != COLI_OK) return status;
    if (cache->backend == COLI_KV_BACKEND_FILE &&
        fflush(cache->storage.file.file) != 0)
        return COLI_ERR_IO;
    return COLI_OK;
}

void coli_kv_cache_stats(const coli_kv_cache_t *cache,
                         coli_kv_cache_stats_t *out_stats)
{
    if (!out_stats) return;
    memset(out_stats, 0, sizeof(*out_stats));
    if (!cache) return;
    out_stats->logical_bytes = cache->layout.total_bytes;
    out_stats->read_count = cache->reads;
    out_stats->write_count = cache->writes;
    out_stats->flush_count = cache->flushes;
    if (cache->backend == COLI_KV_BACKEND_RAM)
        out_stats->resident_bytes =
            cache->layout.total_bytes > (uint64_t)SIZE_MAX
                ? SIZE_MAX
                : (size_t)cache->layout.total_bytes;
    else
        out_stats->resident_bytes = cache->storage.file.page_bytes;
}

const coli_kv_cache_layout_t *coli_kv_cache_get_layout(
    const coli_kv_cache_t *cache)
{
    return cache ? &cache->layout : NULL;
}

void coli_kv_cache_close(coli_kv_cache_t *cache)
{
    if (!cache) return;
    (void)coli_kv_cache_flush(cache);
    if (cache->backend == COLI_KV_BACKEND_FILE) {
        if (cache->storage.file.file) fclose(cache->storage.file.file);
        free(cache->storage.file.page);
    }
    free(cache);
}
