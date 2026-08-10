#pragma once

#include <stddef.h>
#include <stdint.h>

#include "coli_moe.h"
#include "coli_model.h"
#include "coli_ops.h"

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
                                      uint8_t *kv_cache,
                                      size_t kv_cache_bytes,
                                      const coli_kv_cache_layout_t *kv_layout,
                                      void *workspace,
                                      size_t workspace_bytes,
                                      coli_olmoe_layer_stats_t *stats);

#ifdef __cplusplus
}
#endif
