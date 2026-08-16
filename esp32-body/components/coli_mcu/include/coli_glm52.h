#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "coli_kv_cache.h"
#include "coli_model.h"
#include "coli_q4.h"

#ifdef __cplusplus
extern "C" {
#endif

#define COLI_GLM52_MAX_STOP_TOKENS 8u
#define COLI_GLM52_SCALE_ID_OFFSET 0x00800000u

typedef enum {
    COLI_GLM52_TENSOR_EMBED_TOKENS = 1u,
    COLI_GLM52_TENSOR_FINAL_NORM = 2u,
    COLI_GLM52_TENSOR_LM_HEAD = 3u,
} coli_glm52_global_tensor_id_t;

static inline uint32_t coli_glm52_layer_base(uint32_t layer)
{
    return 1000u + layer * 10000u;
}

static inline uint32_t coli_glm52_input_norm_id(uint32_t layer)
{
    return coli_glm52_layer_base(layer) + 1u;
}

static inline uint32_t coli_glm52_post_attention_norm_id(uint32_t layer)
{
    return coli_glm52_layer_base(layer) + 2u;
}

static inline uint32_t coli_glm52_q_a_id(uint32_t layer)
{
    return coli_glm52_layer_base(layer) + 10u;
}

static inline uint32_t coli_glm52_q_a_norm_id(uint32_t layer)
{
    return coli_glm52_layer_base(layer) + 11u;
}

static inline uint32_t coli_glm52_q_b_id(uint32_t layer)
{
    return coli_glm52_layer_base(layer) + 12u;
}

static inline uint32_t coli_glm52_kv_a_id(uint32_t layer)
{
    return coli_glm52_layer_base(layer) + 13u;
}

static inline uint32_t coli_glm52_kv_a_norm_id(uint32_t layer)
{
    return coli_glm52_layer_base(layer) + 14u;
}

static inline uint32_t coli_glm52_kv_b_id(uint32_t layer)
{
    return coli_glm52_layer_base(layer) + 15u;
}

static inline uint32_t coli_glm52_o_id(uint32_t layer)
{
    return coli_glm52_layer_base(layer) + 16u;
}

static inline uint32_t coli_glm52_scale_id(uint32_t tensor_id)
{
    return tensor_id + COLI_GLM52_SCALE_ID_OFFSET;
}

typedef struct {
    uint32_t hidden_size;
    uint32_t num_layers;
    uint32_t num_heads;
    uint32_t num_experts;
    uint32_t experts_per_token;
    uint32_t moe_intermediate_size;
    uint32_t dense_intermediate_size;
    uint32_t first_dense_layers;
    uint32_t q_lora_rank;
    uint32_t kv_lora_rank;
    uint32_t qk_nope_head_dim;
    uint32_t qk_rope_head_dim;
    uint32_t qk_head_dim;
    uint32_t v_head_dim;
    uint32_t shared_experts;
    uint32_t expert_groups;
    uint32_t topk_groups;
    uint32_t vocab_size;
    uint32_t max_context_tokens;
    uint32_t stop_token_ids[COLI_GLM52_MAX_STOP_TOKENS];
    size_t stop_token_count;
    float rms_norm_epsilon;
    float rope_theta;
    float attention_scale;
    float routed_scale;
    bool normalize_topk;
} coli_glm52_config_t;

typedef struct {
    coli_q4_stats_t projections;
    size_t peak_workspace_bytes;
} coli_glm52_attention_stats_t;

/** Decode and validate the bounded GLM-5.2 metadata stored in BMOQ v2. */
coli_status_t coli_glm52_config_load(const coli_model_t *model,
                                     coli_glm52_config_t *out_config);

/** Build the SSD-paged compressed MLA state layout: latent L plus RoPE R. */
coli_status_t coli_glm52_state_layout(const coli_glm52_config_t *config,
                                      coli_kv_cache_layout_t *out_layout);

/** Apply GLM's interleaved-input, split-output RoPE transform in place. */
coli_status_t coli_glm52_rope(float *vector, size_t count, uint32_t position,
                             float theta, float *scratch,
                             size_t scratch_count);

/**
 * Decode one MLA attention head from paged compressed state.
 *
 * The caller supplies only `token_count` scores plus one latent and RoPE row.
 * This avoids a heads-times-context score allocation on the ESP32.
 */
coli_status_t coli_glm52_attention_absorb_head(
    coli_kv_cache_t *state, uint32_t layer, const float *query_absorbed,
    size_t latent_count, const float *query_rope, size_t rope_count,
    uint32_t token_count, float attention_scale, float *output_latent,
    size_t output_count, float *score_workspace, size_t score_count,
    float *latent_scratch, size_t latent_scratch_count, float *rope_scratch,
    size_t rope_scratch_count);

size_t coli_glm52_attention_required_workspace(
    const coli_glm52_config_t *config, uint32_t token_count,
    size_t q4_workspace_bytes);

/** Execute one complete GLM-5.2 MLA attention block from SSD-backed weights/state. */
coli_status_t coli_glm52_attention_decode(
    const coli_model_t *model, const coli_glm52_config_t *config,
    uint32_t layer, uint32_t position, const float *input,
    size_t input_count, float *output, size_t output_count,
    coli_kv_cache_t *state, void *workspace, size_t workspace_bytes,
    coli_glm52_attention_stats_t *stats);

#ifdef __cplusplus
}
#endif
