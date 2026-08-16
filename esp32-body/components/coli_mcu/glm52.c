#include "coli_glm52.h"

#include <math.h>
#include <float.h>
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

static coli_status_t read_f32_default(const coli_model_t *model, uint32_t key,
                                      float fallback, float *value)
{
    size_t count = 0;
    coli_status_t status = coli_model_config_read(
        model, key, BMOQ_CONFIG_F32, value, sizeof(*value), &count);
    if (status == COLI_ERR_NOT_FOUND) {
        *value = fallback;
        return COLI_OK;
    }
    if (status != COLI_OK) return status;
    return count == 1 ? COLI_OK : COLI_ERR_FORMAT;
}

static coli_status_t read_bool_default(const coli_model_t *model, uint32_t key,
                                       bool fallback, bool *value)
{
    uint32_t encoded = 0;
    size_t count = 0;
    coli_status_t status = coli_model_config_read(
        model, key, BMOQ_CONFIG_BOOL, &encoded, sizeof(encoded), &count);
    if (status == COLI_ERR_NOT_FOUND) {
        *value = fallback;
        return COLI_OK;
    }
    if (status != COLI_OK) return status;
    if (count != 1 || encoded > 1u) return COLI_ERR_FORMAT;
    *value = encoded != 0;
    return COLI_OK;
}

coli_status_t coli_glm52_config_load(const coli_model_t *model,
                                     coli_glm52_config_t *out_config)
{
    if (!model || !out_config) return COLI_ERR_ARGUMENT;
    if (model->format_version != BMOQ_VERSION_EXTENDED_CONFIG ||
        model->config.arch != BMOQ_MODEL_ARCH_GLM52)
        return COLI_ERR_UNSUPPORTED;

    coli_glm52_config_t config;
    memset(&config, 0, sizeof(config));
    config.hidden_size = model->config.hidden_size;
    config.num_layers = model->config.num_hidden_layers;
    config.num_heads = model->config.num_attention_heads;
    config.num_experts = model->config.num_experts;
    config.experts_per_token = model->config.num_experts_per_tok;
    config.dense_intermediate_size = model->config.intermediate_size;
    config.vocab_size = model->config.vocab_size;
    config.max_context_tokens = model->config.max_position_embeddings;
    config.rope_theta = (float)model->config.rope_theta;

    coli_status_t status = read_u32_required(
        model, BMOQ_CONFIG_MOE_INTERMEDIATE_SIZE,
        &config.moe_intermediate_size);
    if (status != COLI_OK) return status;
    status = read_u32_required(model, BMOQ_CONFIG_FIRST_DENSE_LAYERS,
                               &config.first_dense_layers);
    if (status != COLI_OK) return status;
    status = read_u32_required(model, BMOQ_CONFIG_Q_LORA_RANK,
                               &config.q_lora_rank);
    if (status != COLI_OK) return status;
    status = read_u32_required(model, BMOQ_CONFIG_KV_LORA_RANK,
                               &config.kv_lora_rank);
    if (status != COLI_OK) return status;
    status = read_u32_required(model, BMOQ_CONFIG_QK_NOPE_HEAD_DIM,
                               &config.qk_nope_head_dim);
    if (status != COLI_OK) return status;
    status = read_u32_required(model, BMOQ_CONFIG_QK_ROPE_HEAD_DIM,
                               &config.qk_rope_head_dim);
    if (status != COLI_OK) return status;
    status = read_u32_required(model, BMOQ_CONFIG_V_HEAD_DIM,
                               &config.v_head_dim);
    if (status != COLI_OK) return status;
    status = read_u32_required(model, BMOQ_CONFIG_SHARED_EXPERTS,
                               &config.shared_experts);
    if (status != COLI_OK) return status;
    status = read_u32_required(model, BMOQ_CONFIG_EXPERT_GROUPS,
                               &config.expert_groups);
    if (status != COLI_OK) return status;
    status = read_u32_required(model, BMOQ_CONFIG_TOPK_GROUPS,
                               &config.topk_groups);
    if (status != COLI_OK) return status;

    status = read_f32_default(model, BMOQ_CONFIG_RMS_NORM_EPS, 1.0e-5f,
                              &config.rms_norm_epsilon);
    if (status != COLI_OK) return status;
    status = read_f32_default(model, BMOQ_CONFIG_ROUTED_SCALE, 1.0f,
                              &config.routed_scale);
    if (status != COLI_OK) return status;
    status = read_bool_default(model, BMOQ_CONFIG_NORMALIZE_TOPK, false,
                               &config.normalize_topk);
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

    if (config.qk_nope_head_dim > UINT32_MAX - config.qk_rope_head_dim)
        return COLI_ERR_RANGE;
    config.qk_head_dim =
        config.qk_nope_head_dim + config.qk_rope_head_dim;
    config.attention_scale = 1.0f / sqrtf((float)config.qk_head_dim);
    status = read_f32_default(model, BMOQ_CONFIG_ATTENTION_SCALE,
                              config.attention_scale,
                              &config.attention_scale);
    if (status != COLI_OK) return status;

    if (config.hidden_size == 0 || config.num_layers == 0 ||
        config.num_layers > 128 || config.num_heads == 0 ||
        config.num_experts == 0 || config.experts_per_token == 0 ||
        config.experts_per_token > config.num_experts ||
        config.moe_intermediate_size == 0 ||
        config.dense_intermediate_size == 0 ||
        config.first_dense_layers > config.num_layers ||
        config.q_lora_rank == 0 || config.kv_lora_rank == 0 ||
        config.qk_nope_head_dim == 0 || config.qk_rope_head_dim == 0 ||
        config.v_head_dim == 0 || config.expert_groups != 1 ||
        config.topk_groups == 0 || config.stop_token_count == 0 ||
        config.stop_token_count > COLI_GLM52_MAX_STOP_TOKENS ||
        config.vocab_size == 0 || config.max_context_tokens == 0 ||
        !(config.rms_norm_epsilon > 0.0f) ||
        !(config.rope_theta > 0.0f) || !(config.attention_scale > 0.0f) ||
        !(config.routed_scale > 0.0f))
        return COLI_ERR_FORMAT;

    *out_config = config;
    return COLI_OK;
}

coli_status_t coli_glm52_state_layout(const coli_glm52_config_t *config,
                                      coli_kv_cache_layout_t *out_layout)
{
    if (!config || !out_layout || config->num_layers == 0 ||
        config->max_context_tokens == 0 || config->kv_lora_rank == 0 ||
        config->qk_rope_head_dim == 0)
        return COLI_ERR_ARGUMENT;
    uint64_t latent_bytes = (uint64_t)config->kv_lora_rank * sizeof(float);
    uint64_t rope_bytes =
        (uint64_t)config->qk_rope_head_dim * sizeof(float);
    if (latent_bytes > SIZE_MAX || rope_bytes > SIZE_MAX) return COLI_ERR_RANGE;
    return coli_kv_cache_layout_custom(
        config->num_layers, config->max_context_tokens,
        (size_t)latent_bytes, (size_t)rope_bytes, out_layout);
}

coli_status_t coli_glm52_rope(float *vector, size_t count, uint32_t position,
                             float theta, float *scratch,
                             size_t scratch_count)
{
    if (!vector || !scratch || count == 0 || (count & 1u) != 0 ||
        scratch_count < count || !(theta > 1.0f))
        return COLI_ERR_ARGUMENT;
    memcpy(scratch, vector, count * sizeof(*scratch));
    const size_t half = count / 2u;
    for (size_t pair = 0; pair < half; ++pair) {
        const float exponent = (float)(2u * pair) / (float)count;
        const float angle = (float)position / powf(theta, exponent);
        const float cosine = cosf(angle);
        const float sine = sinf(angle);
        const float first = scratch[2u * pair];
        const float second = scratch[2u * pair + 1u];
        vector[pair] = first * cosine - second * sine;
        vector[half + pair] = second * cosine + first * sine;
    }
    return COLI_OK;
}

coli_status_t coli_glm52_attention_absorb_head(
    coli_kv_cache_t *state, uint32_t layer, const float *query_absorbed,
    size_t latent_count, const float *query_rope, size_t rope_count,
    uint32_t token_count, float attention_scale, float *output_latent,
    size_t output_count, float *score_workspace, size_t score_count,
    float *latent_scratch, size_t latent_scratch_count, float *rope_scratch,
    size_t rope_scratch_count)
{
    const coli_kv_cache_layout_t *layout =
        coli_kv_cache_get_layout(state);
    if (!state || !layout || !query_absorbed || !query_rope ||
        !output_latent || !score_workspace || !latent_scratch ||
        !rope_scratch || layer >= layout->layers || token_count == 0 ||
        token_count > layout->max_tokens || latent_count == 0 ||
        rope_count == 0 || output_count < latent_count ||
        score_count < token_count || latent_scratch_count < latent_count ||
        rope_scratch_count < rope_count ||
        latent_count > SIZE_MAX / sizeof(float) ||
        rope_count > SIZE_MAX / sizeof(float) ||
        layout->key_token_bytes != latent_count * sizeof(float) ||
        layout->value_token_bytes != rope_count * sizeof(float) ||
        !(attention_scale > 0.0f))
        return COLI_ERR_ARGUMENT;

    float max_score = -FLT_MAX;
    for (uint32_t token = 0; token < token_count; ++token) {
        coli_status_t status =
            coli_kv_cache_read_token(state, layer, token, latent_scratch,
                                     rope_scratch);
        if (status != COLI_OK) return status;
        float score = 0.0f;
        for (size_t i = 0; i < latent_count; ++i)
            score += query_absorbed[i] * latent_scratch[i];
        for (size_t i = 0; i < rope_count; ++i)
            score += query_rope[i] * rope_scratch[i];
        score *= attention_scale;
        score_workspace[token] = score;
        if (score > max_score) max_score = score;
    }

    float sum = 0.0f;
    for (uint32_t token = 0; token < token_count; ++token) {
        score_workspace[token] = expf(score_workspace[token] - max_score);
        sum += score_workspace[token];
    }
    if (!(sum > 0.0f)) return COLI_ERR_RANGE;
    for (uint32_t token = 0; token < token_count; ++token)
        score_workspace[token] /= sum;

    memset(output_latent, 0, latent_count * sizeof(*output_latent));
    for (uint32_t token = 0; token < token_count; ++token) {
        coli_status_t status =
            coli_kv_cache_read_key(state, layer, token, latent_scratch);
        if (status != COLI_OK) return status;
        const float weight = score_workspace[token];
        for (size_t i = 0; i < latent_count; ++i)
            output_latent[i] += weight * latent_scratch[i];
    }
    return COLI_OK;
}
