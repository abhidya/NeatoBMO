#include "coli_gemma.h"

#include <float.h>
#include <math.h>
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

/* GeGLU: Gemma gates with the tanh approximation of GELU, not SiLU. */
static void gemma_gelu_gated(const float *gate, const float *up, float *output,
                             size_t count)
{
    for (size_t i = 0; i < count; ++i) {
        float x = gate[i];
        float inner = 0.7978845608028654f * (x + 0.044715f * x * x * x);
        float gelu = 0.5f * x * (1.0f + tanhf(inner));
        output[i] = gelu * up[i];
    }
}

static coli_status_t gemma_rmsnorm(const coli_model_t *model,
                                   uint32_t tensor_id,
                                   const float *input,
                                   float *output,
                                   size_t count,
                                   float *weight_workspace)
{
    coli_status_t status =
        read_dense_f32(model, tensor_id, weight_workspace, count);
    if (status != COLI_OK) return status;
    double sum_squares = 0.0;
    for (size_t i = 0; i < count; ++i)
        sum_squares += (double)input[i] * (double)input[i];
    const float scale =
        1.0f / sqrtf((float)(sum_squares / (double)count) + 1.0e-6f);
    /* Plain multiply, not (1 + w): llama.cpp's GGUF converter folds Gemma's
     * +1 into every *norm.weight tensor at conversion time, and the exporter
     * copies those tensors verbatim. Adding 1 again computes (2 + w_hf) in
     * every norm — the first sub-step where the runtime diverged from the
     * reference on the real checkpoint. */
    for (size_t i = 0; i < count; ++i)
        output[i] = input[i] * scale * weight_workspace[i];
    return COLI_OK;
}

static coli_status_t q4_project(const coli_model_t *model, uint32_t tensor_id,
                                const float *input, size_t input_count,
                                float *output, size_t output_count,
                                void *workspace, size_t workspace_bytes,
                                coli_q4_stats_t *stats)
{
    const bmoq_tensor_t *weights = coli_model_find(model, tensor_id);
    const bmoq_tensor_t *scales =
        coli_model_find(model, coli_gemma_scale_id(tensor_id));
    if (!weights || !scales) return COLI_ERR_ARGUMENT;
    return coli_q4_matvec(model, weights, scales, input, input_count, output,
                          output_count, workspace, workspace_bytes, stats);
}

static coli_status_t apply_head_norms(const coli_model_t *model,
                                      uint32_t tensor_id,
                                      float *heads,
                                      uint32_t head_count,
                                      uint32_t head_dim,
                                      float *weight_workspace)
{
    for (uint32_t head = 0; head < head_count; ++head) {
        float *head_values = heads + (size_t)head * head_dim;
        coli_status_t status =
            gemma_rmsnorm(model, tensor_id, head_values, head_values, head_dim,
                          weight_workspace);
        if (status != COLI_OK) return status;
    }
    return COLI_OK;
}

static coli_status_t rope_apply_gqa(float *query, float *key,
                                    uint32_t query_heads,
                                    uint32_t kv_heads,
                                    uint32_t head_dim,
                                    uint32_t position,
                                    float theta_base)
{
    if (!query || !key || query_heads == 0 || kv_heads == 0 ||
        head_dim == 0 || (head_dim & 1u) != 0 || !(theta_base > 1.0f))
        return COLI_ERR_ARGUMENT;
    /* Half-split ("rotate_half"/NEOX) rotation: dimension d pairs with
     * d + head_dim/2, matching how Hugging Face and llama.cpp apply RoPE to
     * Gemma weights. The exporter copies projection rows verbatim, so the
     * interleaved (d, d+1) pairing would rotate the wrong coordinate pairs
     * and scramble every attention head. */
    const uint32_t half = head_dim / 2u;
    for (uint32_t head = 0; head < query_heads; ++head) {
        float *q_head = query + (size_t)head * head_dim;
        for (uint32_t d = 0; d < half; ++d) {
            const float exponent = (float)d / (float)half;
            const float angle = (float)position / powf(theta_base, exponent);
            const float cosine = cosf(angle);
            const float sine = sinf(angle);
            const float q0 = q_head[d];
            const float q1 = q_head[d + half];
            q_head[d] = q0 * cosine - q1 * sine;
            q_head[d + half] = q0 * sine + q1 * cosine;
        }
    }
    for (uint32_t head = 0; head < kv_heads; ++head) {
        float *k_head = key + (size_t)head * head_dim;
        for (uint32_t d = 0; d < half; ++d) {
            const float exponent = (float)d / (float)half;
            const float angle = (float)position / powf(theta_base, exponent);
            const float cosine = cosf(angle);
            const float sine = sinf(angle);
            const float k0 = k_head[d];
            const float k1 = k_head[d + half];
            k_head[d] = k0 * cosine - k1 * sine;
            k_head[d + half] = k0 * sine + k1 * cosine;
        }
    }
    return COLI_OK;
}

static coli_status_t attention_decode_gqa(const float *query,
                                          const float *keys,
                                          const float *values,
                                          uint32_t token_count,
                                          uint32_t query_heads,
                                          uint32_t kv_heads,
                                          uint32_t head_dim,
                                          uint32_t sliding_window,
                                          float *output,
                                          float *score_workspace,
                                          size_t score_count)
{
    if (!query || !keys || !values || !output || !score_workspace ||
        token_count == 0 || query_heads == 0 || kv_heads == 0 ||
        query_heads % kv_heads != 0 || head_dim == 0 ||
        score_count < token_count)
        return COLI_ERR_ARGUMENT;

    const uint32_t first_token =
        (sliding_window > 0 && token_count > sliding_window)
            ? token_count - sliding_window
            : 0;
    const float inv_sqrt_dim = 1.0f / sqrtf((float)head_dim);
    const size_t kv_token_values = (size_t)kv_heads * head_dim;
    memset(output, 0, (size_t)query_heads * head_dim * sizeof(*output));
    for (uint32_t head = 0; head < query_heads; ++head) {
        const uint32_t kv_head = head / (query_heads / kv_heads);
        const float *q_head = query + (size_t)head * head_dim;
        float max_score = -FLT_MAX;
        for (uint32_t token = first_token; token < token_count; ++token) {
            const float *k_head = keys + (size_t)token * kv_token_values +
                                  (size_t)kv_head * head_dim;
            float score = 0.0f;
            for (uint32_t d = 0; d < head_dim; ++d)
                score += q_head[d] * k_head[d];
            score *= inv_sqrt_dim;
            score_workspace[token] = score;
            if (score > max_score) max_score = score;
        }

        float sum_exp = 0.0f;
        for (uint32_t token = first_token; token < token_count; ++token) {
            const float value = expf(score_workspace[token] - max_score);
            score_workspace[token] = value;
            sum_exp += value;
        }
        if (!(sum_exp > 0.0f)) return COLI_ERR_RANGE;

        float *out_head = output + (size_t)head * head_dim;
        for (uint32_t token = first_token; token < token_count; ++token) {
            const float weight = score_workspace[token] / sum_exp;
            const float *v_head = values + (size_t)token * kv_token_values +
                                  (size_t)kv_head * head_dim;
            for (uint32_t d = 0; d < head_dim; ++d)
                out_head[d] += weight * v_head[d];
        }
    }
    return COLI_OK;
}

size_t coli_gemma_layer_required_workspace(size_t hidden_count,
                                           size_t attention_count,
                                           size_t kv_count,
                                           size_t intermediate_count,
                                           size_t token_count,
                                           size_t q4_workspace_bytes)
{
    if (hidden_count == 0 || attention_count == 0 || kv_count == 0 ||
        intermediate_count == 0 || token_count == 0)
        return 0;
    size_t total = 0;
    size_t bytes;
    if (!mul_size(hidden_count, sizeof(float), &bytes)) return 0;
    for (size_t i = 0; i < 5; ++i)
        if (!add_aligned(&total, bytes)) return 0;
    if (!mul_size(attention_count, sizeof(float), &bytes)) return 0;
    for (size_t i = 0; i < 2; ++i)
        if (!add_aligned(&total, bytes)) return 0;
    if (!mul_size(kv_count, sizeof(float), &bytes)) return 0;
    for (size_t i = 0; i < 2; ++i)
        if (!add_aligned(&total, bytes)) return 0;
    if (!mul_size(intermediate_count, sizeof(float), &bytes)) return 0;
    for (size_t i = 0; i < 2; ++i)
        if (!add_aligned(&total, bytes)) return 0;
    if (!mul_size(token_count, sizeof(float), &bytes) ||
        !add_aligned(&total, bytes))
        return 0;
    if (!add_aligned(&total, q4_workspace_bytes)) return 0;
    return total;
}

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
                                      coli_gemma_layer_stats_t *stats)
{
    if (!model || !input || !output || !kv_cache || !kv_layout || !workspace ||
        !stats || hidden_count == 0 || output_count != hidden_count ||
        hidden_count != model->config.hidden_size ||
        layer >= model->config.num_hidden_layers ||
        position >= kv_layout->max_tokens ||
        kv_layout->layers < model->config.num_hidden_layers ||
        kv_layout->heads != model->config.num_key_value_heads ||
        kv_layout->head_dim == 0 ||
        model->config.num_attention_heads == 0 ||
        model->config.num_attention_heads % model->config.num_key_value_heads !=
            0)
        return COLI_ERR_ARGUMENT;

    memset(stats, 0, sizeof(*stats));
    const uint32_t query_heads = model->config.num_attention_heads;
    const uint32_t kv_heads = model->config.num_key_value_heads;
    const uint32_t head_dim = kv_layout->head_dim;
    const uint32_t attention_count = query_heads * head_dim;
    const uint32_t kv_count = kv_heads * head_dim;
    /* Gemma 3 interleaves attention kinds in a fixed published pattern:
     * every 6th layer is full/global attention with rope_theta from the
     * checkpoint metadata (1e6), the rest are sliding-window layers with the
     * local base frequency of 1e4. One theta for all layers rotates five of
     * every six layers with the wrong angles. */
    const bool global_layer = (layer + 1u) % 6u == 0u;
    const float rope_theta =
        global_layer ? (float)model->config.rope_theta : 10000.0f;
    const uint32_t sliding_window =
        global_layer ? 0u : model->config.num_experts;
    const size_t intermediate_count = model->config.intermediate_size;

    void *cursor = workspace;
    size_t remaining = workspace_bytes;
    float *norm = NULL;
    float *post_norm = NULL;
    float *projected = NULL;
    float *ffn = NULL;
    float *weight = NULL;
    float *query = NULL;
    float *attention = NULL;
    float *key = NULL;
    float *value = NULL;
    float *gate = NULL;
    float *up = NULL;
    float *scores = NULL;
    if (!carve(&cursor, &remaining, hidden_count * sizeof(float),
               (void **)&norm) ||
        !carve(&cursor, &remaining, hidden_count * sizeof(float),
               (void **)&post_norm) ||
        !carve(&cursor, &remaining, hidden_count * sizeof(float),
               (void **)&projected) ||
        !carve(&cursor, &remaining, hidden_count * sizeof(float),
               (void **)&ffn) ||
        !carve(&cursor, &remaining, hidden_count * sizeof(float),
               (void **)&weight) ||
        !carve(&cursor, &remaining, (size_t)attention_count * sizeof(float),
               (void **)&query) ||
        !carve(&cursor, &remaining, (size_t)attention_count * sizeof(float),
               (void **)&attention) ||
        !carve(&cursor, &remaining, (size_t)kv_count * sizeof(float),
               (void **)&key) ||
        !carve(&cursor, &remaining, (size_t)kv_count * sizeof(float),
               (void **)&value) ||
        !carve(&cursor, &remaining, intermediate_count * sizeof(float),
               (void **)&gate) ||
        !carve(&cursor, &remaining, intermediate_count * sizeof(float),
               (void **)&up) ||
        !carve(&cursor, &remaining, (size_t)(position + 1u) * sizeof(float),
               (void **)&scores))
        return COLI_ERR_RANGE;

    void *q4_workspace = cursor;
    size_t q4_workspace_bytes = remaining;
    coli_status_t status =
        gemma_rmsnorm(model, coli_gemma_attn_norm_id(layer), input, norm,
                      hidden_count, weight);
    if (status != COLI_OK) return status;

    coli_q4_stats_t q4_stats;
    status = q4_project(model, coli_gemma_q_proj_id(layer), norm, hidden_count,
                        query, attention_count, q4_workspace,
                        q4_workspace_bytes, &q4_stats);
    if (status != COLI_OK) return status;
    accumulate_q4(&stats->attention_q4, &q4_stats);
    status = q4_project(model, coli_gemma_k_proj_id(layer), norm, hidden_count,
                        key, kv_count, q4_workspace, q4_workspace_bytes,
                        &q4_stats);
    if (status != COLI_OK) return status;
    accumulate_q4(&stats->attention_q4, &q4_stats);
    status = q4_project(model, coli_gemma_v_proj_id(layer), norm, hidden_count,
                        value, kv_count, q4_workspace, q4_workspace_bytes,
                        &q4_stats);
    if (status != COLI_OK) return status;
    accumulate_q4(&stats->attention_q4, &q4_stats);

    status = apply_head_norms(model, coli_gemma_q_norm_id(layer), query,
                              query_heads, head_dim, weight);
    if (status != COLI_OK) return status;
    status = apply_head_norms(model, coli_gemma_k_norm_id(layer), key, kv_heads,
                              head_dim, weight);
    if (status != COLI_OK) return status;
    status = rope_apply_gqa(query, key, query_heads, kv_heads, head_dim,
                            position, rope_theta);
    if (status != COLI_OK) return status;

    void *key_slot = NULL;
    void *value_slot = NULL;
    status = coli_ops_kv_cache_token_ptrs(kv_cache, kv_cache_bytes, kv_layout,
                                          layer, position, &key_slot,
                                          &value_slot);
    if (status != COLI_OK) return status;
    memcpy(key_slot, key, (size_t)kv_count * sizeof(float));
    memcpy(value_slot, value, (size_t)kv_count * sizeof(float));

    void *layer_key0 = NULL;
    void *layer_value0 = NULL;
    status = coli_ops_kv_cache_token_ptrs(kv_cache, kv_cache_bytes, kv_layout,
                                          layer, 0, &layer_key0, &layer_value0);
    if (status != COLI_OK) return status;
    status = attention_decode_gqa(query, layer_key0, layer_value0,
                                  position + 1u, query_heads, kv_heads,
                                  head_dim, sliding_window, attention, scores,
                                  position + 1u);
    if (status != COLI_OK) return status;

    status = q4_project(model, coli_gemma_o_proj_id(layer), attention,
                        attention_count, projected, hidden_count, q4_workspace,
                        q4_workspace_bytes, &q4_stats);
    if (status != COLI_OK) return status;
    accumulate_q4(&stats->attention_q4, &q4_stats);
    status = gemma_rmsnorm(model, coli_gemma_post_attention_norm_id(layer),
                           projected, projected, hidden_count, weight);
    if (status != COLI_OK) return status;
    status = coli_ops_residual_add(input, projected, output, hidden_count);
    if (status != COLI_OK) return status;

    status = gemma_rmsnorm(model, coli_gemma_ffn_norm_id(layer), output,
                           post_norm, hidden_count, weight);
    if (status != COLI_OK) return status;
    status = q4_project(model, coli_gemma_ffn_gate_id(layer), post_norm,
                        hidden_count, gate, intermediate_count, q4_workspace,
                        q4_workspace_bytes, &q4_stats);
    if (status != COLI_OK) return status;
    accumulate_q4(&stats->ffn_q4, &q4_stats);
    status = q4_project(model, coli_gemma_ffn_up_id(layer), post_norm,
                        hidden_count, up, intermediate_count, q4_workspace,
                        q4_workspace_bytes, &q4_stats);
    if (status != COLI_OK) return status;
    accumulate_q4(&stats->ffn_q4, &q4_stats);
    gemma_gelu_gated(gate, up, gate, intermediate_count);
    status = COLI_OK;
    if (status != COLI_OK) return status;
    status = q4_project(model, coli_gemma_ffn_down_id(layer), gate,
                        intermediate_count, ffn, hidden_count, q4_workspace,
                        q4_workspace_bytes, &q4_stats);
    if (status != COLI_OK) return status;
    accumulate_q4(&stats->ffn_q4, &q4_stats);
    status = gemma_rmsnorm(model, coli_gemma_post_ffw_norm_id(layer), ffn, ffn,
                           hidden_count, weight);
    if (status != COLI_OK) return status;
    status = coli_ops_residual_add(output, ffn, output, hidden_count);
    if (status != COLI_OK) return status;

    stats->peak_workspace_bytes = workspace_bytes - remaining;
    if (stats->peak_workspace_bytes < stats->attention_q4.peak_workspace_bytes)
        stats->peak_workspace_bytes = stats->attention_q4.peak_workspace_bytes;
    if (stats->peak_workspace_bytes < stats->ffn_q4.peak_workspace_bytes)
        stats->peak_workspace_bytes = stats->ffn_q4.peak_workspace_bytes;
    return COLI_OK;
}

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
    coli_gemma_decode_stats_t *stats)
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
    if (!carve(&cursor, &remaining, hidden_count * sizeof(float),
               (void **)&state) ||
        !carve(&cursor, &remaining, hidden_count * sizeof(float),
               (void **)&next))
        return COLI_ERR_RANGE;

    const bmoq_tensor_t *embed =
        coli_model_find(model, COLI_GEMMA_TENSOR_EMBED_TOKENS);
    const bmoq_tensor_t *embed_scales =
        coli_model_find(model, coli_gemma_scale_id(COLI_GEMMA_TENSOR_EMBED_TOKENS));
    coli_status_t status =
        coli_q4_dequantize_row(model, embed, embed_scales, input_token_id,
                               state, hidden_count, cursor, remaining,
                               &stats->embedding_q4);
    if (status != COLI_OK) return status;

    /* Gemma scales token embeddings by sqrt(hidden_size) before the first
     * layer (the HF "normalizer"). Omitting it feeds every layer activations
     * ~25x too small on the 270M checkpoint and yields garbage text; the
     * synthetic fixtures never caught it because their reference implemented
     * the same omission. */
    const float embedding_scale = sqrtf((float)hidden_count);
    for (size_t i = 0; i < hidden_count; ++i) state[i] *= embedding_scale;

    for (uint32_t layer = 0; layer < model->config.num_hidden_layers; ++layer) {
        coli_gemma_layer_stats_t layer_stats;
        status = coli_gemma_layer_decode(model, layer, position, state,
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

    status = gemma_rmsnorm(model, COLI_GEMMA_TENSOR_FINAL_NORM, state, state,
                           hidden_count, next);
    if (status != COLI_OK) return status;

    const bmoq_tensor_t *lm_head =
        coli_model_find(model, COLI_GEMMA_TENSOR_LM_HEAD);
    const bmoq_tensor_t *lm_scales =
        coli_model_find(model, coli_gemma_scale_id(COLI_GEMMA_TENSOR_LM_HEAD));
    if (!lm_head || !lm_scales) {
        lm_head = embed;
        lm_scales = embed_scales;
    }
    status = coli_q4_argmax(model, lm_head, lm_scales, state, hidden_count,
                            cursor, remaining, out_token_id,
                            &stats->selected_logit, &stats->lm_head_q4);
    if (status != COLI_OK) return status;

    stats->peak_workspace_bytes = workspace_bytes - remaining;
    if (stats->peak_workspace_bytes < stats->last_layer.peak_workspace_bytes)
        stats->peak_workspace_bytes = stats->last_layer.peak_workspace_bytes;
    return COLI_OK;
}

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
    coli_gemma_generate_stats_t *stats)
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
        coli_status_t status = coli_gemma_decode_next_token(
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

        coli_status_t status = coli_gemma_decode_next_token(
            model, next_token, (uint32_t)(prompt_token_count + generated),
            kv_cache, kv_cache_bytes, kv_layout, workspace, workspace_bytes,
            &next_token, &stats->last_decode);
        if (status != COLI_OK) return status;
    }
    return COLI_OK;
}
