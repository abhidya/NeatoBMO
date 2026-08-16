#include <assert.h>
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "coli_kv_cache.h"
#include "coli_ops.h"

static void assert_close(const float *left, const float *right, size_t count)
{
    for (size_t i = 0; i < count; ++i)
        assert(fabsf(left[i] - right[i]) < 1.0e-5f);
}

int main(void)
{
    enum { TOKENS = 4, HEADS = 2, DIM = 3, VALUES = HEADS * DIM };
    const float query[VALUES] = {0.3f, -0.2f, 0.7f, 0.4f, 0.9f, -0.1f};
    float keys[TOKENS * VALUES];
    float values[TOKENS * VALUES];
    for (size_t i = 0; i < TOKENS * VALUES; ++i) {
        keys[i] = (float)((int)(i % 7) - 3) * 0.25f;
        values[i] = (float)((int)(i % 5) - 2) * 0.4f;
    }

    float expected[VALUES];
    float legacy_scores[TOKENS];
    assert(coli_ops_attention_decode(query, keys, values, TOKENS, HEADS, DIM,
                                     expected, legacy_scores, TOKENS) ==
           COLI_OK);

    coli_kv_cache_layout_t layout;
    assert(coli_kv_cache_layout(1, HEADS, DIM, TOKENS, sizeof(float),
                                &layout) == COLI_OK);
    char path[] = "/tmp/coli-kv-attention-XXXXXX";
    int fd = mkstemp(path);
    assert(fd >= 0);
    close(fd);
    coli_kv_cache_t *cache = NULL;
    assert(coli_kv_cache_open_file(&layout, path, 29, &cache) == COLI_OK);
    for (uint32_t token = 0; token < TOKENS; ++token)
        assert(coli_kv_cache_write_token(
                   cache, 0, token, keys + token * VALUES,
                   values + token * VALUES) == COLI_OK);

    float actual[VALUES];
    float paged_scores[HEADS * TOKENS];
    float scratch[VALUES];
    assert(coli_kv_cache_attention_decode(
               cache, 0, query, TOKENS, actual, paged_scores,
               HEADS * TOKENS, scratch, VALUES) == COLI_OK);
    assert_close(actual, expected, VALUES);

    coli_kv_cache_stats_t stats;
    coli_kv_cache_stats(cache, &stats);
    assert(stats.resident_bytes == 29);
    assert(stats.read_count == TOKENS * 2u);
    coli_kv_cache_close(cache);
    unlink(path);
    puts("paged KV attention parity: PASS");
    return 0;
}
