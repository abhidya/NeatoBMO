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

#define COLI_INKLING_MAX_STOP_TOKENS 8u
#define COLI_INKLING_MAX_TOP_K 8u
#define COLI_INKLING_MAX_SHARED_EXPERTS 8u
#define COLI_INKLING_MAX_LAYERS 256u
#define COLI_INKLING_SCALE_ID_OFFSET 0x00800000u

typedef enum {
    COLI_INKLING_TENSOR_EMBED_TOKENS = 1u,
    COLI_INKLING_TENSOR_EMBED_NORM = 2u,
    COLI_INKLING_TENSOR_FINAL_NORM = 3u,
    COLI_INKLING_TENSOR_LM_HEAD = 4u,
} coli_inkling_global_tensor_id_t;

static inline uint32_t coli_inkling_layer_base(uint32_t layer)
{
    return 1000u + layer * 10000u;
}

static inline uint32_t coli_inkling_input_norm_id(uint32_t layer)
{
    return coli_inkling_layer_base(layer) + 1u;
}

static inline uint32_t coli_inkling_post_attention_norm_id(uint32_t layer)
{
    return coli_inkling_layer_base(layer) + 2u;
}

static inline uint32_t coli_inkling_q_proj_id(uint32_t layer)
{
    return coli_inkling_layer_base(layer) + 10u;
}

static inline uint32_t coli_inkling_k_proj_id(uint32_t layer)
{
    return coli_inkling_layer_base(layer) + 11u;
}

static inline uint32_t coli_inkling_v_proj_id(uint32_t layer)
{
    return coli_inkling_layer_base(layer) + 12u;
}

static inline uint32_t coli_inkling_r_proj_id(uint32_t layer)
{
    return coli_inkling_layer_base(layer) + 13u;
}

static inline uint32_t coli_inkling_o_proj_id(uint32_t layer)
{
    return coli_inkling_layer_base(layer) + 14u;
}

static inline uint32_t coli_inkling_q_norm_id(uint32_t layer)
{
    return coli_inkling_layer_base(layer) + 15u;
}

static inline uint32_t coli_inkling_k_norm_id(uint32_t layer)
{
    return coli_inkling_layer_base(layer) + 16u;
}

static inline uint32_t coli_inkling_rel_proj_id(uint32_t layer)
{
    return coli_inkling_layer_base(layer) + 17u;
}

static inline uint32_t coli_inkling_k_conv_id(uint32_t layer)
{
    return coli_inkling_layer_base(layer) + 18u;
}

static inline uint32_t coli_inkling_v_conv_id(uint32_t layer)
{
    return coli_inkling_layer_base(layer) + 19u;
}

static inline uint32_t coli_inkling_attn_conv_id(uint32_t layer)
{
    return coli_inkling_layer_base(layer) + 20u;
}

static inline uint32_t coli_inkling_mlp_conv_id(uint32_t layer)
{
    return coli_inkling_layer_base(layer) + 21u;
}

static inline uint32_t coli_inkling_dense_gate_id(uint32_t layer)
{
    return coli_inkling_layer_base(layer) + 40u;
}

static inline uint32_t coli_inkling_dense_up_id(uint32_t layer)
{
    return coli_inkling_layer_base(layer) + 41u;
}

static inline uint32_t coli_inkling_dense_down_id(uint32_t layer)
{
    return coli_inkling_layer_base(layer) + 42u;
}

static inline uint32_t coli_inkling_dense_scale_id(uint32_t layer)
{
    return coli_inkling_layer_base(layer) + 43u;
}

static inline uint32_t coli_inkling_router_id(uint32_t layer)
{
    return coli_inkling_layer_base(layer) + 50u;
}

static inline uint32_t coli_inkling_router_bias_id(uint32_t layer)
{
    return coli_inkling_layer_base(layer) + 51u;
}

static inline uint32_t coli_inkling_router_scale_id(uint32_t layer)
{
    return coli_inkling_layer_base(layer) + 52u;
}

static inline uint32_t coli_inkling_shared_gate_id(uint32_t layer)
{
    return coli_inkling_layer_base(layer) + 60u;
}

static inline uint32_t coli_inkling_shared_up_id(uint32_t layer)
{
    return coli_inkling_layer_base(layer) + 61u;
}

static inline uint32_t coli_inkling_shared_down_id(uint32_t layer)
{
    return coli_inkling_layer_base(layer) + 62u;
}

static inline uint32_t coli_inkling_routed_gate_bundle_id(uint32_t layer)
{
    return coli_inkling_layer_base(layer) + 70u;
}

static inline uint32_t coli_inkling_routed_up_bundle_id(uint32_t layer)
{
    return coli_inkling_layer_base(layer) + 71u;
}

static inline uint32_t coli_inkling_routed_down_bundle_id(uint32_t layer)
{
    return coli_inkling_layer_base(layer) + 72u;
}

static inline uint32_t coli_inkling_scale_id(uint32_t tensor_id)
{
    return tensor_id + COLI_INKLING_SCALE_ID_OFFSET;
}

typedef struct {
    uint32_t hidden_size;
    uint32_t num_layers;
    uint32_t vocab_size;
    uint32_t unpadded_vocab_size;
    uint32_t num_heads;
    uint32_t num_key_value_heads;
    uint32_t head_dim;
    uint32_t swa_num_heads;
    uint32_t swa_num_key_value_heads;
    uint32_t swa_head_dim;
    uint32_t sliding_window;
    uint32_t d_rel;
    uint32_t rel_extent;
    uint32_t conv_kernel;
    uint32_t num_experts;
    uint32_t experts_per_token;
    uint32_t shared_experts;
    uint32_t moe_intermediate_size;
    uint32_t dense_intermediate_size;
    uint32_t dense_mlp_index;
    uint32_t max_context_tokens;
    uint32_t stop_token_ids[COLI_INKLING_MAX_STOP_TOKENS];
    size_t stop_token_count;
    uint32_t local_layer_ids[COLI_INKLING_MAX_LAYERS];
    size_t local_layer_count;
    uint32_t sparse_layer_ids[COLI_INKLING_MAX_LAYERS];
    size_t sparse_layer_count;
    float rms_norm_epsilon;
    float route_scale;
    float logits_mup_width_multiplier;
    float log_scaling_n_floor;
    float log_scaling_alpha;
} coli_inkling_config_t;

typedef enum {
    COLI_INKLING_CONV_K = 0,
    COLI_INKLING_CONV_V = 1,
    COLI_INKLING_CONV_ATTN = 2,
    COLI_INKLING_CONV_MLP = 3,
    COLI_INKLING_CONV_COUNT = 4,
} coli_inkling_conv_stream_t;

typedef struct {
    uint32_t selected_experts[COLI_INKLING_MAX_TOP_K];
    float routing_weights[COLI_INKLING_MAX_TOP_K + COLI_INKLING_MAX_SHARED_EXPERTS];
    size_t selected_count;
    size_t shared_count;
} coli_inkling_moe_stats_t;

typedef struct {
    coli_q4_stats_t attention_q4;
    coli_q4_stats_t mlp_q4;
    coli_inkling_moe_stats_t moe;
    size_t peak_workspace_bytes;
} coli_inkling_layer_stats_t;

typedef struct {
    coli_q4_stats_t embedding_q4;
    coli_q4_stats_t lm_head_q4;
    coli_inkling_layer_stats_t last_layer;
    uint32_t layers_executed;
    float selected_logit;
    size_t peak_workspace_bytes;
} coli_inkling_decode_stats_t;

typedef coli_status_t (*coli_inkling_token_fn)(void *context,
                                               uint32_t token_id,
                                               size_t generated_index);
typedef coli_status_t (*coli_inkling_next_token_fn)(void *context,
                                                    uint32_t previous_token_id,
                                                    size_t position,
                                                    uint32_t *out_token_id);

coli_status_t coli_inkling_config_load(const coli_model_t *model,
                                       coli_inkling_config_t *out_config);

coli_status_t coli_inkling_state_layout(const coli_inkling_config_t *config,
                                        coli_kv_cache_layout_t *out_layout);

bool coli_inkling_is_stop_token(const coli_inkling_config_t *config,
                                uint32_t token_id);

float coli_inkling_global_tau(const coli_inkling_config_t *config,
                              uint32_t token_count);

size_t coli_inkling_conv_state_floats(const coli_inkling_config_t *config,
                                      uint32_t channels);

size_t coli_inkling_layer_conv_state_floats(
    const coli_inkling_config_t *config, uint32_t layer);

coli_status_t coli_inkling_conv_state_layouts(
    const coli_inkling_config_t *config, coli_kv_cache_layout_t *out_kv_conv,
    coli_kv_cache_layout_t *out_residual_conv);

size_t coli_inkling_decode_required_workspace(
    const coli_inkling_config_t *config, uint32_t position,
    size_t q4_workspace_bytes);

coli_status_t coli_inkling_short_conv_step(const float *input,
                                           const float *weights,
                                           uint32_t channels,
                                           uint32_t kernel,
                                           float *state,
                                           size_t state_count,
                                           float *output,
                                           size_t output_count);

coli_status_t coli_inkling_qk_rmsnorm(float *heads,
                                      const float *weights,
                                      uint32_t head_count,
                                      uint32_t head_dim,
                                      float epsilon);

coli_status_t coli_inkling_attention_decode(
    const coli_inkling_config_t *config, coli_kv_cache_t *state,
    uint32_t layer, bool local_layer, uint32_t position, const float *query,
    const float *key, const float *value, const float *relative_bias,
    size_t relative_bias_count, float *output, size_t output_count,
    float *score_workspace, size_t score_count, float *key_scratch,
    size_t key_scratch_count, float *value_scratch, size_t value_scratch_count);

coli_status_t coli_inkling_dense_swiglu(const float *gate, const float *up,
                                        const float *down,
                                        uint32_t hidden_size,
                                        uint32_t intermediate_size,
                                        float global_scale,
                                        float *output,
                                        size_t output_count);

coli_status_t coli_inkling_moe_route(const coli_inkling_config_t *config,
                                     const float *router_logits,
                                     const float *router_bias,
                                     float global_scale,
                                     coli_inkling_moe_stats_t *stats);

coli_status_t coli_inkling_moe_combine(
    const coli_inkling_config_t *config, const coli_inkling_moe_stats_t *stats,
    const float *expert_outputs, size_t expert_output_count,
    const float *shared_outputs, size_t shared_output_count,
    float *output, size_t output_count);

coli_status_t coli_inkling_logits_argmax(const float *hidden,
                                         const float *lm_head,
                                         uint32_t vocab_size,
                                         uint32_t unpadded_vocab_size,
                                         uint32_t hidden_size,
                                         float mup_width_multiplier,
                                         uint32_t *out_token_id,
                                         float *out_logit);

coli_status_t coli_inkling_decode_next_token(
    const coli_model_t *model, const coli_inkling_config_t *config,
    uint32_t input_token_id, uint32_t position, coli_kv_cache_t *kv_cache,
    coli_kv_cache_t *conv_kv_cache, coli_kv_cache_t *conv_residual_cache,
    void *workspace, size_t workspace_bytes, uint32_t *out_token_id,
    coli_inkling_decode_stats_t *stats);

coli_status_t coli_inkling_generate_greedy_stream(
    const coli_inkling_config_t *config, uint32_t seed_token,
    size_t seed_position, size_t max_new_tokens, uint32_t *output_token_ids,
    size_t output_token_capacity, size_t *out_output_token_count,
    coli_inkling_next_token_fn next_token, void *next_token_context,
    coli_inkling_token_fn on_token, void *token_context);

#ifdef __cplusplus
}
#endif
