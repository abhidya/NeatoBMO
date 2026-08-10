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
    const coli_moe_expert_t *experts;
    size_t expert_count;
    size_t top_k;
    bool norm_topk_prob;
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
