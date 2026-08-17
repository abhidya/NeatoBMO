#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "coli_moe.h"
#include "coli_model.h"
#include "coli_ops.h"
#include "coli_kv_cache.h"

#ifdef __cplusplus
extern "C" {
#endif

#define COLI_OLMOE_EXPECTED_HIDDEN_SIZE 2048u
#define COLI_OLMOE_EXPECTED_INTERMEDIATE_SIZE 1024u
#define COLI_OLMOE_EXPECTED_LAYERS 16u
#define COLI_OLMOE_EXPECTED_ATTENTION_HEADS 16u
#define COLI_OLMOE_EXPECTED_KV_HEADS 16u
#define COLI_OLMOE_EXPECTED_EXPERTS 64u
#define COLI_OLMOE_EXPECTED_EXPERTS_PER_TOKEN 8u
#define COLI_OLMOE_EXPECTED_VOCAB_SIZE 50304u
#define COLI_OLMOE_EXPECTED_MAX_POSITIONS 4096u
#define COLI_OLMOE_EXPECTED_ROPE_THETA 10000u
#define COLI_OLMOE_EXPECTED_EOS_TOKEN_ID 50279u
#define COLI_OLMOE_EXPECTED_PAD_TOKEN_ID 1u

#define COLI_OLMOE_MANIFEST_TENSOR_ID 0x4f4c4d45u
#define COLI_OLMOE_SCALE_ID_OFFSET 0x00800000u

typedef enum {
    COLI_OLMOE_TENSOR_EMBED_TOKENS = 1u,
    COLI_OLMOE_TENSOR_FINAL_NORM = 2u,
    COLI_OLMOE_TENSOR_LM_HEAD = 3u,
} coli_olmoe_global_tensor_id_t;

static inline uint32_t coli_olmoe_layer_base(uint32_t layer)
{
    return 1000u + layer * 10000u;
}

static inline uint32_t coli_olmoe_input_norm_id(uint32_t layer)
{
    return coli_olmoe_layer_base(layer) + 1u;
}

static inline uint32_t coli_olmoe_post_attention_norm_id(uint32_t layer)
{
    return coli_olmoe_layer_base(layer) + 2u;
}

static inline uint32_t coli_olmoe_q_proj_id(uint32_t layer)
{
    return coli_olmoe_layer_base(layer) + 10u;
}

static inline uint32_t coli_olmoe_k_proj_id(uint32_t layer)
{
    return coli_olmoe_layer_base(layer) + 11u;
}

static inline uint32_t coli_olmoe_v_proj_id(uint32_t layer)
{
    return coli_olmoe_layer_base(layer) + 12u;
}

static inline uint32_t coli_olmoe_o_proj_id(uint32_t layer)
{
    return coli_olmoe_layer_base(layer) + 13u;
}

/* OLMoE RMS-normalizes the projected query and key vectors before RoPE
 * (OlmoeAttention applies q_norm/k_norm across the full num_heads*head_dim
 * vector). Omitting these tensors silently changes the architecture, not just
 * its precision. */
static inline uint32_t coli_olmoe_q_norm_id(uint32_t layer)
{
    return coli_olmoe_layer_base(layer) + 14u;
}

static inline uint32_t coli_olmoe_k_norm_id(uint32_t layer)
{
    return coli_olmoe_layer_base(layer) + 15u;
}

static inline uint32_t coli_olmoe_router_id(uint32_t layer)
{
    return coli_olmoe_layer_base(layer) + 20u;
}

static inline uint32_t coli_olmoe_expert_gate_id(uint32_t layer, uint32_t expert)
{
    return coli_olmoe_layer_base(layer) + 100u + expert * 10u + 1u;
}

static inline uint32_t coli_olmoe_expert_up_id(uint32_t layer, uint32_t expert)
{
    return coli_olmoe_layer_base(layer) + 100u + expert * 10u + 2u;
}

static inline uint32_t coli_olmoe_expert_down_id(uint32_t layer, uint32_t expert)
{
    return coli_olmoe_layer_base(layer) + 100u + expert * 10u + 3u;
}

static inline uint32_t coli_olmoe_scale_id(uint32_t tensor_id)
{
    return tensor_id + COLI_OLMOE_SCALE_ID_OFFSET;
}

typedef struct {
    coli_q4_stats_t attention_q4;
    coli_moe_stats_t moe;
    size_t peak_workspace_bytes;
} coli_olmoe_layer_stats_t;

typedef struct {
    coli_q4_stats_t embedding_q4;
    coli_q4_stats_t lm_head_q4;
    coli_olmoe_layer_stats_t last_layer;
    uint32_t layers_executed;
    float selected_logit;
    size_t peak_workspace_bytes;
} coli_olmoe_decode_stats_t;

typedef struct {
    coli_olmoe_decode_stats_t last_decode;
    size_t prompt_tokens_consumed;
    size_t generated_tokens;
    bool stopped_on_eos;
} coli_olmoe_generate_stats_t;

typedef coli_status_t (*coli_olmoe_layer_observer_fn)(
    void *context,
    uint32_t layer,
    const coli_moe_stats_t *moe_stats);

typedef coli_status_t (*coli_olmoe_token_fn)(void *context,
                                             uint32_t token_id,
                                             size_t generated_index);

size_t coli_olmoe_layer_required_workspace(size_t hidden_count,
                                           size_t intermediate_count,
                                           size_t expert_count,
                                           size_t top_k,
                                           size_t token_count,
                                           size_t q4_workspace_bytes);

coli_status_t coli_olmoe_layer_decode(const coli_model_t *model,
                                      uint32_t layer,
                                      uint32_t position,
                                      const float *input,
                                      size_t hidden_count,
                                      float *output,
                                      size_t output_count,
                                      coli_kv_cache_t *kv_cache,
                                      void *workspace,
                                      size_t workspace_bytes,
                                      coli_olmoe_layer_stats_t *stats);

coli_status_t coli_olmoe_decode_next_token(
    const coli_model_t *model,
    uint32_t input_token_id,
    uint32_t position,
    coli_kv_cache_t *kv_cache,
    void *workspace,
    size_t workspace_bytes,
    uint32_t *out_token_id,
    coli_olmoe_decode_stats_t *stats);

coli_status_t coli_olmoe_decode_eval_token(
    const coli_model_t *model,
    uint32_t input_token_id,
    uint32_t position,
    coli_kv_cache_t *kv_cache,
    void *workspace,
    size_t workspace_bytes,
    float *out_logits,
    size_t out_logit_count,
    uint32_t *out_token_id,
    coli_olmoe_layer_observer_fn on_layer,
    void *observer_context,
    coli_olmoe_decode_stats_t *stats);

coli_status_t coli_olmoe_generate_greedy(
    const coli_model_t *model,
    const uint32_t *prompt_token_ids,
    size_t prompt_token_count,
    uint32_t *output_token_ids,
    size_t output_token_capacity,
    size_t max_new_tokens,
    size_t *out_output_token_count,
    coli_kv_cache_t *kv_cache,
    void *workspace,
    size_t workspace_bytes,
    coli_olmoe_generate_stats_t *stats);

coli_status_t coli_olmoe_generate_greedy_stream(
    const coli_model_t *model,
    const uint32_t *prompt_token_ids,
    size_t prompt_token_count,
    uint32_t *output_token_ids,
    size_t output_token_capacity,
    size_t max_new_tokens,
    size_t *out_output_token_count,
    coli_kv_cache_t *kv_cache,
    void *workspace,
    size_t workspace_bytes,
    coli_olmoe_token_fn on_token,
    void *token_context,
    coli_olmoe_generate_stats_t *stats);

#ifdef __cplusplus
}
#endif
