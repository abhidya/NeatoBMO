#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "coli_model.h"
#include "coli_ops.h"
#include "coli_q4.h"

#ifdef __cplusplus
extern "C" {
#endif

#define COLI_GEMMA3_ARCH_ID 2u
#define COLI_GEMMA3_MANIFEST_TENSOR_ID 0x47454d33u
#define COLI_GEMMA_SCALE_ID_OFFSET 0x00800000u

typedef enum {
    COLI_GEMMA_TENSOR_EMBED_TOKENS = 1u,
    COLI_GEMMA_TENSOR_FINAL_NORM = 2u,
    COLI_GEMMA_TENSOR_LM_HEAD = 3u,
} coli_gemma_global_tensor_id_t;

static inline uint32_t coli_gemma_layer_base(uint32_t layer)
{
    return 2000u + layer * 100u;
}

static inline uint32_t coli_gemma_attn_norm_id(uint32_t layer)
{
    return coli_gemma_layer_base(layer) + 1u;
}

static inline uint32_t coli_gemma_post_attention_norm_id(uint32_t layer)
{
    return coli_gemma_layer_base(layer) + 2u;
}

static inline uint32_t coli_gemma_ffn_norm_id(uint32_t layer)
{
    return coli_gemma_layer_base(layer) + 3u;
}

static inline uint32_t coli_gemma_post_ffw_norm_id(uint32_t layer)
{
    return coli_gemma_layer_base(layer) + 4u;
}

static inline uint32_t coli_gemma_q_norm_id(uint32_t layer)
{
    return coli_gemma_layer_base(layer) + 5u;
}

static inline uint32_t coli_gemma_k_norm_id(uint32_t layer)
{
    return coli_gemma_layer_base(layer) + 6u;
}

static inline uint32_t coli_gemma_q_proj_id(uint32_t layer)
{
    return coli_gemma_layer_base(layer) + 10u;
}

static inline uint32_t coli_gemma_k_proj_id(uint32_t layer)
{
    return coli_gemma_layer_base(layer) + 11u;
}

static inline uint32_t coli_gemma_v_proj_id(uint32_t layer)
{
    return coli_gemma_layer_base(layer) + 12u;
}

static inline uint32_t coli_gemma_o_proj_id(uint32_t layer)
{
    return coli_gemma_layer_base(layer) + 13u;
}

static inline uint32_t coli_gemma_ffn_gate_id(uint32_t layer)
{
    return coli_gemma_layer_base(layer) + 20u;
}

static inline uint32_t coli_gemma_ffn_up_id(uint32_t layer)
{
    return coli_gemma_layer_base(layer) + 21u;
}

static inline uint32_t coli_gemma_ffn_down_id(uint32_t layer)
{
    return coli_gemma_layer_base(layer) + 22u;
}

static inline uint32_t coli_gemma_scale_id(uint32_t tensor_id)
{
    return tensor_id + COLI_GEMMA_SCALE_ID_OFFSET;
}

typedef struct {
    coli_q4_stats_t attention_q4;
    coli_q4_stats_t ffn_q4;
    size_t peak_workspace_bytes;
} coli_gemma_layer_stats_t;

typedef struct {
    coli_q4_stats_t embedding_q4;
    coli_q4_stats_t lm_head_q4;
    coli_gemma_layer_stats_t last_layer;
    uint32_t layers_executed;
    float selected_logit;
    size_t peak_workspace_bytes;
} coli_gemma_decode_stats_t;

typedef struct {
    coli_gemma_decode_stats_t last_decode;
    size_t prompt_tokens_consumed;
    size_t generated_tokens;
    bool stopped_on_eos;
} coli_gemma_generate_stats_t;

size_t coli_gemma_layer_required_workspace(size_t hidden_count,
                                           size_t attention_count,
                                           size_t kv_count,
                                           size_t intermediate_count,
                                           size_t token_count,
                                           size_t q4_workspace_bytes);

coli_status_t coli_gemma_layer_decode(const coli_model_t *model,
                                      uint32_t layer,
                                      uint32_t position,
                                      const float *input,
                                      size_t hidden_count,
                                      float *output,
                                      size_t output_count,
                                      uint8_t *kv_cache,
                                      size_t kv_cache_bytes,
                                      const coli_kv_cache_layout_t *kv_layout,
                                      void *workspace,
                                      size_t workspace_bytes,
                                      coli_gemma_layer_stats_t *stats);

coli_status_t coli_gemma_decode_next_token(
    const coli_model_t *model,
    uint32_t input_token_id,
    uint32_t position,
    uint8_t *kv_cache,
    size_t kv_cache_bytes,
    const coli_kv_cache_layout_t *kv_layout,
    void *workspace,
    size_t workspace_bytes,
    uint32_t *out_token_id,
    coli_gemma_decode_stats_t *stats);

coli_status_t coli_gemma_generate_greedy(
    const coli_model_t *model,
    const uint32_t *prompt_token_ids,
    size_t prompt_token_count,
    uint32_t *output_token_ids,
    size_t output_token_capacity,
    size_t max_new_tokens,
    size_t *out_output_token_count,
    uint8_t *kv_cache,
    size_t kv_cache_bytes,
    const coli_kv_cache_layout_t *kv_layout,
    void *workspace,
    size_t workspace_bytes,
    coli_gemma_generate_stats_t *stats);

#ifdef __cplusplus
}
#endif
