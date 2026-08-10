#include "coli_olmoe.h"

#include <stdbool.h>
#include <string.h>

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
    if (left != 0 && right > SIZE_MAX / left) return false;
    *out = left * right;
    return true;
}

static bool add_aligned(size_t *total, size_t bytes)
{
    return add_size(*total, align_up_size(bytes, sizeof(float)), total);
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

static void accumulate_q4(coli_q4_stats_t *total, const coli_q4_stats_t *delta)
{
    total->weight_bytes_read += delta->weight_bytes_read;
    total->scale_bytes_read += delta->scale_bytes_read;
    total->storage_reads += delta->storage_reads;
    total->page_boundary_crossings += delta->page_boundary_crossings;
    if (total->peak_workspace_bytes < delta->peak_workspace_bytes)
        total->peak_workspace_bytes = delta->peak_workspace_bytes;
}

static bool dense_f32_compatible(const bmoq_tensor_t *tensor, size_t count)
{
    if (!tensor || tensor->dtype != BMOQ_DTYPE_F32 ||
        tensor->layout != BMOQ_LAYOUT_DENSE_F32 ||
        tensor->byte_length != count * sizeof(float))
        return false;
    uint64_t elements = 1;
    for (size_t i = 0; i < 4; ++i) {
        uint32_t dimension = tensor->dimensions[i] ? tensor->dimensions[i] : 1u;
        if (elements > UINT64_MAX / dimension) return false;
        elements *= dimension;
    }
    return elements == count;
}

static coli_status_t read_dense_f32(const coli_model_t *model, uint32_t tensor_id,
                                    float *output, size_t count)
{
    const bmoq_tensor_t *tensor = coli_model_find(model, tensor_id);
    if (!dense_f32_compatible(tensor, count)) return COLI_ERR_ARGUMENT;
    return coli_tensor_read(model, tensor, 0, output, count * sizeof(float));
}

static coli_status_t q4_project(const coli_model_t *model, uint32_t tensor_id,
                                const float *input, size_t input_count,
                                float *output, size_t output_count,
                                void *workspace, size_t workspace_bytes,
                                coli_q4_stats_t *stats)
{
    const bmoq_tensor_t *weights = coli_model_find(model, tensor_id);
    const bmoq_tensor_t *scales =
        coli_model_find(model, coli_olmoe_scale_id(tensor_id));
    if (!weights || !scales) return COLI_ERR_ARGUMENT;
    return coli_q4_matvec(model, weights, scales, input, input_count, output,
                          output_count, workspace, workspace_bytes, stats);
}

size_t coli_olmoe_layer_required_workspace(size_t hidden_count,
                                           size_t intermediate_count,
                                           size_t expert_count,
                                           size_t top_k,
                                           size_t token_count,
                                           size_t q4_workspace_bytes)
{
    if (hidden_count == 0 || token_count == 0) return 0;
    size_t float_bytes;
    size_t total = 0;
    if (!mul_size(hidden_count, sizeof(float), &float_bytes)) return 0;
    for (size_t i = 0; i < 7; ++i)
        if (!add_aligned(&total, float_bytes)) return 0;
    size_t score_bytes;
    if (!mul_size(token_count, sizeof(float), &score_bytes) ||
        !add_aligned(&total, score_bytes))
        return 0;
    size_t moe_bytes = coli_moe_required_workspace(
        hidden_count, intermediate_count, expert_count, top_k, q4_workspace_bytes);
    if (moe_bytes == 0 || !add_aligned(&total, moe_bytes)) return 0;
    if (!add_aligned(&total, q4_workspace_bytes)) return 0;
    return total;
}

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
                                      coli_olmoe_layer_stats_t *stats)
{
    if (!model || !input || !output || !kv_cache || !kv_layout || !workspace ||
        !stats || hidden_count == 0 || output_count != hidden_count ||
        layer >= model->config.num_hidden_layers ||
        position >= kv_layout->max_tokens ||
        kv_layout->layers < model->config.num_hidden_layers ||
        kv_layout->heads != model->config.num_attention_heads ||
        kv_layout->head_dim == 0 ||
        hidden_count !=
            (size_t)model->config.num_attention_heads * kv_layout->head_dim ||
        model->config.num_experts == 0 ||
        model->config.num_experts > COLI_OLMOE_EXPECTED_EXPERTS ||
        model->config.num_experts_per_tok == 0 ||
        model->config.num_experts_per_tok > COLI_MOE_MAX_TOP_K)
        return COLI_ERR_ARGUMENT;

    memset(stats, 0, sizeof(*stats));
    void *cursor = workspace;
    size_t remaining = workspace_bytes;
    float *norm = NULL;
    float *query = NULL;
    float *key = NULL;
    float *value = NULL;
    float *attention = NULL;
    float *projected = NULL;
    float *post_norm = NULL;
    float *scores = NULL;
    const size_t hidden_bytes = hidden_count * sizeof(float);
    if (!carve(&cursor, &remaining, hidden_bytes, (void **)&norm) ||
        !carve(&cursor, &remaining, hidden_bytes, (void **)&query) ||
        !carve(&cursor, &remaining, hidden_bytes, (void **)&key) ||
        !carve(&cursor, &remaining, hidden_bytes, (void **)&value) ||
        !carve(&cursor, &remaining, hidden_bytes, (void **)&attention) ||
        !carve(&cursor, &remaining, hidden_bytes, (void **)&projected) ||
        !carve(&cursor, &remaining, hidden_bytes, (void **)&post_norm) ||
        !carve(&cursor, &remaining, (size_t)(position + 1u) * sizeof(float),
               (void **)&scores))
        return COLI_ERR_RANGE;

    void *q4_workspace = cursor;
    size_t q4_workspace_bytes = remaining;
    float *norm_weight = projected;
    coli_status_t status =
        read_dense_f32(model, coli_olmoe_input_norm_id(layer), norm_weight,
                       hidden_count);
    if (status != COLI_OK) return status;
    status = coli_ops_rmsnorm(input, norm_weight, norm, hidden_count, 1.0e-5f);
    if (status != COLI_OK) return status;

    coli_q4_stats_t q4_stats;
    status = q4_project(model, coli_olmoe_q_proj_id(layer), norm, hidden_count,
                        query, hidden_count, q4_workspace, q4_workspace_bytes,
                        &q4_stats);
    if (status != COLI_OK) return status;
    accumulate_q4(&stats->attention_q4, &q4_stats);
    status = q4_project(model, coli_olmoe_k_proj_id(layer), norm, hidden_count,
                        key, hidden_count, q4_workspace, q4_workspace_bytes,
                        &q4_stats);
    if (status != COLI_OK) return status;
    accumulate_q4(&stats->attention_q4, &q4_stats);
    status = q4_project(model, coli_olmoe_v_proj_id(layer), norm, hidden_count,
                        value, hidden_count, q4_workspace, q4_workspace_bytes,
                        &q4_stats);
    if (status != COLI_OK) return status;
    accumulate_q4(&stats->attention_q4, &q4_stats);

    status = coli_ops_rope_apply(query, key, kv_layout->heads,
                                 kv_layout->head_dim, position,
                                 (float)model->config.rope_theta);
    if (status != COLI_OK) return status;

    void *key_slot = NULL;
    void *value_slot = NULL;
    status = coli_ops_kv_cache_token_ptrs(kv_cache, kv_cache_bytes, kv_layout,
                                          layer, position, &key_slot,
                                          &value_slot);
    if (status != COLI_OK) return status;
    memcpy(key_slot, key, hidden_bytes);
    memcpy(value_slot, value, hidden_bytes);

    void *layer_key0 = NULL;
    void *layer_value0 = NULL;
    status = coli_ops_kv_cache_token_ptrs(kv_cache, kv_cache_bytes, kv_layout,
                                          layer, 0, &layer_key0, &layer_value0);
    if (status != COLI_OK) return status;
    status = coli_ops_attention_decode(query, layer_key0, layer_value0,
                                       position + 1u, kv_layout->heads,
                                       kv_layout->head_dim, attention, scores,
                                       position + 1u);
    if (status != COLI_OK) return status;

    status = q4_project(model, coli_olmoe_o_proj_id(layer), attention,
                        hidden_count, projected, hidden_count, q4_workspace,
                        q4_workspace_bytes, &q4_stats);
    if (status != COLI_OK) return status;
    accumulate_q4(&stats->attention_q4, &q4_stats);
    status = coli_ops_residual_add(input, projected, output, hidden_count);
    if (status != COLI_OK) return status;

    status =
        read_dense_f32(model, coli_olmoe_post_attention_norm_id(layer),
                       norm_weight, hidden_count);
    if (status != COLI_OK) return status;
    status = coli_ops_rmsnorm(output, norm_weight, post_norm, hidden_count,
                              1.0e-5f);
    if (status != COLI_OK) return status;

    coli_moe_expert_t experts[COLI_OLMOE_EXPECTED_EXPERTS];
    for (uint32_t expert = 0; expert < model->config.num_experts; ++expert) {
        uint32_t gate = coli_olmoe_expert_gate_id(layer, expert);
        uint32_t up = coli_olmoe_expert_up_id(layer, expert);
        uint32_t down = coli_olmoe_expert_down_id(layer, expert);
        experts[expert].gate.weight_id = gate;
        experts[expert].gate.scale_id = coli_olmoe_scale_id(gate);
        experts[expert].up.weight_id = up;
        experts[expert].up.scale_id = coli_olmoe_scale_id(up);
        experts[expert].down.weight_id = down;
        experts[expert].down.scale_id = coli_olmoe_scale_id(down);
    }
    coli_moe_config_t moe_config = {
        .router =
            {
                .weight_id = coli_olmoe_router_id(layer),
                .scale_id = coli_olmoe_scale_id(coli_olmoe_router_id(layer)),
            },
        .experts = experts,
        .expert_count = model->config.num_experts,
        .top_k = model->config.num_experts_per_tok,
        .norm_topk_prob = false,
    };
    status = coli_moe_forward(model, &moe_config, post_norm, hidden_count,
                              projected, hidden_count, cursor, remaining,
                              &stats->moe);
    if (status != COLI_OK) return status;
    status = coli_ops_residual_add(output, projected, output, hidden_count);
    if (status != COLI_OK) return status;

    stats->peak_workspace_bytes = workspace_bytes - remaining;
    if (stats->peak_workspace_bytes < stats->moe.peak_workspace_bytes)
        stats->peak_workspace_bytes = stats->moe.peak_workspace_bytes;
    return COLI_OK;
}

coli_status_t coli_olmoe_decode_next_token(
    const coli_model_t *model,
    uint32_t input_token_id,
    uint32_t position,
    uint8_t *kv_cache,
    size_t kv_cache_bytes,
    const coli_kv_cache_layout_t *kv_layout,
    void *workspace,
    size_t workspace_bytes,
    uint32_t *out_token_id,
    coli_olmoe_decode_stats_t *stats)
{
    if (!model || !kv_cache || !kv_layout || !workspace || !out_token_id ||
        !stats || model->config.hidden_size == 0 ||
        input_token_id >= model->config.vocab_size ||
        position >= kv_layout->max_tokens)
        return COLI_ERR_ARGUMENT;

    memset(stats, 0, sizeof(*stats));
    void *cursor = workspace;
    size_t remaining = workspace_bytes;
    float *state = NULL;
    float *next = NULL;
    const size_t hidden_count = model->config.hidden_size;
    const size_t hidden_bytes = hidden_count * sizeof(float);
    if (!carve(&cursor, &remaining, hidden_bytes, (void **)&state) ||
        !carve(&cursor, &remaining, hidden_bytes, (void **)&next))
        return COLI_ERR_RANGE;

    const bmoq_tensor_t *embed =
        coli_model_find(model, COLI_OLMOE_TENSOR_EMBED_TOKENS);
    const bmoq_tensor_t *embed_scales = coli_model_find(
        model, coli_olmoe_scale_id(COLI_OLMOE_TENSOR_EMBED_TOKENS));
    coli_status_t status =
        coli_q4_dequantize_row(model, embed, embed_scales, input_token_id,
                               state, hidden_count, cursor, remaining,
                               &stats->embedding_q4);
    if (status != COLI_OK) return status;

    for (uint32_t layer = 0; layer < model->config.num_hidden_layers; ++layer) {
        coli_olmoe_layer_stats_t layer_stats;
        status = coli_olmoe_layer_decode(model, layer, position, state,
                                         hidden_count, next, hidden_count,
                                         kv_cache, kv_cache_bytes, kv_layout,
                                         cursor, remaining, &layer_stats);
        if (status != COLI_OK) return status;
        stats->last_layer = layer_stats;
        ++stats->layers_executed;
        float *swap = state;
        state = next;
        next = swap;
    }

    status = read_dense_f32(model, COLI_OLMOE_TENSOR_FINAL_NORM, next,
                            hidden_count);
    if (status != COLI_OK) return status;
    status = coli_ops_rmsnorm(state, next, state, hidden_count, 1.0e-5f);
    if (status != COLI_OK) return status;

    const bmoq_tensor_t *lm_head =
        coli_model_find(model, COLI_OLMOE_TENSOR_LM_HEAD);
    const bmoq_tensor_t *lm_scales =
        coli_model_find(model, coli_olmoe_scale_id(COLI_OLMOE_TENSOR_LM_HEAD));
    status = coli_q4_argmax(model, lm_head, lm_scales, state, hidden_count,
                            cursor, remaining, out_token_id,
                            &stats->selected_logit, &stats->lm_head_q4);
    if (status != COLI_OK) return status;

    stats->peak_workspace_bytes = workspace_bytes - remaining;
    if (stats->peak_workspace_bytes < stats->last_layer.peak_workspace_bytes)
        stats->peak_workspace_bytes = stats->last_layer.peak_workspace_bytes;
    return COLI_OK;
}

coli_status_t coli_olmoe_generate_greedy(
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
    coli_olmoe_generate_stats_t *stats)
{
    if (!model || !prompt_token_ids || prompt_token_count == 0 ||
        !output_token_ids || !out_output_token_count || !kv_cache ||
        !kv_layout || !workspace || !stats ||
        output_token_capacity < prompt_token_count ||
        max_new_tokens > output_token_capacity - prompt_token_count ||
        prompt_token_count + max_new_tokens > kv_layout->max_tokens)
        return COLI_ERR_ARGUMENT;

    memset(stats, 0, sizeof(*stats));
    for (size_t i = 0; i < prompt_token_count; ++i)
        output_token_ids[i] = prompt_token_ids[i];
    *out_output_token_count = prompt_token_count;

    uint32_t next_token = prompt_token_ids[0];
    for (size_t i = 0; i < prompt_token_count; ++i) {
        coli_status_t status = coli_olmoe_decode_next_token(
            model, prompt_token_ids[i], (uint32_t)i, kv_cache, kv_cache_bytes,
            kv_layout, workspace, workspace_bytes, &next_token,
            &stats->last_decode);
        if (status != COLI_OK) return status;
        ++stats->prompt_tokens_consumed;
    }

    for (size_t generated = 0; generated < max_new_tokens; ++generated) {
        if (next_token == model->config.eos_token_id) {
            stats->stopped_on_eos = true;
            return COLI_OK;
        }
        output_token_ids[*out_output_token_count] = next_token;
        ++*out_output_token_count;
        ++stats->generated_tokens;

        coli_status_t status = coli_olmoe_decode_next_token(
            model, next_token, (uint32_t)(prompt_token_count + generated),
            kv_cache, kv_cache_bytes, kv_layout, workspace, workspace_bytes,
            &next_token, &stats->last_decode);
        if (status != COLI_OK) return status;
    }
    return COLI_OK;
}
