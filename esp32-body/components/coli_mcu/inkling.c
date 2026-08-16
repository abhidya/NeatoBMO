#include "coli_inkling.h"

#include "coli_ops.h"

#include <float.h>
#include <math.h>
#include <stdbool.h>
#include <string.h>

static coli_status_t read_u32_required(const coli_model_t *model, uint32_t key,
                                       uint32_t *value)
{
    size_t count = 0;
    coli_status_t status = coli_model_config_read(
        model, key, BMOQ_CONFIG_U32, value, sizeof(*value), &count);
    if (status != COLI_OK) return status;
    return count == 1 ? COLI_OK : COLI_ERR_FORMAT;
}

static coli_status_t read_f32_required(const coli_model_t *model, uint32_t key,
                                       float *value)
{
    size_t count = 0;
    coli_status_t status = coli_model_config_read(
        model, key, BMOQ_CONFIG_F32, value, sizeof(*value), &count);
    if (status != COLI_OK) return status;
    return count == 1 ? COLI_OK : COLI_ERR_FORMAT;
}

static coli_status_t read_u32_array_required(const coli_model_t *model,
                                             uint32_t key, uint32_t *values,
                                             size_t values_bytes,
                                             size_t *out_count)
{
    coli_status_t status = coli_model_config_read(
        model, key, BMOQ_CONFIG_U32_ARRAY, values, values_bytes, out_count);
    if (status != COLI_OK) return status;
    return *out_count != 0 ? COLI_OK : COLI_ERR_FORMAT;
}

static uint32_t max_u32(uint32_t left, uint32_t right)
{
    return left > right ? left : right;
}

static float sigmoidf_bounded(float x)
{
    if (x >= 0.0f) {
        const float z = expf(-x);
        return 1.0f / (1.0f + z);
    }
    const float z = expf(x);
    return z / (1.0f + z);
}

static bool mul_size(size_t left, size_t right, size_t *out)
{
    if (!out || (left != 0 && right > SIZE_MAX / left)) return false;
    *out = left * right;
    return true;
}

coli_status_t coli_inkling_config_load(const coli_model_t *model,
                                       coli_inkling_config_t *out_config)
{
    if (!model || !out_config) return COLI_ERR_ARGUMENT;
    if (model->format_version != BMOQ_VERSION_EXTENDED_CONFIG ||
        model->config.arch != BMOQ_MODEL_ARCH_INKLING)
        return COLI_ERR_UNSUPPORTED;

    coli_inkling_config_t config;
    memset(&config, 0, sizeof(config));
    config.hidden_size = model->config.hidden_size;
    config.num_layers = model->config.num_hidden_layers;
    config.vocab_size = model->config.vocab_size;
    config.num_heads = model->config.num_attention_heads;
    config.num_key_value_heads = model->config.num_key_value_heads;
    config.num_experts = model->config.num_experts;
    config.experts_per_token = model->config.num_experts_per_tok;
    config.dense_intermediate_size = model->config.intermediate_size;
    config.max_context_tokens = model->config.max_position_embeddings;

    coli_status_t status = read_u32_required(
        model, BMOQ_CONFIG_UNPADDED_VOCAB_SIZE, &config.unpadded_vocab_size);
    if (status != COLI_OK) return status;
    status = read_u32_required(model, BMOQ_CONFIG_HEAD_DIM, &config.head_dim);
    if (status != COLI_OK) return status;
    status = read_u32_required(model, BMOQ_CONFIG_SWA_NUM_ATTENTION_HEADS,
                               &config.swa_num_heads);
    if (status != COLI_OK) return status;
    status = read_u32_required(model, BMOQ_CONFIG_SWA_NUM_KEY_VALUE_HEADS,
                               &config.swa_num_key_value_heads);
    if (status != COLI_OK) return status;
    status = read_u32_required(model, BMOQ_CONFIG_SWA_HEAD_DIM,
                               &config.swa_head_dim);
    if (status != COLI_OK) return status;
    status = read_u32_required(model, BMOQ_CONFIG_SLIDING_WINDOW,
                               &config.sliding_window);
    if (status != COLI_OK) return status;
    status = read_u32_required(model, BMOQ_CONFIG_D_REL, &config.d_rel);
    if (status != COLI_OK) return status;
    status =
        read_u32_required(model, BMOQ_CONFIG_REL_EXTENT, &config.rel_extent);
    if (status != COLI_OK) return status;
    status =
        read_u32_required(model, BMOQ_CONFIG_CONV_KERNEL, &config.conv_kernel);
    if (status != COLI_OK) return status;
    status = read_u32_required(model, BMOQ_CONFIG_MOE_INTERMEDIATE_SIZE,
                               &config.moe_intermediate_size);
    if (status != COLI_OK) return status;
    status = read_u32_required(model, BMOQ_CONFIG_DENSE_INTERMEDIATE_SIZE,
                               &config.dense_intermediate_size);
    if (status != COLI_OK) return status;
    status =
        read_u32_required(model, BMOQ_CONFIG_DENSE_MLP_INDEX,
                          &config.dense_mlp_index);
    if (status != COLI_OK) return status;
    status = read_u32_required(model, BMOQ_CONFIG_SHARED_EXPERTS_INKLING,
                               &config.shared_experts);
    if (status != COLI_OK) return status;
    status = read_f32_required(model, BMOQ_CONFIG_RMS_NORM_EPS,
                               &config.rms_norm_epsilon);
    if (status != COLI_OK) return status;
    status =
        read_f32_required(model, BMOQ_CONFIG_ROUTED_SCALE, &config.route_scale);
    if (status != COLI_OK) return status;
    status = read_f32_required(model,
                               BMOQ_CONFIG_LOGITS_MUP_WIDTH_MULTIPLIER,
                               &config.logits_mup_width_multiplier);
    if (status != COLI_OK) return status;
    uint32_t log_floor = 0;
    status = read_u32_required(model, BMOQ_CONFIG_LOG_SCALING_N_FLOOR,
                               &log_floor);
    if (status != COLI_OK) return status;
    config.log_scaling_n_floor = (float)log_floor;
    status = read_f32_required(model, BMOQ_CONFIG_LOG_SCALING_ALPHA,
                               &config.log_scaling_alpha);
    if (status != COLI_OK) return status;

    size_t stop_count = 0;
    status = coli_model_config_read(
        model, BMOQ_CONFIG_STOP_TOKEN_IDS, BMOQ_CONFIG_U32_ARRAY,
        config.stop_token_ids, sizeof(config.stop_token_ids), &stop_count);
    if (status == COLI_ERR_NOT_FOUND && model->config.eos_token_id != 0) {
        config.stop_token_ids[0] = model->config.eos_token_id;
        stop_count = 1;
        status = COLI_OK;
    }
    if (status != COLI_OK) return status;
    config.stop_token_count = stop_count;
    status = read_u32_array_required(
        model, BMOQ_CONFIG_LOCAL_LAYER_IDS, config.local_layer_ids,
        sizeof(config.local_layer_ids), &config.local_layer_count);
    if (status != COLI_OK) return status;
    status = read_u32_array_required(
        model, BMOQ_CONFIG_SPARSE_LAYER_IDS, config.sparse_layer_ids,
        sizeof(config.sparse_layer_ids), &config.sparse_layer_count);
    if (status != COLI_OK) return status;

    if (config.hidden_size == 0 || config.hidden_size > (1u << 20) ||
        config.num_layers == 0 || config.num_layers > 256 ||
        config.vocab_size == 0 || config.unpadded_vocab_size == 0 ||
        config.unpadded_vocab_size > config.vocab_size ||
        config.max_context_tokens == 0 ||
        config.sliding_window > config.max_context_tokens ||
        config.num_heads == 0 ||
        config.num_key_value_heads == 0 || config.head_dim == 0 ||
        config.num_heads % config.num_key_value_heads != 0 ||
        config.swa_num_heads == 0 || config.swa_num_key_value_heads == 0 ||
        config.swa_head_dim == 0 ||
        config.swa_num_heads % config.swa_num_key_value_heads != 0 ||
        config.sliding_window == 0 || config.d_rel == 0 ||
        config.rel_extent == 0 || config.conv_kernel < 2 ||
        config.conv_kernel > 16 || config.num_experts == 0 ||
        config.experts_per_token == 0 ||
        config.experts_per_token > COLI_INKLING_MAX_TOP_K ||
        config.experts_per_token > config.num_experts ||
        config.shared_experts > COLI_INKLING_MAX_SHARED_EXPERTS ||
        config.moe_intermediate_size == 0 ||
        config.dense_intermediate_size == 0 ||
        config.dense_mlp_index >= config.num_layers ||
        config.stop_token_count > COLI_INKLING_MAX_STOP_TOKENS ||
        config.local_layer_count > config.num_layers ||
        config.sparse_layer_count > config.num_layers ||
        !(config.rms_norm_epsilon > 0.0f) ||
        !(config.route_scale > 0.0f) ||
        !(config.logits_mup_width_multiplier > 0.0f) ||
        config.log_scaling_n_floor < 0.0f)
        return COLI_ERR_FORMAT;

    for (size_t i = 0; i < config.local_layer_count; ++i)
        if (config.local_layer_ids[i] >= config.num_layers)
            return COLI_ERR_FORMAT;
    for (size_t i = 0; i < config.sparse_layer_count; ++i)
        if (config.sparse_layer_ids[i] >= config.num_layers)
            return COLI_ERR_FORMAT;

    *out_config = config;
    return COLI_OK;
}

coli_status_t coli_inkling_state_layout(const coli_inkling_config_t *config,
                                        coli_kv_cache_layout_t *out_layout)
{
    if (!config || !out_layout) return COLI_ERR_ARGUMENT;
    uint32_t kv_heads =
        max_u32(config->num_key_value_heads, config->swa_num_key_value_heads);
    uint32_t head_dim = max_u32(config->head_dim, config->swa_head_dim);
    return coli_kv_cache_layout(config->num_layers, kv_heads, head_dim,
                                config->max_context_tokens, sizeof(float),
                                out_layout);
}

bool coli_inkling_is_stop_token(const coli_inkling_config_t *config,
                                uint32_t token_id)
{
    if (!config) return false;
    for (size_t i = 0; i < config->stop_token_count; ++i)
        if (config->stop_token_ids[i] == token_id) return true;
    return false;
}

float coli_inkling_global_tau(const coli_inkling_config_t *config,
                              uint32_t token_count)
{
    if (!config || !(config->log_scaling_n_floor > 0.0f) ||
        token_count <= (uint32_t)config->log_scaling_n_floor)
        return 1.0f;
    const float ratio = (float)token_count / config->log_scaling_n_floor;
    return 1.0f + config->log_scaling_alpha * logf(ratio);
}

size_t coli_inkling_conv_state_floats(const coli_inkling_config_t *config,
                                      uint32_t channels)
{
    if (!config || channels == 0 || config->conv_kernel < 2) return 0;
    size_t state;
    return mul_size(channels, config->conv_kernel - 1u, &state) ? state : 0;
}

coli_status_t coli_inkling_short_conv_step(const float *input,
                                           const float *weights,
                                           uint32_t channels,
                                           uint32_t kernel,
                                           float *state,
                                           size_t state_count,
                                           float *output,
                                           size_t output_count)
{
    if (!input || !weights || !state || !output || channels == 0 ||
        kernel < 2 || state_count < (size_t)channels * (kernel - 1u) ||
        output_count < channels)
        return COLI_ERR_ARGUMENT;

    for (uint32_t channel = 0; channel < channels; ++channel) {
        float acc = input[channel] * weights[(size_t)channel * kernel];
        float *history = state + (size_t)channel * (kernel - 1u);
        for (uint32_t tap = 1; tap < kernel; ++tap)
            acc += history[tap - 1u] *
                   weights[(size_t)channel * kernel + tap];
        output[channel] = input[channel] + acc;
        for (uint32_t i = kernel - 1u; i > 1u; --i)
            history[i - 1u] = history[i - 2u];
        history[0] = input[channel];
    }
    return COLI_OK;
}

coli_status_t coli_inkling_qk_rmsnorm(float *heads,
                                      const float *weights,
                                      uint32_t head_count,
                                      uint32_t head_dim,
                                      float epsilon)
{
    if (!heads || !weights || head_count == 0 || head_dim == 0 ||
        !(epsilon > 0.0f))
        return COLI_ERR_ARGUMENT;
    for (uint32_t head = 0; head < head_count; ++head) {
        float *head_values = heads + (size_t)head * head_dim;
        coli_status_t status = coli_ops_rmsnorm(
            head_values, weights, head_values, head_dim, epsilon);
        if (status != COLI_OK) return status;
    }
    return COLI_OK;
}

coli_status_t coli_inkling_attention_decode(
    const coli_inkling_config_t *config, coli_kv_cache_t *state,
    uint32_t layer, bool local_layer, uint32_t position, const float *query,
    const float *key, const float *value, const float *relative_bias,
    size_t relative_bias_count, float *output, size_t output_count,
    float *score_workspace, size_t score_count, float *key_scratch,
    size_t key_scratch_count, float *value_scratch, size_t value_scratch_count)
{
    const coli_kv_cache_layout_t *layout = coli_kv_cache_get_layout(state);
    if (!config || !state || !layout || !query || !key || !value || !output ||
        !score_workspace || !key_scratch || !value_scratch ||
        layer >= config->num_layers || position >= config->max_context_tokens)
        return COLI_ERR_ARGUMENT;

    const uint32_t heads =
        local_layer ? config->swa_num_heads : config->num_heads;
    const uint32_t kv_heads = local_layer ? config->swa_num_key_value_heads
                                          : config->num_key_value_heads;
    const uint32_t head_dim =
        local_layer ? config->swa_head_dim : config->head_dim;
    const uint32_t extent =
        local_layer ? config->sliding_window : config->rel_extent;
    const uint32_t kv_stride = layout->key_token_bytes / sizeof(float);
    const size_t kv_values = (size_t)kv_heads * head_dim;
    if (heads == 0 || kv_heads == 0 || heads % kv_heads != 0 ||
        head_dim == 0 || output_count < (size_t)heads * head_dim ||
        score_count < position + 1u || key_scratch_count < kv_stride ||
        value_scratch_count < kv_stride || kv_values > kv_stride ||
        relative_bias_count < extent)
        return COLI_ERR_ARGUMENT;

    memset(key_scratch, 0, kv_stride * sizeof(*key_scratch));
    memset(value_scratch, 0, kv_stride * sizeof(*value_scratch));
    memcpy(key_scratch, key, kv_values * sizeof(*key_scratch));
    memcpy(value_scratch, value, kv_values * sizeof(*value_scratch));
    coli_status_t status = coli_kv_cache_write_token(
        state, layer, position, key_scratch, value_scratch);
    if (status != COLI_OK) return status;

    const uint32_t start =
        local_layer && position + 1u > config->sliding_window
            ? position + 1u - config->sliding_window
            : 0;
    const float scale = 1.0f / (float)head_dim;
    const float tau = local_layer ? 1.0f
                                  : coli_inkling_global_tau(config,
                                                            position + 1u);
    for (uint32_t head = 0; head < heads; ++head) {
        const uint32_t kv_head = head / (heads / kv_heads);
        const float *q_head = query + (size_t)head * head_dim;
        float max_score = -FLT_MAX;
        size_t used = 0;
        for (uint32_t token = start; token <= position; ++token) {
            status = coli_kv_cache_read_key(state, layer, token, key_scratch);
            if (status != COLI_OK) return status;
            const float *k_head = key_scratch + (size_t)kv_head * head_dim;
            float score = 0.0f;
            for (uint32_t d = 0; d < head_dim; ++d)
                score += q_head[d] * k_head[d];
            uint32_t distance = position - token;
            if (distance >= extent) distance = extent - 1u;
            score = (score * scale + relative_bias[distance]) * tau;
            score_workspace[used++] = score;
            if (score > max_score) max_score = score;
        }
        float sum = 0.0f;
        for (size_t i = 0; i < used; ++i) {
            score_workspace[i] = expf(score_workspace[i] - max_score);
            sum += score_workspace[i];
        }
        if (!(sum > 0.0f)) return COLI_ERR_RANGE;
        float *out_head = output + (size_t)head * head_dim;
        memset(out_head, 0, head_dim * sizeof(*out_head));
        used = 0;
        for (uint32_t token = start; token <= position; ++token) {
            status = coli_kv_cache_read_value(state, layer, token,
                                              value_scratch);
            if (status != COLI_OK) return status;
            const float weight = score_workspace[used++] / sum;
            const float *v_head = value_scratch + (size_t)kv_head * head_dim;
            for (uint32_t d = 0; d < head_dim; ++d)
                out_head[d] += weight * v_head[d];
        }
    }
    return COLI_OK;
}

coli_status_t coli_inkling_dense_swiglu(const float *gate, const float *up,
                                        const float *down,
                                        uint32_t hidden_size,
                                        uint32_t intermediate_size,
                                        float global_scale,
                                        float *output,
                                        size_t output_count)
{
    if (!gate || !up || !down || !output || hidden_size == 0 ||
        intermediate_size == 0 || output_count < hidden_size)
        return COLI_ERR_ARGUMENT;
    for (uint32_t h = 0; h < hidden_size; ++h) {
        float acc = 0.0f;
        for (uint32_t i = 0; i < intermediate_size; ++i) {
            const float silu = gate[i] * sigmoidf_bounded(gate[i]);
            acc += silu * up[i] * down[(size_t)h * intermediate_size + i];
        }
        output[h] = acc * global_scale;
    }
    return COLI_OK;
}

coli_status_t coli_inkling_moe_route(const coli_inkling_config_t *config,
                                     const float *router_logits,
                                     const float *router_bias,
                                     float global_scale,
                                     coli_inkling_moe_stats_t *stats)
{
    if (!config || !router_logits || !router_bias || !stats ||
        config->experts_per_token > COLI_INKLING_MAX_TOP_K ||
        config->shared_experts > COLI_INKLING_MAX_SHARED_EXPERTS)
        return COLI_ERR_ARGUMENT;
    memset(stats, 0, sizeof(*stats));
    for (uint32_t pick = 0; pick < config->experts_per_token; ++pick) {
        uint32_t best = UINT32_MAX;
        float best_score = -FLT_MAX;
        for (uint32_t expert = 0; expert < config->num_experts; ++expert) {
            bool already = false;
            for (uint32_t j = 0; j < pick; ++j)
                if (stats->selected_experts[j] == expert) already = true;
            if (already) continue;
            const float score =
                sigmoidf_bounded(router_logits[expert]) + router_bias[expert];
            if (score > best_score) {
                best_score = score;
                best = expert;
            }
        }
        if (best == UINT32_MAX) return COLI_ERR_RANGE;
        stats->selected_experts[pick] = best;
        stats->selected_count++;
    }

    float denom = 0.0f;
    for (size_t i = 0; i < stats->selected_count; ++i)
        denom += sigmoidf_bounded(router_logits[stats->selected_experts[i]]);
    for (uint32_t i = 0; i < config->shared_experts; ++i)
        denom += sigmoidf_bounded(router_logits[config->num_experts + i]);
    if (!(denom > 0.0f)) return COLI_ERR_RANGE;
    const float scale = config->route_scale * global_scale / denom;
    for (size_t i = 0; i < stats->selected_count; ++i)
        stats->routing_weights[i] =
            sigmoidf_bounded(router_logits[stats->selected_experts[i]]) *
            scale;
    stats->shared_count = config->shared_experts;
    for (size_t i = 0; i < stats->shared_count; ++i)
        stats->routing_weights[stats->selected_count + i] =
            sigmoidf_bounded(router_logits[config->num_experts + i]) * scale;
    return COLI_OK;
}

coli_status_t coli_inkling_moe_combine(
    const coli_inkling_config_t *config, const coli_inkling_moe_stats_t *stats,
    const float *expert_outputs, size_t expert_output_count,
    const float *shared_outputs, size_t shared_output_count,
    float *output, size_t output_count)
{
    if (!config || !stats || !expert_outputs || !shared_outputs || !output ||
        output_count < config->hidden_size ||
        expert_output_count < stats->selected_count * config->hidden_size ||
        shared_output_count < stats->shared_count * config->hidden_size)
        return COLI_ERR_ARGUMENT;
    memset(output, 0, config->hidden_size * sizeof(*output));
    for (size_t expert = 0; expert < stats->selected_count; ++expert) {
        const float weight = stats->routing_weights[expert];
        const float *source = expert_outputs + expert * config->hidden_size;
        for (uint32_t h = 0; h < config->hidden_size; ++h)
            output[h] += weight * source[h];
    }
    for (size_t shared = 0; shared < stats->shared_count; ++shared) {
        const float weight =
            stats->routing_weights[stats->selected_count + shared];
        const float *source = shared_outputs + shared * config->hidden_size;
        for (uint32_t h = 0; h < config->hidden_size; ++h)
            output[h] += weight * source[h];
    }
    return COLI_OK;
}

coli_status_t coli_inkling_logits_argmax(const float *hidden,
                                         const float *lm_head,
                                         uint32_t vocab_size,
                                         uint32_t unpadded_vocab_size,
                                         uint32_t hidden_size,
                                         float mup_width_multiplier,
                                         uint32_t *out_token_id,
                                         float *out_logit)
{
    if (!hidden || !lm_head || !out_token_id || !out_logit ||
        unpadded_vocab_size == 0 || unpadded_vocab_size > vocab_size ||
        hidden_size == 0 || !(mup_width_multiplier > 0.0f))
        return COLI_ERR_ARGUMENT;
    uint32_t best = 0;
    float best_logit = -FLT_MAX;
    for (uint32_t token = 0; token < unpadded_vocab_size; ++token) {
        const float *row = lm_head + (size_t)token * hidden_size;
        float logit = 0.0f;
        for (uint32_t h = 0; h < hidden_size; ++h) logit += hidden[h] * row[h];
        logit /= mup_width_multiplier;
        if (logit > best_logit) {
            best_logit = logit;
            best = token;
        }
    }
    *out_token_id = best;
    *out_logit = best_logit;
    return COLI_OK;
}
