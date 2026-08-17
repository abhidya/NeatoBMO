#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "coli_model.h"
#include "coli_q4.h"

#ifdef __cplusplus
extern "C" {
#endif

#define COLI_MOE_MAX_TOP_K 8u

typedef struct {
    uint32_t weight_id;
    uint32_t scale_id;
} coli_q4_tensor_ids_t;

typedef struct {
    coli_q4_tensor_ids_t gate;
    coli_q4_tensor_ids_t up;
    coli_q4_tensor_ids_t down;
} coli_moe_expert_t;

typedef struct {
    coli_q4_tensor_ids_t router;
    /** Optional dense-f32 correction bias used for expert selection. */
    uint32_t router_bias_id;
    const coli_moe_expert_t *experts;
    /** One Q4 expert bundle per projection when experts_bundled is true. */
    coli_moe_expert_t expert_bundles;
    size_t expert_count;
    size_t top_k;
    bool norm_topk_prob;
    bool experts_bundled;
    /** Use sigmoid gate mass while ranking by sigmoid(logit) + bias. */
    bool sigmoid_router;
    /** Multiplier applied after optional selected-weight normalization. */
    float routed_scale;
} coli_moe_config_t;

typedef struct {
    uint32_t selected_experts[COLI_MOE_MAX_TOP_K];
    float routing_weights[COLI_MOE_MAX_TOP_K];
    size_t selected_count;
    size_t peak_workspace_bytes;
    uint32_t q4_calls;
    coli_q4_stats_t q4;
} coli_moe_stats_t;

size_t coli_moe_required_workspace(size_t hidden_count,
                                   size_t intermediate_count,
                                   size_t expert_count,
                                   size_t top_k,
                                   size_t q4_workspace_bytes);

coli_status_t coli_moe_forward(const coli_model_t *model,
                               const coli_moe_config_t *config,
                               const float *input,
                               size_t hidden_count,
                               float *output,
                               size_t output_count,
                               void *workspace,
                               size_t workspace_bytes,
                               coli_moe_stats_t *stats);

#ifdef __cplusplus
}
#endif
