#pragma once

#include <stddef.h>
#include <stdint.h>

#include "coli_store.h"

#ifdef __cplusplus
extern "C" {
#endif

#define COLI_OLMOE_HIDDEN_SIZE 2048u
#define COLI_OLMOE_ATTENTION_HEADS 16u
#define COLI_OLMOE_HEAD_DIM 128u

typedef struct {
    uint32_t layers;
    uint32_t heads;
    uint32_t head_dim;
    uint32_t max_tokens;
    size_t bytes_per_value;
    uint64_t key_token_bytes;
    uint64_t value_token_bytes;
    uint64_t key_layer_bytes;
    uint64_t value_layer_bytes;
    uint64_t layer_stride_bytes;
    uint64_t total_bytes;
} coli_kv_cache_layout_t;

coli_status_t coli_ops_rmsnorm(const float *input, const float *weight,
                               float *output, size_t count, float epsilon);

coli_status_t coli_ops_residual_add(const float *left, const float *right,
                                    float *output, size_t count);

coli_status_t coli_ops_silu_gated(const float *gate, const float *up,
                                  float *output, size_t count);

/**
 * Apply rotary position embedding in place to token-local Q/K vectors.
 *
 * Q and K use head-major layout: [head][head_dim]. `head_dim` must be even.
 */
coli_status_t coli_ops_rope_apply(float *query, float *key, uint32_t heads,
                                  uint32_t head_dim, uint32_t position,
                                  float theta_base);

/**
 * Single-token causal attention decode.
 *
 * Keys and values use token-major layout: [token][head][head_dim]. Query and
 * output use head-major layout. `score_workspace` needs at least `token_count`
 * floats and is reused per head.
 */
coli_status_t coli_ops_attention_decode(const float *query, const float *keys,
                                        const float *values,
                                        uint32_t token_count, uint32_t heads,
                                        uint32_t head_dim, float *output,
                                        float *score_workspace,
                                        size_t score_count);

coli_status_t coli_ops_kv_cache_layout(uint32_t layers, uint32_t heads,
                                       uint32_t head_dim, uint32_t max_tokens,
                                       size_t bytes_per_value,
                                       coli_kv_cache_layout_t *out_layout);

coli_status_t coli_ops_kv_cache_token_ptrs(
    uint8_t *cache, size_t cache_bytes, const coli_kv_cache_layout_t *layout,
    uint32_t layer, uint32_t token, void **out_key, void **out_value);

#ifdef __cplusplus
}
#endif
