#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "coli_kv_cache.h"

static void fill_pattern(uint8_t *bytes, size_t count, uint8_t seed)
{
    for (size_t i = 0; i < count; ++i)
        bytes[i] = (uint8_t)(seed + (uint8_t)(i * 17u));
}

static void assert_token(coli_kv_cache_t *cache, uint32_t layer,
                         uint32_t token, const uint8_t *key,
                         const uint8_t *value, size_t token_bytes)
{
    uint8_t got_key[64];
    uint8_t got_value[64];
    assert(token_bytes <= sizeof(got_key));
    memset(got_key, 0, sizeof(got_key));
    memset(got_value, 0, sizeof(got_value));
    assert(coli_kv_cache_read_token(cache, layer, token, got_key, got_value) ==
           COLI_OK);
    assert(memcmp(got_key, key, token_bytes) == 0);
    assert(memcmp(got_value, value, token_bytes) == 0);
}

static void test_ram_file_parity_and_boundary_crossing(void)
{
    coli_kv_cache_layout_t layout;
    assert(coli_kv_cache_layout(2, 3, 5, 7, 1, &layout) == COLI_OK);
    const size_t token_bytes = (size_t)layout.heads * layout.head_dim;

    uint8_t ram_memory[2u * 2u * 7u * 15u];
    memset(ram_memory, 0, sizeof(ram_memory));
    coli_kv_cache_t *ram = NULL;
    assert(coli_kv_cache_open_ram(&layout, ram_memory, sizeof(ram_memory),
                                  &ram) == COLI_OK);

    char path[] = "/tmp/coli-kv-cache-XXXXXX";
    int fd = mkstemp(path);
    assert(fd >= 0);
    close(fd);
    coli_kv_cache_t *file = NULL;
    assert(coli_kv_cache_open_file(&layout, path, 17, &file) == COLI_OK);

    uint8_t key[64];
    uint8_t value[64];
    for (uint32_t layer = 0; layer < layout.layers; ++layer) {
        for (uint32_t token = 0; token < layout.max_tokens; ++token) {
            fill_pattern(key, token_bytes,
                         (uint8_t)(11u + layer * 19u + token));
            fill_pattern(value, token_bytes,
                         (uint8_t)(93u + layer * 7u + token * 3u));
            assert(coli_kv_cache_write_token(ram, layer, token, key, value) ==
                   COLI_OK);
            assert(coli_kv_cache_write_token(file, layer, token, key, value) ==
                   COLI_OK);
        }
    }

    for (uint32_t layer = 0; layer < layout.layers; ++layer) {
        for (uint32_t token = 0; token < layout.max_tokens; ++token) {
            fill_pattern(key, token_bytes,
                         (uint8_t)(11u + layer * 19u + token));
            fill_pattern(value, token_bytes,
                         (uint8_t)(93u + layer * 7u + token * 3u));
            assert_token(ram, layer, token, key, value, token_bytes);
            assert_token(file, layer, token, key, value, token_bytes);
        }
    }

    coli_kv_cache_stats_t stats;
    coli_kv_cache_stats(file, &stats);
    assert(stats.logical_bytes == layout.total_bytes);
    assert(stats.resident_bytes == 17);
    assert(stats.write_count == (uint64_t)layout.layers * layout.max_tokens);
    assert(stats.read_count == (uint64_t)layout.layers * layout.max_tokens);
    assert(stats.flush_count > 1);

    coli_kv_cache_close(ram);
    coli_kv_cache_close(file);
    unlink(path);
}

static void test_persistence_reopen(void)
{
    coli_kv_cache_layout_t layout;
    assert(coli_kv_cache_layout(1, 2, 4, 4, sizeof(uint16_t), &layout) ==
           COLI_OK);
    const size_t token_bytes =
        (size_t)layout.heads * layout.head_dim * sizeof(uint16_t);
    uint8_t key[32];
    uint8_t value[32];
    fill_pattern(key, token_bytes, 41);
    fill_pattern(value, token_bytes, 211);

    char path[] = "/tmp/coli-kv-cache-reopen-XXXXXX";
    int fd = mkstemp(path);
    assert(fd >= 0);
    close(fd);

    coli_kv_cache_t *cache = NULL;
    assert(coli_kv_cache_open_file(&layout, path, 13, &cache) == COLI_OK);
    assert(coli_kv_cache_write_token(cache, 0, 2, key, value) == COLI_OK);
    assert(coli_kv_cache_flush(cache) == COLI_OK);
    coli_kv_cache_close(cache);

    cache = NULL;
    assert(coli_kv_cache_open_file(&layout, path, 13, &cache) == COLI_OK);
    assert_token(cache, 0, 2, key, value, token_bytes);
    coli_kv_cache_close(cache);
    unlink(path);
}

static void test_unequal_state_planes(void)
{
    coli_kv_cache_layout_t layout;
    assert(coli_kv_cache_layout_custom(2, 5, 12, 4, &layout) == COLI_OK);
    assert(layout.key_token_bytes == 12);
    assert(layout.value_token_bytes == 4);
    assert(layout.key_layer_bytes == 60);
    assert(layout.value_layer_bytes == 20);
    assert(layout.total_bytes == 160);

    char path[] = "/tmp/coli-kv-cache-unequal-XXXXXX";
    int fd = mkstemp(path);
    assert(fd >= 0);
    close(fd);

    coli_kv_cache_t *cache = NULL;
    assert(coli_kv_cache_open_file(&layout, path, 7, &cache) == COLI_OK);
    uint8_t key[12];
    uint8_t value[4];
    uint8_t got_key[12];
    uint8_t got_value[4];
    for (uint32_t layer = 0; layer < layout.layers; ++layer) {
        for (uint32_t token = 0; token < layout.max_tokens; ++token) {
            fill_pattern(key, sizeof(key), (uint8_t)(31 + layer + token));
            fill_pattern(value, sizeof(value),
                         (uint8_t)(171 + layer + token));
            assert(coli_kv_cache_write_token(cache, layer, token, key,
                                             value) == COLI_OK);
        }
    }
    assert(coli_kv_cache_flush(cache) == COLI_OK);
    coli_kv_cache_close(cache);

    assert(coli_kv_cache_open_file(&layout, path, 7, &cache) == COLI_OK);
    for (uint32_t layer = 0; layer < layout.layers; ++layer) {
        for (uint32_t token = 0; token < layout.max_tokens; ++token) {
            fill_pattern(key, sizeof(key), (uint8_t)(31 + layer + token));
            fill_pattern(value, sizeof(value),
                         (uint8_t)(171 + layer + token));
            assert(coli_kv_cache_read_token(cache, layer, token, got_key,
                                            got_value) == COLI_OK);
            assert(memcmp(got_key, key, sizeof(key)) == 0);
            assert(memcmp(got_value, value, sizeof(value)) == 0);
        }
    }
    coli_kv_cache_close(cache);
    unlink(path);
}

static void test_large_logical_context_small_resident(void)
{
    coli_kv_cache_layout_t layout;
    assert(coli_kv_cache_layout(32, 32, 128, 65536, 2, &layout) == COLI_OK);
    assert(layout.total_bytes > (uint64_t)16 * 1024 * 1024 * 1024);

    char path[] = "/tmp/coli-kv-cache-large-XXXXXX";
    int fd = mkstemp(path);
    assert(fd >= 0);
    close(fd);

    coli_kv_cache_t *cache = NULL;
    assert(coli_kv_cache_open_file(&layout, path, 4096, &cache) == COLI_OK);
    coli_kv_cache_stats_t stats;
    coli_kv_cache_stats(cache, &stats);
    assert(stats.logical_bytes == layout.total_bytes);
    assert(stats.resident_bytes == 4096);
    assert(stats.resident_bytes < stats.logical_bytes / 1024u);
    coli_kv_cache_close(cache);
    unlink(path);
}

static void test_invalid_cases(void)
{
    coli_kv_cache_layout_t layout;
    assert(coli_kv_cache_layout(0, 1, 1, 1, 1, &layout) ==
           COLI_ERR_ARGUMENT);
    assert(coli_kv_cache_layout(UINT32_MAX, UINT32_MAX, UINT32_MAX,
                                UINT32_MAX, UINT32_MAX, &layout) ==
           COLI_ERR_RANGE);
    assert(coli_kv_cache_layout(1, 1, 4, 2, 1, &layout) == COLI_OK);

    uint8_t too_small[7];
    coli_kv_cache_t *cache = NULL;
    assert(coli_kv_cache_open_ram(&layout, too_small, sizeof(too_small),
                                  &cache) == COLI_ERR_ARGUMENT);

    char path[] = "/tmp/coli-kv-cache-invalid-XXXXXX";
    int fd = mkstemp(path);
    assert(fd >= 0);
    close(fd);
    assert(coli_kv_cache_open_file(&layout, path, 0, &cache) ==
           COLI_ERR_ARGUMENT);
    assert(coli_kv_cache_open_file(&layout, path, 8, &cache) == COLI_OK);

    uint8_t key[4] = {1, 2, 3, 4};
    uint8_t value[4] = {5, 6, 7, 8};
    assert(coli_kv_cache_write_token(cache, 1, 0, key, value) ==
           COLI_ERR_RANGE);
    assert(coli_kv_cache_write_token(cache, 0, 2, key, value) ==
           COLI_ERR_RANGE);
    assert(coli_kv_cache_write_token(cache, 0, 0, NULL, value) ==
           COLI_ERR_ARGUMENT);
    assert(coli_kv_cache_read_token(cache, 0, 0, key, NULL) ==
           COLI_ERR_ARGUMENT);

    coli_kv_cache_close(cache);
    unlink(path);
}

int main(void)
{
    test_ram_file_parity_and_boundary_crossing();
    test_persistence_reopen();
    test_unequal_state_planes();
    test_large_logical_context_small_resident();
    test_invalid_cases();
    puts("coli_kv_cache ram/file backends: PASS");
    return 0;
}
