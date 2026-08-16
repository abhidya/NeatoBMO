#pragma once

#include <stddef.h>
#include <stdint.h>

#include "coli_ops.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef struct coli_kv_cache coli_kv_cache_t;

typedef struct {
    uint64_t logical_bytes;
    size_t resident_bytes;
    uint64_t read_count;
    uint64_t write_count;
    uint64_t flush_count;
} coli_kv_cache_stats_t;

coli_status_t coli_kv_cache_layout(uint32_t layers, uint32_t heads,
                                   uint32_t head_dim, uint32_t max_tokens,
                                   size_t bytes_per_value,
                                   coli_kv_cache_layout_t *out_layout);

/** Build a paged state layout whose two per-token planes have unequal sizes. */
coli_status_t coli_kv_cache_layout_custom(
    uint32_t layers, uint32_t max_tokens, size_t key_token_bytes,
    size_t value_token_bytes, coli_kv_cache_layout_t *out_layout);

/** Wrap caller-owned contiguous memory. The cache object itself is allocated. */
coli_status_t coli_kv_cache_open_ram(const coli_kv_cache_layout_t *layout,
                                     void *memory, size_t memory_bytes,
                                     coli_kv_cache_t **out_cache);

/**
 * Open a writable file-backed cache with one bounded resident page.
 *
 * The logical cache may be much larger than RAM. Unwritten file regions read as
 * zero; dirty pages are written on eviction, flush, or close.
 */
coli_status_t coli_kv_cache_open_file(const coli_kv_cache_layout_t *layout,
                                      const char *path, size_t page_bytes,
                                      coli_kv_cache_t **out_cache);

coli_status_t coli_kv_cache_read_token(coli_kv_cache_t *cache,
                                       uint32_t layer, uint32_t token,
                                       void *key, void *value);

coli_status_t coli_kv_cache_read_key(coli_kv_cache_t *cache,
                                     uint32_t layer, uint32_t token,
                                     void *key);

coli_status_t coli_kv_cache_read_value(coli_kv_cache_t *cache,
                                       uint32_t layer, uint32_t token,
                                       void *value);

coli_status_t coli_kv_cache_write_token(coli_kv_cache_t *cache,
                                        uint32_t layer, uint32_t token,
                                        const void *key, const void *value);

coli_status_t coli_kv_cache_flush(coli_kv_cache_t *cache);

/**
 * Single-token causal attention over a bounded KV backend.
 *
 * `score_workspace` needs `heads * token_count` floats. `vector_scratch`
 * needs one complete key/value vector (`heads * head_dim` floats).
 */
coli_status_t coli_kv_cache_attention_decode(
    coli_kv_cache_t *cache, uint32_t layer, const float *query,
    uint32_t token_count, float *output, float *score_workspace,
    size_t score_count, float *vector_scratch, size_t vector_count);

void coli_kv_cache_stats(const coli_kv_cache_t *cache,
                         coli_kv_cache_stats_t *out_stats);
const coli_kv_cache_layout_t *coli_kv_cache_get_layout(
    const coli_kv_cache_t *cache);
void coli_kv_cache_close(coli_kv_cache_t *cache);

#ifdef __cplusplus
}
#endif
