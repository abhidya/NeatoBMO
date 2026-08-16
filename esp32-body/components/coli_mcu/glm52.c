#include "coli_glm52.h"

#include <math.h>
#include <float.h>
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
    if (left != 0 && right > SIZE_MAX / left) return false;
    *out = left * right;
    return true;
}

static bool align_float(size_t bytes, size_t *out)
{
    if (!out || bytes > SIZE_MAX - (sizeof(float) - 1u)) return false;
    *out = (bytes + sizeof(float) - 1u) / sizeof(float) * sizeof(float);
    return true;
}

static bool add_floats(size_t *total, size_t count)
{
    size_t bytes;
    size_t aligned;
    return mul_size(count, sizeof(float), &bytes) &&
           align_float(bytes, &aligned) && add_size(*total, aligned, total);
}

static bool carve_floats(void **cursor, size_t *remaining, size_t count,
                         float **out)
{
    size_t bytes;
    if (!mul_size(count, sizeof(float), &bytes)) return false;
    if (!align_float(bytes, &bytes)) return false;
    if (*remaining < bytes) return false;
    *out = *cursor;
    *cursor = (uint8_t *)*cursor + bytes;
    *remaining -= bytes;
    return true;
}

static void accumulate_q4(coli_q4_stats_t *total,
                          const coli_q4_stats_t *delta)
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

static coli_status_t read_dense_f32(const coli_model_t *model,
                                    uint32_t tensor_id, float *output,
                                    size_t count)
{
    const bmoq_tensor_t *tensor = coli_model_find(model, tensor_id);
    if (!dense_f32_compatible(tensor, count)) return COLI_ERR_FORMAT;
    return coli_tensor_read(model, tensor, 0, output, count * sizeof(float));
}

static coli_status_t find_q4_pair(const coli_model_t *model,
                                  uint32_t tensor_id,
                                  const bmoq_tensor_t **weights,
                                  const bmoq_tensor_t **scales)
{
    *weights = coli_model_find(model, tensor_id);
    *scales = coli_model_find(model, coli_glm52_scale_id(tensor_id));
    return *weights && *scales ? COLI_OK : COLI_ERR_NOT_FOUND;
}

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

    if (config.hidden_size == 0 || config.hidden_size > (1u << 20) ||
        config.num_layers == 0 ||
        config.num_layers > 128 || config.num_heads == 0 ||
        config.num_heads > 1024 || config.num_experts == 0 ||
        config.num_experts > 4096 || config.experts_per_token == 0 ||
        config.experts_per_token > COLI_MOE_MAX_TOP_K ||
        config.experts_per_token > config.num_experts ||
        config.moe_intermediate_size == 0 ||
        config.moe_intermediate_size > (1u << 20) ||
        config.dense_intermediate_size == 0 ||
        config.dense_intermediate_size > (1u << 24) ||
        config.first_dense_layers > config.num_layers ||
        config.q_lora_rank == 0 || config.q_lora_rank > (1u << 20) ||
        config.kv_lora_rank == 0 || config.kv_lora_rank > (1u << 20) ||
        config.qk_nope_head_dim == 0 ||
        config.qk_nope_head_dim > (1u << 16) ||
        config.qk_rope_head_dim == 0 ||
        config.qk_rope_head_dim > (1u << 16) ||
        config.v_head_dim == 0 || config.v_head_dim > (1u << 16) ||
        config.shared_experts > 64 || config.expert_groups != 1 ||
        config.topk_groups != 1 || config.stop_token_count == 0 ||
        config.stop_token_count > COLI_GLM52_MAX_STOP_TOKENS ||
        config.vocab_size == 0 || config.vocab_size > (1u << 24) ||
        config.max_context_tokens == 0 ||
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

size_t coli_glm52_attention_required_workspace(
    const coli_glm52_config_t *config, uint32_t token_count,
    size_t q4_workspace_bytes)
{
    if (!config || token_count == 0 || token_count > config->max_context_tokens)
        return 0;
    size_t total = 0;
    size_t comp;
    size_t context;
    size_t q4_aligned;
    if (!add_size(config->kv_lora_rank, config->qk_rope_head_dim, &comp) ||
        !mul_size(config->num_heads, config->v_head_dim, &context) ||
        !align_float(q4_workspace_bytes, &q4_aligned))
        return 0;
    size_t norm = config->hidden_size > config->q_lora_rank
                      ? config->hidden_size
                      : config->q_lora_rank;
    if (norm < config->kv_lora_rank) norm = config->kv_lora_rank;
    return add_floats(&total, config->hidden_size) &&
                   add_floats(&total, norm) &&
                   add_floats(&total, config->q_lora_rank) &&
                   add_floats(&total, comp) &&
                   add_floats(&total, config->qk_head_dim) &&
                   add_floats(&total, config->kv_lora_rank) &&
                   add_floats(&total, config->kv_lora_rank) &&
                   add_floats(&total, token_count) &&
                   add_floats(&total, config->kv_lora_rank) &&
                   add_floats(&total, config->qk_rope_head_dim) &&
                   add_floats(&total, context) &&
                   add_size(total, q4_aligned, &total)
               ? total
               : 0;
}

coli_status_t coli_glm52_attention_decode(
    const coli_model_t *model, const coli_glm52_config_t *config,
    uint32_t layer, uint32_t position, const float *input,
    size_t input_count, float *output, size_t output_count,
    coli_kv_cache_t *state, void *workspace, size_t workspace_bytes,
    coli_glm52_attention_stats_t *stats)
{
    const coli_kv_cache_layout_t *layout = coli_kv_cache_get_layout(state);
    if (!model || !config || !input || !output || !state || !layout ||
        !workspace || !stats || layer >= config->num_layers ||
        position >= config->max_context_tokens ||
        input_count != config->hidden_size || output_count != input_count ||
        layout->layers < config->num_layers ||
        layout->max_tokens < config->max_context_tokens ||
        layout->key_token_bytes !=
            (uint64_t)config->kv_lora_rank * sizeof(float) ||
        layout->value_token_bytes !=
            (uint64_t)config->qk_rope_head_dim * sizeof(float))
        return COLI_ERR_ARGUMENT;

    memset(stats, 0, sizeof(*stats));
    void *cursor = workspace;
    size_t remaining = workspace_bytes;
    size_t comp_count;
    size_t context_count;
    size_t q_b_rows;
    size_t kv_b_stride;
    size_t kv_b_rows;
    if (!add_size(config->kv_lora_rank, config->qk_rope_head_dim,
                  &comp_count) ||
        !mul_size(config->num_heads, config->v_head_dim, &context_count) ||
        !mul_size(config->num_heads, config->qk_head_dim, &q_b_rows) ||
        !add_size(config->qk_nope_head_dim, config->v_head_dim,
                  &kv_b_stride) ||
        !mul_size(config->num_heads, kv_b_stride, &kv_b_rows) ||
        q_b_rows > UINT32_MAX || kv_b_rows > UINT32_MAX)
        return COLI_ERR_RANGE;
    size_t norm_count = config->hidden_size > config->q_lora_rank
                            ? config->hidden_size
                            : config->q_lora_rank;
    if (norm_count < config->kv_lora_rank) norm_count = config->kv_lora_rank;
    float *normalized;
    float *norm_weight;
    float *q_latent;
    float *comp;
    float *q_head;
    float *query_absorbed;
    float *context_latent;
    float *scores;
    float *latent_scratch;
    float *rope_scratch;
    float *context;
    if (!carve_floats(&cursor, &remaining, config->hidden_size, &normalized) ||
        !carve_floats(&cursor, &remaining, norm_count, &norm_weight) ||
        !carve_floats(&cursor, &remaining, config->q_lora_rank, &q_latent) ||
        !carve_floats(&cursor, &remaining, comp_count, &comp) ||
        !carve_floats(&cursor, &remaining, config->qk_head_dim, &q_head) ||
        !carve_floats(&cursor, &remaining, config->kv_lora_rank,
                      &query_absorbed) ||
        !carve_floats(&cursor, &remaining, config->kv_lora_rank,
                      &context_latent) ||
        !carve_floats(&cursor, &remaining, position + 1u, &scores) ||
        !carve_floats(&cursor, &remaining, config->kv_lora_rank,
                      &latent_scratch) ||
        !carve_floats(&cursor, &remaining, config->qk_rope_head_dim,
                      &rope_scratch) ||
        !carve_floats(&cursor, &remaining, context_count, &context))
        return COLI_ERR_RANGE;
    void *q4_workspace = cursor;
    const size_t q4_workspace_bytes = remaining;

    coli_status_t status = read_dense_f32(
        model, coli_glm52_input_norm_id(layer), norm_weight,
        config->hidden_size);
    if (status != COLI_OK) return status;
    status = coli_ops_rmsnorm(input, norm_weight, normalized,
                              config->hidden_size,
                              config->rms_norm_epsilon);
    if (status != COLI_OK) return status;

    const bmoq_tensor_t *weights;
    const bmoq_tensor_t *scales;
    coli_q4_stats_t q4;
    status = find_q4_pair(model, coli_glm52_q_a_id(layer), &weights, &scales);
    if (status != COLI_OK) return status;
    status = coli_q4_matvec(model, weights, scales, normalized,
                            config->hidden_size, q_latent,
                            config->q_lora_rank, q4_workspace,
                            q4_workspace_bytes, &q4);
    if (status != COLI_OK) return status;
    accumulate_q4(&stats->projections, &q4);
    status = read_dense_f32(model, coli_glm52_q_a_norm_id(layer), norm_weight,
                            config->q_lora_rank);
    if (status != COLI_OK) return status;
    status = coli_ops_rmsnorm(q_latent, norm_weight, q_latent,
                              config->q_lora_rank,
                              config->rms_norm_epsilon);
    if (status != COLI_OK) return status;

    status = find_q4_pair(model, coli_glm52_kv_a_id(layer), &weights, &scales);
    if (status != COLI_OK) return status;
    status = coli_q4_matvec(model, weights, scales, normalized,
                            config->hidden_size, comp, comp_count,
                            q4_workspace, q4_workspace_bytes, &q4);
    if (status != COLI_OK) return status;
    accumulate_q4(&stats->projections, &q4);
    status = read_dense_f32(model, coli_glm52_kv_a_norm_id(layer), norm_weight,
                            config->kv_lora_rank);
    if (status != COLI_OK) return status;
    status = coli_ops_rmsnorm(comp, norm_weight, comp,
                              config->kv_lora_rank,
                              config->rms_norm_epsilon);
    if (status != COLI_OK) return status;
    status = coli_glm52_rope(comp + config->kv_lora_rank,
                             config->qk_rope_head_dim, position,
                             config->rope_theta, rope_scratch,
                             config->qk_rope_head_dim);
    if (status != COLI_OK) return status;
    status = coli_kv_cache_write_token(state, layer, position, comp,
                                       comp + config->kv_lora_rank);
    if (status != COLI_OK) return status;

    const bmoq_tensor_t *q_b_weights;
    const bmoq_tensor_t *q_b_scales;
    const bmoq_tensor_t *kv_b_weights;
    const bmoq_tensor_t *kv_b_scales;
    status = find_q4_pair(model, coli_glm52_q_b_id(layer), &q_b_weights,
                          &q_b_scales);
    if (status != COLI_OK) return status;
    status = find_q4_pair(model, coli_glm52_kv_b_id(layer), &kv_b_weights,
                          &kv_b_scales);
    if (status != COLI_OK) return status;
    if (q_b_weights->dimensions[0] != q_b_rows ||
        q_b_weights->dimensions[1] != config->q_lora_rank ||
        kv_b_weights->dimensions[0] != kv_b_rows ||
        kv_b_weights->dimensions[1] != config->kv_lora_rank)
        return COLI_ERR_FORMAT;
    for (uint32_t head = 0; head < config->num_heads; ++head) {
        size_t q_b_base;
        size_t kv_b_base;
        size_t context_base;
        if (!mul_size(head, config->qk_head_dim, &q_b_base) ||
            !mul_size(head, kv_b_stride, &kv_b_base) ||
            !mul_size(head, config->v_head_dim, &context_base) ||
            q_b_base > UINT32_MAX || kv_b_base > UINT32_MAX)
            return COLI_ERR_RANGE;
        status = coli_q4_matvec_rows(
            model, q_b_weights, q_b_scales, (uint32_t)q_b_base,
            config->qk_head_dim, q_latent, config->q_lora_rank, q_head,
            config->qk_head_dim, q4_workspace, q4_workspace_bytes, &q4);
        if (status != COLI_OK) return status;
        accumulate_q4(&stats->projections, &q4);
        status = coli_glm52_rope(
            q_head + config->qk_nope_head_dim,
            config->qk_rope_head_dim, position, config->rope_theta,
            rope_scratch, config->qk_rope_head_dim);
        if (status != COLI_OK) return status;
        status = coli_q4_transposed_rows(
            model, kv_b_weights, kv_b_scales, (uint32_t)kv_b_base,
            config->qk_nope_head_dim, q_head,
            config->qk_nope_head_dim, query_absorbed,
            config->kv_lora_rank, q4_workspace, q4_workspace_bytes, &q4);
        if (status != COLI_OK) return status;
        accumulate_q4(&stats->projections, &q4);
        status = coli_glm52_attention_absorb_head(
            state, layer, query_absorbed, config->kv_lora_rank,
            q_head + config->qk_nope_head_dim,
            config->qk_rope_head_dim, position + 1u,
            config->attention_scale, context_latent,
            config->kv_lora_rank, scores, position + 1u, latent_scratch,
            config->kv_lora_rank, rope_scratch,
            config->qk_rope_head_dim);
        if (status != COLI_OK) return status;
        status = coli_q4_matvec_rows(
            model, kv_b_weights, kv_b_scales,
            (uint32_t)kv_b_base + config->qk_nope_head_dim,
            config->v_head_dim,
            context_latent, config->kv_lora_rank,
            context + context_base,
            config->v_head_dim, q4_workspace, q4_workspace_bytes, &q4);
        if (status != COLI_OK) return status;
        accumulate_q4(&stats->projections, &q4);
    }

    status = find_q4_pair(model, coli_glm52_o_id(layer), &weights, &scales);
    if (status != COLI_OK) return status;
    status = coli_q4_matvec(model, weights, scales, context, context_count,
                            output, output_count, q4_workspace,
                            q4_workspace_bytes, &q4);
    if (status != COLI_OK) return status;
    accumulate_q4(&stats->projections, &q4);
    stats->peak_workspace_bytes =
        workspace_bytes - q4_workspace_bytes +
        stats->projections.peak_workspace_bytes;
    return COLI_OK;
}

size_t coli_glm52_dense_mlp_required_workspace(
    const coli_glm52_config_t *config, size_t q4_workspace_bytes)
{
    if (!config || config->dense_intermediate_size == 0) return 0;
    size_t total = 0;
    size_t q4_aligned;
    return align_float(q4_workspace_bytes, &q4_aligned) &&
                   add_floats(&total, config->dense_intermediate_size) &&
                   add_floats(&total, config->dense_intermediate_size) &&
                   add_size(total, q4_aligned, &total)
               ? total
               : 0;
}

coli_status_t coli_glm52_dense_mlp_decode(
    const coli_model_t *model, const coli_glm52_config_t *config,
    uint32_t layer, const float *input, size_t input_count, float *output,
    size_t output_count, void *workspace, size_t workspace_bytes,
    coli_q4_stats_t *stats)
{
    if (!model || !config || !input || !output || !workspace || !stats ||
        layer >= config->num_layers || layer >= config->first_dense_layers ||
        input_count != config->hidden_size || output_count != input_count)
        return COLI_ERR_ARGUMENT;
    memset(stats, 0, sizeof(*stats));
    void *cursor = workspace;
    size_t remaining = workspace_bytes;
    float *gate;
    float *up;
    if (!carve_floats(&cursor, &remaining, config->dense_intermediate_size,
                      &gate) ||
        !carve_floats(&cursor, &remaining, config->dense_intermediate_size,
                      &up))
        return COLI_ERR_RANGE;

    const bmoq_tensor_t *weights;
    const bmoq_tensor_t *scales;
    coli_q4_stats_t q4;
    coli_status_t status = find_q4_pair(
        model, coli_glm52_dense_gate_id(layer), &weights, &scales);
    if (status != COLI_OK) return status;
    status = coli_q4_matvec(model, weights, scales, input, input_count, gate,
                            config->dense_intermediate_size, cursor, remaining,
                            &q4);
    if (status != COLI_OK) return status;
    accumulate_q4(stats, &q4);
    status = find_q4_pair(model, coli_glm52_dense_up_id(layer), &weights,
                          &scales);
    if (status != COLI_OK) return status;
    status = coli_q4_matvec(model, weights, scales, input, input_count, up,
                            config->dense_intermediate_size, cursor, remaining,
                            &q4);
    if (status != COLI_OK) return status;
    accumulate_q4(stats, &q4);
    status = coli_ops_silu_gated(gate, up, gate,
                                 config->dense_intermediate_size);
    if (status != COLI_OK) return status;
    status = find_q4_pair(model, coli_glm52_dense_down_id(layer), &weights,
                          &scales);
    if (status != COLI_OK) return status;
    status = coli_q4_matvec(model, weights, scales, gate,
                            config->dense_intermediate_size, output,
                            output_count, cursor, remaining, &q4);
    if (status == COLI_OK) accumulate_q4(stats, &q4);
    return status;
}

size_t coli_glm52_dense_layer_required_workspace(
    const coli_glm52_config_t *config, uint32_t token_count,
    size_t q4_workspace_bytes)
{
    if (!config) return 0;
    const size_t attention = coli_glm52_attention_required_workspace(
        config, token_count, q4_workspace_bytes);
    const size_t mlp = coli_glm52_dense_mlp_required_workspace(
        config, q4_workspace_bytes);
    if (attention == 0 || mlp == 0) return 0;
    size_t mlp_stage = 0;
    if (!add_floats(&mlp_stage, config->hidden_size) ||
        !add_floats(&mlp_stage, config->hidden_size) ||
        !add_size(mlp_stage, mlp, &mlp_stage))
        return 0;
    return attention > mlp_stage ? attention : mlp_stage;
}

coli_status_t coli_glm52_dense_layer_decode(
    const coli_model_t *model, const coli_glm52_config_t *config,
    uint32_t layer, uint32_t position, const float *input,
    size_t input_count, float *output, size_t output_count,
    coli_kv_cache_t *state, void *workspace, size_t workspace_bytes,
    coli_glm52_layer_stats_t *stats)
{
    if (!model || !config || !input || !output || !state || !workspace ||
        !stats || layer >= config->first_dense_layers ||
        input_count != config->hidden_size || output_count != input_count)
        return COLI_ERR_ARGUMENT;
    memset(stats, 0, sizeof(*stats));
    coli_status_t status = coli_glm52_attention_decode(
        model, config, layer, position, input, input_count, output,
        output_count, state, workspace, workspace_bytes, &stats->attention);
    if (status != COLI_OK) return status;
    status = coli_ops_residual_add(input, output, output, output_count);
    if (status != COLI_OK) return status;

    void *cursor = workspace;
    size_t remaining = workspace_bytes;
    float *mlp_output;
    float *normalized;
    if (!carve_floats(&cursor, &remaining, config->hidden_size, &mlp_output) ||
        !carve_floats(&cursor, &remaining, config->hidden_size, &normalized))
        return COLI_ERR_RANGE;
    status = read_dense_f32(model, coli_glm52_post_attention_norm_id(layer),
                            mlp_output, config->hidden_size);
    if (status != COLI_OK) return status;
    status = coli_ops_rmsnorm(output, mlp_output, normalized,
                              config->hidden_size,
                              config->rms_norm_epsilon);
    if (status != COLI_OK) return status;
    status = coli_glm52_dense_mlp_decode(
        model, config, layer, normalized, config->hidden_size, mlp_output,
        config->hidden_size, cursor, remaining, &stats->mlp);
    if (status != COLI_OK) return status;
    status = coli_ops_residual_add(output, mlp_output, output, output_count);
    if (status != COLI_OK) return status;
    const size_t stage_buffers = workspace_bytes - remaining;
    size_t mlp_buffers;
    if (!mul_size(config->dense_intermediate_size, 2u * sizeof(float),
                  &mlp_buffers) ||
        !add_size(mlp_buffers, stats->mlp.peak_workspace_bytes,
                  &mlp_buffers))
        return COLI_ERR_RANGE;
    if (!add_size(stage_buffers, mlp_buffers, &mlp_buffers))
        return COLI_ERR_RANGE;
    stats->peak_workspace_bytes =
        stats->attention.peak_workspace_bytes > mlp_buffers
            ? stats->attention.peak_workspace_bytes
            : mlp_buffers;
    return COLI_OK;
}

size_t coli_glm52_sparse_mlp_required_workspace(
    const coli_glm52_config_t *config, size_t q4_workspace_bytes)
{
    if (!config || config->num_experts == 0 ||
        config->experts_per_token == 0 || config->moe_intermediate_size == 0)
        return 0;
    size_t routed = coli_moe_required_workspace(
        config->hidden_size, config->moe_intermediate_size,
        config->num_experts, config->experts_per_token, q4_workspace_bytes);
    if (routed == 0 || config->shared_experts == 0) return routed;
    size_t shared_intermediate;
    size_t shared = 0;
    size_t q4_aligned;
    if (!mul_size(config->moe_intermediate_size, config->shared_experts,
                  &shared_intermediate) ||
        !add_floats(&shared, config->hidden_size) ||
        !add_floats(&shared, shared_intermediate) ||
        !add_floats(&shared, shared_intermediate) ||
        !align_float(q4_workspace_bytes, &q4_aligned) ||
        !add_size(shared, q4_aligned, &shared))
        return 0;
    return routed > shared ? routed : shared;
}

coli_status_t coli_glm52_sparse_mlp_decode(
    const coli_model_t *model, const coli_glm52_config_t *config,
    uint32_t layer, const float *input, size_t input_count, float *output,
    size_t output_count, void *workspace, size_t workspace_bytes,
    coli_moe_stats_t *moe_stats, coli_q4_stats_t *shared_stats)
{
    if (!model || !config || !input || !output || !workspace || !moe_stats ||
        !shared_stats || layer < config->first_dense_layers ||
        layer >= config->num_layers || input_count != config->hidden_size ||
        output_count != input_count)
        return COLI_ERR_ARGUMENT;
    memset(shared_stats, 0, sizeof(*shared_stats));
    const coli_moe_config_t moe = {
        .router =
            {
                .weight_id = coli_glm52_router_id(layer),
                .scale_id = coli_glm52_scale_id(coli_glm52_router_id(layer)),
            },
        .router_bias_id = coli_glm52_router_bias_id(layer),
        .expert_bundles =
            {
                .gate =
                    {
                        .weight_id = coli_glm52_expert_gate_bundle_id(layer),
                        .scale_id = coli_glm52_scale_id(
                            coli_glm52_expert_gate_bundle_id(layer)),
                    },
                .up =
                    {
                        .weight_id = coli_glm52_expert_up_bundle_id(layer),
                        .scale_id = coli_glm52_scale_id(
                            coli_glm52_expert_up_bundle_id(layer)),
                    },
                .down =
                    {
                        .weight_id = coli_glm52_expert_down_bundle_id(layer),
                        .scale_id = coli_glm52_scale_id(
                            coli_glm52_expert_down_bundle_id(layer)),
                    },
            },
        .expert_count = config->num_experts,
        .top_k = config->experts_per_token,
        .norm_topk_prob = config->normalize_topk,
        .experts_bundled = true,
        .sigmoid_router = true,
        .routed_scale = config->routed_scale,
    };
    coli_status_t status = coli_moe_forward(
        model, &moe, input, input_count, output, output_count, workspace,
        workspace_bytes, moe_stats);
    if (status != COLI_OK || config->shared_experts == 0) return status;

    size_t shared_intermediate;
    if (!mul_size(config->moe_intermediate_size, config->shared_experts,
                  &shared_intermediate))
        return COLI_ERR_RANGE;
    void *cursor = workspace;
    size_t remaining = workspace_bytes;
    float *shared_output;
    float *gate;
    float *up;
    if (!carve_floats(&cursor, &remaining, config->hidden_size,
                      &shared_output) ||
        !carve_floats(&cursor, &remaining, shared_intermediate, &gate) ||
        !carve_floats(&cursor, &remaining, shared_intermediate, &up))
        return COLI_ERR_RANGE;
    const bmoq_tensor_t *weights;
    const bmoq_tensor_t *scales;
    coli_q4_stats_t q4;
    status = find_q4_pair(model, coli_glm52_shared_gate_id(layer), &weights,
                          &scales);
    if (status != COLI_OK) return status;
    status = coli_q4_matvec(model, weights, scales, input, input_count, gate,
                            shared_intermediate, cursor, remaining, &q4);
    if (status != COLI_OK) return status;
    accumulate_q4(shared_stats, &q4);
    status = find_q4_pair(model, coli_glm52_shared_up_id(layer), &weights,
                          &scales);
    if (status != COLI_OK) return status;
    status = coli_q4_matvec(model, weights, scales, input, input_count, up,
                            shared_intermediate, cursor, remaining, &q4);
    if (status != COLI_OK) return status;
    accumulate_q4(shared_stats, &q4);
    status = coli_ops_silu_gated(gate, up, gate, shared_intermediate);
    if (status != COLI_OK) return status;
    status = find_q4_pair(model, coli_glm52_shared_down_id(layer), &weights,
                          &scales);
    if (status != COLI_OK) return status;
    status = coli_q4_matvec(model, weights, scales, gate,
                            shared_intermediate, shared_output,
                            config->hidden_size, cursor, remaining, &q4);
    if (status != COLI_OK) return status;
    accumulate_q4(shared_stats, &q4);
    return coli_ops_residual_add(output, shared_output, output, output_count);
}

size_t coli_glm52_sparse_layer_required_workspace(
    const coli_glm52_config_t *config, uint32_t token_count,
    size_t q4_workspace_bytes)
{
    if (!config) return 0;
    const size_t attention = coli_glm52_attention_required_workspace(
        config, token_count, q4_workspace_bytes);
    const size_t sparse = coli_glm52_sparse_mlp_required_workspace(
        config, q4_workspace_bytes);
    if (attention == 0 || sparse == 0) return 0;
    size_t sparse_stage = 0;
    if (!add_floats(&sparse_stage, config->hidden_size) ||
        !add_floats(&sparse_stage, config->hidden_size) ||
        !add_size(sparse_stage, sparse, &sparse_stage))
        return 0;
    return attention > sparse_stage ? attention : sparse_stage;
}

coli_status_t coli_glm52_sparse_layer_decode(
    const coli_model_t *model, const coli_glm52_config_t *config,
    uint32_t layer, uint32_t position, const float *input,
    size_t input_count, float *output, size_t output_count,
    coli_kv_cache_t *state, void *workspace, size_t workspace_bytes,
    coli_glm52_layer_stats_t *stats)
{
    if (!model || !config || !input || !output || !state || !workspace ||
        !stats || layer < config->first_dense_layers ||
        layer >= config->num_layers || input_count != config->hidden_size ||
        output_count != input_count)
        return COLI_ERR_ARGUMENT;
    memset(stats, 0, sizeof(*stats));
    coli_status_t status = coli_glm52_attention_decode(
        model, config, layer, position, input, input_count, output,
        output_count, state, workspace, workspace_bytes, &stats->attention);
    if (status != COLI_OK) return status;
    status = coli_ops_residual_add(input, output, output, output_count);
    if (status != COLI_OK) return status;

    void *cursor = workspace;
    size_t remaining = workspace_bytes;
    float *mlp_output;
    float *normalized;
    if (!carve_floats(&cursor, &remaining, config->hidden_size, &mlp_output) ||
        !carve_floats(&cursor, &remaining, config->hidden_size, &normalized))
        return COLI_ERR_RANGE;
    status = read_dense_f32(model, coli_glm52_post_attention_norm_id(layer),
                            mlp_output, config->hidden_size);
    if (status != COLI_OK) return status;
    status = coli_ops_rmsnorm(output, mlp_output, normalized,
                              config->hidden_size,
                              config->rms_norm_epsilon);
    if (status != COLI_OK) return status;
    status = coli_glm52_sparse_mlp_decode(
        model, config, layer, normalized, config->hidden_size, mlp_output,
        config->hidden_size, cursor, remaining, &stats->moe,
        &stats->shared_expert);
    if (status != COLI_OK) return status;
    status = coli_ops_residual_add(output, mlp_output, output, output_count);
    if (status != COLI_OK) return status;
    const size_t stage_buffers = workspace_bytes - remaining;
    size_t sparse_peak = stats->moe.peak_workspace_bytes;
    size_t shared_intermediate;
    size_t shared_peak = 0;
    if (config->shared_experts != 0) {
        if (!mul_size(config->moe_intermediate_size, config->shared_experts,
                      &shared_intermediate) ||
            !add_floats(&shared_peak, config->hidden_size) ||
            !add_floats(&shared_peak, shared_intermediate) ||
            !add_floats(&shared_peak, shared_intermediate) ||
            !add_size(shared_peak, stats->shared_expert.peak_workspace_bytes,
                      &shared_peak))
            return COLI_ERR_RANGE;
        if (sparse_peak < shared_peak) sparse_peak = shared_peak;
    }
    if (!add_size(stage_buffers, sparse_peak, &sparse_peak))
        return COLI_ERR_RANGE;
    stats->peak_workspace_bytes =
        stats->attention.peak_workspace_bytes > sparse_peak
            ? stats->attention.peak_workspace_bytes
            : sparse_peak;
    return COLI_OK;
}
