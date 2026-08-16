#include "coli_moe.h"

#include <float.h>
#include <math.h>
#include <stdbool.h>
#include <string.h>

typedef struct {
    uint32_t expert;
    float logit;
    float weight;
} route_slot_t;

static size_t align_up_size(size_t value, size_t alignment)
{
    return (value + alignment - 1u) / alignment * alignment;
}

static bool add_size(size_t left, size_t right, size_t *out)
{
    if (left > SIZE_MAX - right) return false;
    *out = left + right;
    return true;
}

static bool mul_size(size_t left, size_t right, size_t *out)
{
    if (left && right > SIZE_MAX / left) return false;
    *out = left * right;
    return true;
}

static bool carve(void **cursor, size_t *remaining, size_t bytes, void **out)
{
    bytes = align_up_size(bytes, sizeof(float));
    if (*remaining < bytes) return false;
    *out = *cursor;
    *cursor = (uint8_t *)*cursor + bytes;
    *remaining -= bytes;
    return true;
}

static float silu(float x)
{
    return x / (1.0f + expf(-x));
}

static void accumulate_q4_stats(coli_q4_stats_t *total,
                                const coli_q4_stats_t *delta)
{
    total->weight_bytes_read += delta->weight_bytes_read;
    total->scale_bytes_read += delta->scale_bytes_read;
    total->storage_reads += delta->storage_reads;
    total->page_boundary_crossings += delta->page_boundary_crossings;
    if (total->peak_workspace_bytes < delta->peak_workspace_bytes)
        total->peak_workspace_bytes = delta->peak_workspace_bytes;
}

static void record_q4_call(coli_moe_stats_t *stats, const coli_q4_stats_t *delta,
                           size_t fixed_workspace_bytes)
{
    accumulate_q4_stats(&stats->q4, delta);
    ++stats->q4_calls;
    size_t total_peak = fixed_workspace_bytes + delta->peak_workspace_bytes;
    if (stats->peak_workspace_bytes < total_peak)
        stats->peak_workspace_bytes = total_peak;
}

static const bmoq_tensor_t *find_tensor(const coli_model_t *model,
                                        uint32_t tensor_id)
{
    return coli_model_find(model, tensor_id);
}

typedef struct {
    bmoq_tensor_t views[6];
    const bmoq_tensor_t *gate_weights;
    const bmoq_tensor_t *gate_scales;
    const bmoq_tensor_t *up_weights;
    const bmoq_tensor_t *up_scales;
    const bmoq_tensor_t *down_weights;
    const bmoq_tensor_t *down_scales;
} resolved_expert_t;

static coli_status_t resolve_expert(const coli_model_t *model,
                                    const coli_moe_config_t *config,
                                    uint32_t expert,
                                    resolved_expert_t *resolved)
{
    if (!model || !config || !resolved || expert >= config->expert_count)
        return COLI_ERR_ARGUMENT;
    memset(resolved, 0, sizeof(*resolved));
    const coli_moe_expert_t *ids = config->experts_bundled
                                       ? &config->expert_bundles
                                       : &config->experts[expert];
    const bmoq_tensor_t *gate_weights = find_tensor(model, ids->gate.weight_id);
    const bmoq_tensor_t *gate_scales = find_tensor(model, ids->gate.scale_id);
    const bmoq_tensor_t *up_weights = find_tensor(model, ids->up.weight_id);
    const bmoq_tensor_t *up_scales = find_tensor(model, ids->up.scale_id);
    const bmoq_tensor_t *down_weights = find_tensor(model, ids->down.weight_id);
    const bmoq_tensor_t *down_scales = find_tensor(model, ids->down.scale_id);
    if (!gate_weights || !gate_scales || !up_weights || !up_scales ||
        !down_weights || !down_scales)
        return COLI_ERR_NOT_FOUND;
    if (config->experts_bundled) {
        coli_status_t status = coli_model_q4_expert_view(
            gate_weights, gate_scales, expert, &resolved->views[0],
            &resolved->views[1]);
        if (status != COLI_OK) return status;
        status = coli_model_q4_expert_view(
            up_weights, up_scales, expert, &resolved->views[2],
            &resolved->views[3]);
        if (status != COLI_OK) return status;
        status = coli_model_q4_expert_view(
            down_weights, down_scales, expert, &resolved->views[4],
            &resolved->views[5]);
        if (status != COLI_OK) return status;
        resolved->gate_weights = &resolved->views[0];
        resolved->gate_scales = &resolved->views[1];
        resolved->up_weights = &resolved->views[2];
        resolved->up_scales = &resolved->views[3];
        resolved->down_weights = &resolved->views[4];
        resolved->down_scales = &resolved->views[5];
    } else {
        resolved->gate_weights = gate_weights;
        resolved->gate_scales = gate_scales;
        resolved->up_weights = up_weights;
        resolved->up_scales = up_scales;
        resolved->down_weights = down_weights;
        resolved->down_scales = down_scales;
    }
    return COLI_OK;
}

static bool route_precedes(const route_slot_t *left, const route_slot_t *right)
{
    if (left->logit > right->logit) return true;
    if (left->logit < right->logit) return false;
    return left->expert < right->expert;
}

static coli_status_t select_routes(const float *logits, const float *bias,
                                   size_t expert_count, size_t top_k,
                                   bool norm_topk_prob, bool sigmoid_router,
                                   float routed_scale, route_slot_t *top)
{
    if (expert_count == 0 || top_k == 0 || top_k > COLI_MOE_MAX_TOP_K ||
        top_k > expert_count)
        return COLI_ERR_RANGE;

    float max_logit = -FLT_MAX;
    float denominator = 1.0f;
    if (!sigmoid_router) {
        for (size_t expert = 0; expert < expert_count; ++expert)
            if (logits[expert] > max_logit) max_logit = logits[expert];
        denominator = 0.0f;
        for (size_t expert = 0; expert < expert_count; ++expert)
            denominator += expf(logits[expert] - max_logit);
        if (!(denominator > 0.0f)) return COLI_ERR_RANGE;
    }

    for (size_t slot = 0; slot < top_k; ++slot) {
        top[slot].expert = UINT32_MAX;
        top[slot].logit = -FLT_MAX;
        top[slot].weight = 0.0f;
    }

    for (size_t expert = 0; expert < expert_count; ++expert) {
        const float weight = sigmoid_router
                                 ? 1.0f / (1.0f + expf(-logits[expert]))
                                 : expf(logits[expert] - max_logit) / denominator;
        route_slot_t candidate = {
            .expert = (uint32_t)expert,
            .logit = weight + (bias ? bias[expert] : 0.0f),
            .weight = weight,
        };
        for (size_t slot = 0; slot < top_k; ++slot) {
            if (route_precedes(&candidate, &top[slot])) {
                for (size_t move = top_k - 1u; move > slot; --move)
                    top[move] = top[move - 1u];
                top[slot] = candidate;
                break;
            }
        }
    }

    if (norm_topk_prob) {
        float selected_sum = 0.0f;
        for (size_t slot = 0; slot < top_k; ++slot)
            selected_sum += top[slot].weight;
        if (!(selected_sum > 0.0f)) return COLI_ERR_RANGE;
        for (size_t slot = 0; slot < top_k; ++slot)
            top[slot].weight /= selected_sum;
    }
    if (!(routed_scale > 0.0f)) routed_scale = 1.0f;
    for (size_t slot = 0; slot < top_k; ++slot)
        top[slot].weight *= routed_scale;
    return COLI_OK;
}

size_t coli_moe_required_workspace(size_t hidden_count,
                                   size_t intermediate_count,
                                   size_t expert_count,
                                   size_t top_k,
                                   size_t q4_workspace_bytes)
{
    if (top_k == 0 || top_k > COLI_MOE_MAX_TOP_K) return 0;
    size_t bytes = 0;
    size_t part = 0;
    if (!mul_size(expert_count, sizeof(float), &part) ||
        !add_size(bytes, align_up_size(part, sizeof(float)), &bytes))
        return 0;
    if (!add_size(bytes, align_up_size(part, sizeof(float)), &bytes))
        return 0;
    if (!mul_size(top_k, sizeof(route_slot_t), &part) ||
        !add_size(bytes, align_up_size(part, sizeof(float)), &bytes))
        return 0;
    if (!mul_size(intermediate_count, sizeof(float), &part) ||
        !add_size(bytes, align_up_size(part, sizeof(float)), &bytes) ||
        !add_size(bytes, align_up_size(part, sizeof(float)), &bytes))
        return 0;
    if (!mul_size(hidden_count, sizeof(float), &part) ||
        !add_size(bytes, align_up_size(part, sizeof(float)), &bytes))
        return 0;
    if (!add_size(bytes, align_up_size(q4_workspace_bytes, sizeof(float)), &bytes))
        return 0;
    return bytes;
}

coli_status_t coli_moe_forward(const coli_model_t *model,
                               const coli_moe_config_t *config,
                               const float *input,
                               size_t hidden_count,
                               float *output,
                               size_t output_count,
                               void *workspace,
                               size_t workspace_bytes,
                               coli_moe_stats_t *stats)
{
    if (!model || !config || (!config->experts && !config->experts_bundled) ||
        !input || !output ||
        !workspace || !stats || config->expert_count == 0 ||
        config->top_k == 0 || config->top_k > COLI_MOE_MAX_TOP_K ||
        config->top_k > config->expert_count || output_count != hidden_count ||
        config->expert_count > UINT32_MAX)
        return COLI_ERR_ARGUMENT;

    const bmoq_tensor_t *router_weights =
        find_tensor(model, config->router.weight_id);
    const bmoq_tensor_t *router_scales =
        find_tensor(model, config->router.scale_id);
    if (!router_weights || !router_scales ||
        router_weights->dimensions[0] != config->expert_count ||
        router_weights->dimensions[1] != hidden_count)
        return COLI_ERR_ARGUMENT;

    size_t intermediate_count = 0;
    for (size_t expert = 0; expert < config->expert_count; ++expert) {
        resolved_expert_t resolved;
        coli_status_t status =
            resolve_expert(model, config, (uint32_t)expert, &resolved);
        if (status != COLI_OK) return status;
        const bmoq_tensor_t *gate_weights = resolved.gate_weights;
        const bmoq_tensor_t *up_weights = resolved.up_weights;
        const bmoq_tensor_t *down_weights = resolved.down_weights;
        if (gate_weights->dimensions[1] != hidden_count ||
            up_weights->dimensions[1] != hidden_count ||
            down_weights->dimensions[0] != hidden_count ||
            gate_weights->dimensions[0] != up_weights->dimensions[0] ||
            down_weights->dimensions[1] != gate_weights->dimensions[0])
            return COLI_ERR_ARGUMENT;
        if (expert == 0)
            intermediate_count = gate_weights->dimensions[0];
        else if (intermediate_count != gate_weights->dimensions[0])
            return COLI_ERR_ARGUMENT;
    }
    if (intermediate_count == 0) return COLI_ERR_ARGUMENT;

    memset(stats, 0, sizeof(*stats));
    void *cursor = workspace;
    size_t remaining = workspace_bytes;
    float *router_logits = NULL;
    float *router_bias = NULL;
    route_slot_t *top = NULL;
    float *gate = NULL;
    float *up = NULL;
    float *down = NULL;
    if (!carve(&cursor, &remaining, config->expert_count * sizeof(float),
               (void **)&router_logits) ||
        !carve(&cursor, &remaining, config->expert_count * sizeof(float),
               (void **)&router_bias) ||
        !carve(&cursor, &remaining, config->top_k * sizeof(route_slot_t),
               (void **)&top) ||
        !carve(&cursor, &remaining, intermediate_count * sizeof(float),
               (void **)&gate) ||
        !carve(&cursor, &remaining, intermediate_count * sizeof(float),
               (void **)&up) ||
        !carve(&cursor, &remaining, hidden_count * sizeof(float),
               (void **)&down))
        return COLI_ERR_RANGE;

    memset(router_bias, 0, config->expert_count * sizeof(*router_bias));
    if (config->router_bias_id != 0) {
        const bmoq_tensor_t *bias =
            find_tensor(model, config->router_bias_id);
        if (!bias || bias->dtype != BMOQ_DTYPE_F32 ||
            bias->layout != BMOQ_LAYOUT_DENSE_F32 ||
            bias->dimensions[0] != config->expert_count ||
            bias->dimensions[1] != 1 || bias->dimensions[2] != 1 ||
            bias->dimensions[3] != 1)
            return COLI_ERR_FORMAT;
        coli_status_t bias_status = coli_tensor_read(
            model, bias, 0, router_bias,
            config->expert_count * sizeof(*router_bias));
        if (bias_status != COLI_OK) return bias_status;
    }

    size_t used = workspace_bytes - remaining;
    void *q4_workspace = cursor;
    stats->peak_workspace_bytes = used;

    coli_q4_stats_t q4_stats;
    coli_status_t status =
        coli_q4_matvec(model, router_weights, router_scales, input, hidden_count,
                       router_logits, config->expert_count, q4_workspace,
                       remaining, &q4_stats);
    if (status != COLI_OK) return status;
    record_q4_call(stats, &q4_stats, used);

    status = select_routes(router_logits, router_bias, config->expert_count,
                           config->top_k, config->norm_topk_prob,
                           config->sigmoid_router, config->routed_scale, top);
    if (status != COLI_OK) return status;

    memset(output, 0, hidden_count * sizeof(*output));
    for (size_t slot = 0; slot < config->top_k; ++slot) {
        const uint32_t expert_index = top[slot].expert;
        resolved_expert_t resolved;
        status = resolve_expert(model, config, expert_index, &resolved);
        if (status != COLI_OK) return status;
        const bmoq_tensor_t *gate_weights = resolved.gate_weights;
        const bmoq_tensor_t *gate_scales = resolved.gate_scales;
        const bmoq_tensor_t *up_weights = resolved.up_weights;
        const bmoq_tensor_t *up_scales = resolved.up_scales;
        const bmoq_tensor_t *down_weights = resolved.down_weights;
        const bmoq_tensor_t *down_scales = resolved.down_scales;

        status = coli_q4_matvec(model, gate_weights, gate_scales, input,
                                hidden_count, gate, intermediate_count,
                                q4_workspace, remaining, &q4_stats);
        if (status != COLI_OK) return status;
        record_q4_call(stats, &q4_stats, used);

        status = coli_q4_matvec(model, up_weights, up_scales, input,
                                hidden_count, up, intermediate_count,
                                q4_workspace, remaining, &q4_stats);
        if (status != COLI_OK) return status;
        record_q4_call(stats, &q4_stats, used);

        for (size_t i = 0; i < intermediate_count; ++i)
            up[i] *= silu(gate[i]);

        status = coli_q4_matvec(model, down_weights, down_scales, up,
                                intermediate_count, down, hidden_count,
                                q4_workspace, remaining, &q4_stats);
        if (status != COLI_OK) return status;
        record_q4_call(stats, &q4_stats, used);

        stats->selected_experts[slot] = expert_index;
        stats->routing_weights[slot] = top[slot].weight;
        ++stats->selected_count;
        for (size_t i = 0; i < hidden_count; ++i)
            output[i] += top[slot].weight * down[i];
    }
    return COLI_OK;
}
