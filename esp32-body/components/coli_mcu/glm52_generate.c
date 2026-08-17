#include "coli_glm52.h"

#include <stdbool.h>
#include <string.h>

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

static bool carve_floats(void **cursor, size_t *remaining, size_t count,
                         float **out)
{
    size_t bytes;
    if (!mul_size(count, sizeof(float), &bytes) || *remaining < bytes)
        return false;
    *out = *cursor;
    *cursor = (uint8_t *)*cursor + bytes;
    *remaining -= bytes;
    return true;
}

static bool dense_f32_compatible(const bmoq_tensor_t *tensor, size_t count)
{
    return tensor && tensor->dtype == BMOQ_DTYPE_F32 &&
           tensor->layout == BMOQ_LAYOUT_DENSE_F32 &&
           tensor->dimensions[0] == count && tensor->dimensions[1] == 1 &&
           tensor->dimensions[2] == 1 && tensor->dimensions[3] == 1 &&
           count <= SIZE_MAX / sizeof(float) &&
           tensor->byte_length == count * sizeof(float);
}

static coli_status_t read_dense_f32(const coli_model_t *model,
                                    uint32_t tensor_id, float *output,
                                    size_t count)
{
    const bmoq_tensor_t *tensor = coli_model_find(model, tensor_id);
    if (!dense_f32_compatible(tensor, count)) return COLI_ERR_FORMAT;
    return coli_tensor_read(model, tensor, 0, output, count * sizeof(float));
}

static bool is_stop_token(const coli_glm52_config_t *config, uint32_t token)
{
    for (size_t i = 0; i < config->stop_token_count; ++i)
        if (config->stop_token_ids[i] == token) return true;
    return false;
}

size_t coli_glm52_decode_required_workspace(
    const coli_glm52_config_t *config, uint32_t token_count,
    size_t q4_workspace_bytes)
{
    if (!config || token_count == 0 || token_count > config->max_context_tokens)
        return 0;
    size_t layer = 0;
    if (config->first_dense_layers != 0) {
        layer = coli_glm52_dense_layer_required_workspace(
            config, token_count, q4_workspace_bytes);
        if (layer == 0) return 0;
    }
    if (config->first_dense_layers < config->num_layers) {
        size_t sparse = coli_glm52_sparse_layer_required_workspace(
            config, token_count, q4_workspace_bytes);
        if (sparse == 0) return 0;
        if (layer < sparse) layer = sparse;
    }
    size_t state_bytes;
    return layer != 0 && mul_size(config->hidden_size, 2u * sizeof(float),
                                  &state_bytes) &&
                   add_size(state_bytes, layer, &state_bytes)
               ? state_bytes
               : 0;
}

coli_status_t coli_glm52_decode_next_token(
    const coli_model_t *model, const coli_glm52_config_t *config,
    uint32_t input_token_id, uint32_t position, coli_kv_cache_t *state,
    void *workspace, size_t workspace_bytes, uint32_t *out_token_id,
    coli_glm52_decode_stats_t *stats)
{
    if (!model || !config || !state || !workspace || !out_token_id || !stats ||
        input_token_id >= config->vocab_size ||
        position >= config->max_context_tokens)
        return COLI_ERR_ARGUMENT;
    memset(stats, 0, sizeof(*stats));
    void *cursor = workspace;
    size_t remaining = workspace_bytes;
    float *current;
    float *next;
    if (!carve_floats(&cursor, &remaining, config->hidden_size, &current) ||
        !carve_floats(&cursor, &remaining, config->hidden_size, &next))
        return COLI_ERR_RANGE;

    const bmoq_tensor_t *embed =
        coli_model_find(model, COLI_GLM52_TENSOR_EMBED_TOKENS);
    const bmoq_tensor_t *embed_scales = coli_model_find(
        model, coli_glm52_scale_id(COLI_GLM52_TENSOR_EMBED_TOKENS));
    coli_status_t status = coli_q4_dequantize_row(
        model, embed, embed_scales, input_token_id, current,
        config->hidden_size, cursor, remaining, &stats->embedding_q4);
    if (status != COLI_OK) return status;

    for (uint32_t layer = 0; layer < config->num_layers; ++layer) {
        coli_glm52_layer_stats_t layer_stats;
        if (layer < config->first_dense_layers)
            status = coli_glm52_dense_layer_decode(
                model, config, layer, position, current, config->hidden_size,
                next, config->hidden_size, state, cursor, remaining,
                &layer_stats);
        else
            status = coli_glm52_sparse_layer_decode(
                model, config, layer, position, current, config->hidden_size,
                next, config->hidden_size, state, cursor, remaining,
                &layer_stats);
        if (status != COLI_OK) return status;
        stats->last_layer = layer_stats;
        ++stats->layers_executed;
        float *swap = current;
        current = next;
        next = swap;
    }

    status = read_dense_f32(model, COLI_GLM52_TENSOR_FINAL_NORM, next,
                            config->hidden_size);
    if (status != COLI_OK) return status;
    status = coli_ops_rmsnorm(current, next, current, config->hidden_size,
                              config->rms_norm_epsilon);
    if (status != COLI_OK) return status;
    const bmoq_tensor_t *lm_head =
        coli_model_find(model, COLI_GLM52_TENSOR_LM_HEAD);
    const bmoq_tensor_t *lm_scales = coli_model_find(
        model, coli_glm52_scale_id(COLI_GLM52_TENSOR_LM_HEAD));
    status = coli_q4_argmax(
        model, lm_head, lm_scales, current, config->hidden_size, cursor,
        remaining, out_token_id, &stats->selected_logit, &stats->lm_head_q4);
    if (status != COLI_OK) return status;

    size_t fixed = workspace_bytes - remaining;
    stats->peak_workspace_bytes = fixed + stats->embedding_q4.peak_workspace_bytes;
    size_t peak = fixed + stats->lm_head_q4.peak_workspace_bytes;
    if (stats->peak_workspace_bytes < peak) stats->peak_workspace_bytes = peak;
    peak = fixed + stats->last_layer.peak_workspace_bytes;
    if (stats->peak_workspace_bytes < peak) stats->peak_workspace_bytes = peak;
    return COLI_OK;
}

coli_status_t coli_glm52_generate_greedy_stream(
    const coli_model_t *model, const coli_glm52_config_t *config,
    const uint32_t *prompt_token_ids, size_t prompt_token_count,
    uint32_t *output_token_ids, size_t output_token_capacity,
    size_t max_new_tokens, size_t *out_output_token_count,
    coli_kv_cache_t *state, void *workspace, size_t workspace_bytes,
    coli_glm52_token_fn on_token, void *token_context,
    coli_glm52_generate_stats_t *stats)
{
    if (!model || !config || !prompt_token_ids || prompt_token_count == 0 ||
        !output_token_ids || !out_output_token_count || !state || !workspace ||
        !stats || prompt_token_count > SIZE_MAX / sizeof(*output_token_ids) ||
        output_token_capacity < prompt_token_count ||
        max_new_tokens > output_token_capacity - prompt_token_count ||
        prompt_token_count + max_new_tokens > config->max_context_tokens)
        return COLI_ERR_ARGUMENT;
    memset(stats, 0, sizeof(*stats));
    memcpy(output_token_ids, prompt_token_ids,
           prompt_token_count * sizeof(*output_token_ids));
    *out_output_token_count = prompt_token_count;

    uint32_t next_token = prompt_token_ids[0];
    for (size_t i = 0; i < prompt_token_count; ++i) {
        coli_status_t status = coli_glm52_decode_next_token(
            model, config, prompt_token_ids[i], (uint32_t)i, state, workspace,
            workspace_bytes, &next_token, &stats->last_decode);
        if (status != COLI_OK) return status;
        ++stats->prompt_tokens_consumed;
    }
    for (size_t generated = 0; generated < max_new_tokens; ++generated) {
        if (is_stop_token(config, next_token)) {
            stats->stopped_on_eos = true;
            return COLI_OK;
        }
        output_token_ids[(*out_output_token_count)++] = next_token;
        ++stats->generated_tokens;
        if (on_token) {
            coli_status_t status =
                on_token(token_context, next_token, generated);
            if (status != COLI_OK) return status;
        }
        coli_status_t status = coli_glm52_decode_next_token(
            model, config, next_token,
            (uint32_t)(prompt_token_count + generated), state, workspace,
            workspace_bytes, &next_token, &stats->last_decode);
        if (status != COLI_OK) return status;
    }
    return COLI_OK;
}

coli_status_t coli_glm52_generate_greedy(
    const coli_model_t *model, const coli_glm52_config_t *config,
    const uint32_t *prompt_token_ids, size_t prompt_token_count,
    uint32_t *output_token_ids, size_t output_token_capacity,
    size_t max_new_tokens, size_t *out_output_token_count,
    coli_kv_cache_t *state, void *workspace, size_t workspace_bytes,
    coli_glm52_generate_stats_t *stats)
{
    return coli_glm52_generate_greedy_stream(
        model, config, prompt_token_ids, prompt_token_count, output_token_ids,
        output_token_capacity, max_new_tokens, out_output_token_count, state,
        workspace, workspace_bytes, NULL, NULL, stats);
}
