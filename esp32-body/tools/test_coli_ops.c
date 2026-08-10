#include <assert.h>
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "coli_ops.h"

#define EPS 0.00002f

static void assert_close(float actual, double expected, double tolerance)
{
    assert(fabs((double)actual - expected) <= tolerance);
}

static void test_rmsnorm(void)
{
    const float input[] = {1.0f, -2.0f, 0.5f, 4.0f};
    const float weight[] = {0.5f, -1.0f, 1.5f, 2.0f};
    float output[4];
    assert(coli_ops_rmsnorm(input, weight, output, 4, 0.0001f) == COLI_OK);

    double sum = 0.0;
    for (size_t i = 0; i < 4; ++i) sum += (double)input[i] * input[i];
    const double scale = 1.0 / sqrt(sum / 4.0 + 0.0001);
    for (size_t i = 0; i < 4; ++i)
        assert_close(output[i], (double)input[i] * scale * weight[i], EPS);
    assert(coli_ops_rmsnorm(input, weight, output, 0, 0.0001f) ==
           COLI_ERR_ARGUMENT);
}

static void test_residual_and_silu(void)
{
    const float left[] = {0.25f, -1.0f, 3.0f};
    const float right[] = {1.75f, 0.5f, -4.0f};
    float output[3];
    assert(coli_ops_residual_add(left, right, output, 3) == COLI_OK);
    assert_close(output[0], 2.0, EPS);
    assert_close(output[1], -0.5, EPS);
    assert_close(output[2], -1.0, EPS);

    const float gate[] = {-2.0f, 0.0f, 3.0f};
    const float up[] = {4.0f, -5.0f, 0.25f};
    assert(coli_ops_silu_gated(gate, up, output, 3) == COLI_OK);
    for (size_t i = 0; i < 3; ++i) {
        const double sigmoid = 1.0 / (1.0 + exp(-(double)gate[i]));
        assert_close(output[i], (double)gate[i] * sigmoid * up[i], EPS);
    }
}

static void test_rope(void)
{
    float query[] = {1.0f, 2.0f, -3.0f, 4.0f, 0.5f, -0.25f, 2.0f, -1.0f};
    float key[] = {-1.0f, 0.75f, 2.5f, -3.5f, -0.5f, 1.25f, -2.0f, 1.0f};
    float ref_query[8];
    float ref_key[8];
    memcpy(ref_query, query, sizeof(query));
    memcpy(ref_key, key, sizeof(key));
    assert(coli_ops_rope_apply(query, key, 2, 4, 7, 10000.0f) == COLI_OK);

    for (uint32_t head = 0; head < 2; ++head) {
        for (uint32_t d = 0; d < 4; d += 2) {
            const double angle = 7.0 / pow(10000.0, (double)d / 4.0);
            const double c = cos(angle);
            const double s = sin(angle);
            const size_t i = (size_t)head * 4u + d;
            assert_close(query[i], ref_query[i] * c - ref_query[i + 1] * s,
                         EPS);
            assert_close(query[i + 1], ref_query[i] * s + ref_query[i + 1] * c,
                         EPS);
            assert_close(key[i], ref_key[i] * c - ref_key[i + 1] * s, EPS);
            assert_close(key[i + 1], ref_key[i] * s + ref_key[i + 1] * c, EPS);
        }
    }
    assert(coli_ops_rope_apply(query, key, 2, 3, 7, 10000.0f) ==
           COLI_ERR_ARGUMENT);
}

static void test_attention(void)
{
    const uint32_t tokens = 3;
    const uint32_t heads = 2;
    const uint32_t dim = 4;
    const size_t values = (size_t)tokens * heads * dim;
    float query[8];
    float keys[24];
    float vals[24];
    for (size_t i = 0; i < sizeof(query) / sizeof(query[0]); ++i)
        query[i] = (float)((int)(i % 5u) - 2) * 0.2f;
    for (size_t i = 0; i < values; ++i) {
        keys[i] = (float)((int)(i % 7u) - 3) * 0.125f;
        vals[i] = (float)((int)(i % 11u) - 5) * 0.0625f;
    }
    float output[8];
    float scores[3];
    assert(coli_ops_attention_decode(query, keys, vals, tokens, heads, dim,
                                     output, scores, 3) == COLI_OK);

    for (uint32_t head = 0; head < heads; ++head) {
        double ref_scores[3];
        double max_score = -1.0e100;
        for (uint32_t token = 0; token < tokens; ++token) {
            double dot = 0.0;
            for (uint32_t d = 0; d < dim; ++d) {
                const size_t qi = (size_t)head * dim + d;
                const size_t ki = ((size_t)token * heads + head) * dim + d;
                dot += (double)query[qi] * keys[ki];
            }
            ref_scores[token] = dot / sqrt((double)dim);
            if (ref_scores[token] > max_score) max_score = ref_scores[token];
        }
        double denom = 0.0;
        for (uint32_t token = 0; token < tokens; ++token) {
            ref_scores[token] = exp(ref_scores[token] - max_score);
            denom += ref_scores[token];
        }
        for (uint32_t d = 0; d < dim; ++d) {
            double expected = 0.0;
            for (uint32_t token = 0; token < tokens; ++token) {
                const size_t vi = ((size_t)token * heads + head) * dim + d;
                expected += ref_scores[token] / denom * vals[vi];
            }
            assert_close(output[(size_t)head * dim + d], expected, EPS);
        }
    }
    assert(coli_ops_attention_decode(query, keys, vals, tokens, heads, dim,
                                     output, scores, 2) == COLI_ERR_ARGUMENT);
}

static void test_kv_layout(void)
{
    coli_kv_cache_layout_t layout;
    assert(coli_ops_kv_cache_layout(16, COLI_OLMOE_ATTENTION_HEADS,
                                    COLI_OLMOE_HEAD_DIM, 32, sizeof(float),
                                    &layout) == COLI_OK);
    assert(layout.key_layer_bytes ==
           32u * COLI_OLMOE_HIDDEN_SIZE * sizeof(float));
    assert(layout.layer_stride_bytes == layout.key_layer_bytes * 2u);
    assert(layout.total_bytes == layout.layer_stride_bytes * 16u);

    uint8_t cache[2u * 2u * 2u * 3u * 4u * sizeof(float)];
    assert(coli_ops_kv_cache_layout(2, 2, 3, 4, sizeof(float), &layout) ==
           COLI_OK);
    void *key = NULL;
    void *value = NULL;
    assert(coli_ops_kv_cache_token_ptrs(cache, sizeof(cache), &layout, 1, 2,
                                        &key, &value) == COLI_OK);
    const size_t token_bytes = 2u * 3u * sizeof(float);
    assert((uint8_t *)key == cache + layout.layer_stride_bytes +
                                2u * token_bytes);
    assert((uint8_t *)value == cache + layout.layer_stride_bytes +
                                  layout.key_layer_bytes + 2u * token_bytes);
    assert(coli_ops_kv_cache_token_ptrs(cache, sizeof(cache) - 1u, &layout, 1,
                                        2, &key, &value) == COLI_ERR_ARGUMENT);
    assert(coli_ops_kv_cache_layout(UINT32_MAX, UINT32_MAX, UINT32_MAX,
                                    UINT32_MAX, UINT32_MAX, &layout) ==
           COLI_ERR_RANGE);
}

int main(void)
{
    test_rmsnorm();
    test_residual_and_silu();
    test_rope();
    test_attention();
    test_kv_layout();
    puts("coli_ops transformer primitives: PASS");
    return 0;
}
