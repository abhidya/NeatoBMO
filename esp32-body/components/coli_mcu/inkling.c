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

static uint32_t max_u32(uint32_t left, uint32_t right)
{
    return left > right ? left : right;
}

static bool layer_id_present(const uint32_t *layer_ids, size_t count,
                             uint32_t layer)
{
    for (size_t i = 0; i < count; ++i)
        if (layer_ids[i] == layer) return true;
    return false;
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

static bool add_size(size_t left, size_t right, size_t *out)
{
    if (!out || left > SIZE_MAX - right) return false;
    *out = left + right;
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
        const uint32_t dimension =
            tensor->dimensions[i] ? tensor->dimensions[i] : 1u;
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

static coli_status_t read_scalar_f32(const coli_model_t *model,
                                     uint32_t tensor_id, float *out_value)
{
    if (!out_value) return COLI_ERR_ARGUMENT;
    return read_dense_f32(model, tensor_id, out_value, 1);
}

static coli_status_t find_q4_pair(const coli_model_t *model,
                                  uint32_t tensor_id,
                                  const bmoq_tensor_t **weights,
                                  const bmoq_tensor_t **scales)
{
    if (!weights || !scales) return COLI_ERR_ARGUMENT;
    *weights = coli_model_find(model, tensor_id);
    *scales = coli_model_find(model, coli_inkling_scale_id(tensor_id));
    return *weights && *scales ? COLI_OK : COLI_ERR_NOT_FOUND;
}

static coli_status_t q4_project(const coli_model_t *model, uint32_t tensor_id,
                                const float *input, size_t input_count,
                                float *output, size_t output_count,
                                void *workspace, size_t workspace_bytes,
                                coli_q4_stats_t *stats)
{
    const bmoq_tensor_t *weights;
    const bmoq_tensor_t *scales;
    coli_status_t status = find_q4_pair(model, tensor_id, &weights, &scales);
    if (status != COLI_OK) return status;
    return coli_q4_matvec(model, weights, scales, input, input_count, output,
                          output_count, workspace, workspace_bytes, stats);
}

static coli_status_t q4_project_rows(
    const coli_model_t *model, uint32_t tensor_id, uint32_t first_row,
    uint32_t row_count, const float *input, size_t input_count, float *output,
    size_t output_count, void *workspace, size_t workspace_bytes,
    coli_q4_stats_t *stats)
{
    const bmoq_tensor_t *weights;
    const bmoq_tensor_t *scales;
    coli_status_t status = find_q4_pair(model, tensor_id, &weights, &scales);
    if (status != COLI_OK) return status;
    return coli_q4_matvec_rows(model, weights, scales, first_row, row_count,
                               input, input_count, output, output_count,
                               workspace, workspace_bytes, stats);
}

static coli_status_t dense_f32_matvec(
    const coli_model_t *model, uint32_t tensor_id, const float *input,
    size_t input_count, float *output, size_t output_count, float *row_scratch,
    size_t row_scratch_count)
{
    if (!model || !input || !output || !row_scratch ||
        row_scratch_count < input_count)
        return COLI_ERR_ARGUMENT;
    const bmoq_tensor_t *tensor = coli_model_find(model, tensor_id);
    if (!tensor || tensor->dtype != BMOQ_DTYPE_F32 ||
        tensor->layout != BMOQ_LAYOUT_DENSE_F32 ||
        tensor->dimensions[0] != output_count ||
        tensor->dimensions[1] != input_count)
        return COLI_ERR_FORMAT;
    for (uint32_t row = 0; row < output_count; ++row) {
        coli_status_t status = coli_tensor_read(
            model, tensor, (uint64_t)row * input_count * sizeof(float),
            row_scratch, input_count * sizeof(float));
        if (status != COLI_OK) return status;
        float sum = 0.0f;
        for (size_t column = 0; column < input_count; ++column)
            sum += row_scratch[column] * input[column];
        output[row] = sum;
    }
    return COLI_OK;
}

static bool is_local_layer(const coli_inkling_config_t *config, uint32_t layer)
{
    return layer_id_present(config->local_layer_ids, config->local_layer_count,
                            layer);
}

static bool is_sparse_layer(const coli_inkling_config_t *config,
                            uint32_t layer)
{
    return layer_id_present(config->sparse_layer_ids,
                            config->sparse_layer_count, layer);
}

static uint32_t layer_heads(const coli_inkling_config_t *config,
                            uint32_t layer)
{
    return is_local_layer(config, layer) ? config->swa_num_heads
                                        : config->num_heads;
}

static uint32_t layer_kv_heads(const coli_inkling_config_t *config,
                               uint32_t layer)
{
    return is_local_layer(config, layer) ? config->swa_num_key_value_heads
                                        : config->num_key_value_heads;
}

static uint32_t layer_head_dim(const coli_inkling_config_t *config,
                               uint32_t layer)
{
    return is_local_layer(config, layer) ? config->swa_head_dim
                                        : config->head_dim;
}

static uint32_t layer_rel_extent(const coli_inkling_config_t *config,
                                 uint32_t layer)
{
    return is_local_layer(config, layer) ? config->sliding_window
                                        : config->rel_extent;
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
    status = coli_model_config_read(
        model, BMOQ_CONFIG_LOCAL_LAYER_IDS, BMOQ_CONFIG_U32_ARRAY,
        config.local_layer_ids, sizeof(config.local_layer_ids),
        &config.local_layer_count);
    if (status == COLI_ERR_NOT_FOUND) {
        config.local_layer_count = 0;
        status = COLI_OK;
    }
    if (status != COLI_OK) return status;
    status = coli_model_config_read(
        model, BMOQ_CONFIG_SPARSE_LAYER_IDS, BMOQ_CONFIG_U32_ARRAY,
        config.sparse_layer_ids, sizeof(config.sparse_layer_ids),
        &config.sparse_layer_count);
    if (status == COLI_ERR_NOT_FOUND) {
        config.sparse_layer_count = 0;
        status = COLI_OK;
    }
    if (status != COLI_OK) return status;

    size_t global_qdim = 0;
    size_t global_kvdim = 0;
    size_t local_qdim = 0;
    size_t local_kvdim = 0;
    size_t checked_shape = 0;
    const uint32_t max_extent = max_u32(config.rel_extent,
                                        config.sliding_window);
    const bool shapes_valid =
        mul_size(config.num_heads, config.head_dim, &global_qdim) &&
        mul_size(config.num_key_value_heads, config.head_dim,
                 &global_kvdim) &&
        mul_size(config.swa_num_heads, config.swa_head_dim, &local_qdim) &&
        mul_size(config.swa_num_key_value_heads, config.swa_head_dim,
                 &local_kvdim) &&
        global_qdim <= UINT32_MAX && global_kvdim <= UINT32_MAX &&
        local_qdim <= UINT32_MAX && local_kvdim <= UINT32_MAX &&
        mul_size(global_qdim, sizeof(float), &checked_shape) &&
        mul_size(global_kvdim, config.conv_kernel, &checked_shape) &&
        mul_size(checked_shape, sizeof(float), &checked_shape) &&
        mul_size(local_qdim, sizeof(float), &checked_shape) &&
        mul_size(local_kvdim, config.conv_kernel, &checked_shape) &&
        mul_size(checked_shape, sizeof(float), &checked_shape) &&
        mul_size(config.d_rel, max_extent, &checked_shape) &&
        mul_size(checked_shape, sizeof(float), &checked_shape) &&
        mul_size(config.num_heads, config.rel_extent, &checked_shape) &&
        mul_size(checked_shape, sizeof(float), &checked_shape) &&
        mul_size(config.swa_num_heads, config.sliding_window,
                 &checked_shape) &&
        mul_size(checked_shape, sizeof(float), &checked_shape) &&
        config.num_experts <= SIZE_MAX - config.shared_experts;

    if (!shapes_valid || config.hidden_size == 0 ||
        config.hidden_size > (1u << 20) ||
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
    uint32_t capacities[COLI_KV_CACHE_MAX_LAYERS];
    size_t key_bytes[COLI_KV_CACHE_MAX_LAYERS];
    size_t value_bytes[COLI_KV_CACHE_MAX_LAYERS];
    if (config->num_layers > COLI_KV_CACHE_MAX_LAYERS)
        return COLI_ERR_ARGUMENT;
    for (uint32_t layer = 0; layer < config->num_layers; ++layer) {
        const bool local_layer =
            layer_id_present(config->local_layer_ids,
                             config->local_layer_count, layer);
        const uint32_t layer_kv_heads =
            local_layer ? config->swa_num_key_value_heads
                        : config->num_key_value_heads;
        const uint32_t layer_head_dim =
            local_layer ? config->swa_head_dim : config->head_dim;
        capacities[layer] =
            local_layer ? config->sliding_window : config->max_context_tokens;
        key_bytes[layer] =
            (size_t)layer_kv_heads * layer_head_dim * sizeof(float);
        value_bytes[layer] = key_bytes[layer];
    }
    coli_status_t status = coli_kv_cache_layout_custom_per_layer_bytes(
        config->num_layers, config->max_context_tokens, capacities,
        key_bytes, value_bytes, out_layout);
    if (status != COLI_OK) return status;
    out_layout->heads = kv_heads;
    out_layout->head_dim = head_dim;
    out_layout->bytes_per_value = sizeof(float);
    return COLI_OK;
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

size_t coli_inkling_layer_conv_state_floats(
    const coli_inkling_config_t *config, uint32_t layer)
{
    if (!config || layer >= config->num_layers) return 0;
    const size_t kv =
        (size_t)layer_kv_heads(config, layer) * layer_head_dim(config, layer);
    size_t total = 0;
    size_t one = coli_inkling_conv_state_floats(config, (uint32_t)kv);
    if (!add_size(total, one, &total) || !add_size(total, one, &total))
        return 0;
    one = coli_inkling_conv_state_floats(config, config->hidden_size);
    if (!add_size(total, one, &total) || !add_size(total, one, &total))
        return 0;
    return total;
}

coli_status_t coli_inkling_conv_state_layouts(
    const coli_inkling_config_t *config, coli_kv_cache_layout_t *out_kv_conv,
    coli_kv_cache_layout_t *out_residual_conv)
{
    if (!config || !out_kv_conv || !out_residual_conv ||
        config->num_layers > COLI_KV_CACHE_MAX_LAYERS)
        return COLI_ERR_ARGUMENT;
    uint32_t capacities[COLI_KV_CACHE_MAX_LAYERS];
    size_t k_bytes[COLI_KV_CACHE_MAX_LAYERS];
    size_t v_bytes[COLI_KV_CACHE_MAX_LAYERS];
    size_t a_bytes[COLI_KV_CACHE_MAX_LAYERS];
    size_t m_bytes[COLI_KV_CACHE_MAX_LAYERS];
    for (uint32_t layer = 0; layer < config->num_layers; ++layer) {
        const size_t kv = (size_t)layer_kv_heads(config, layer) *
                          layer_head_dim(config, layer);
        capacities[layer] = 1u;
        k_bytes[layer] =
            coli_inkling_conv_state_floats(config, (uint32_t)kv) *
            sizeof(float);
        v_bytes[layer] = k_bytes[layer];
        a_bytes[layer] =
            coli_inkling_conv_state_floats(config, config->hidden_size) *
            sizeof(float);
        m_bytes[layer] = a_bytes[layer];
    }
    coli_status_t status = coli_kv_cache_layout_custom_per_layer_bytes(
        config->num_layers, 1u, capacities, k_bytes, v_bytes, out_kv_conv);
    if (status != COLI_OK) return status;
    status = coli_kv_cache_layout_custom_per_layer_bytes(
        config->num_layers, 1u, capacities, a_bytes, m_bytes,
        out_residual_conv);
    if (status != COLI_OK) return status;
    out_kv_conv->bytes_per_value = sizeof(float);
    out_residual_conv->bytes_per_value = sizeof(float);
    return COLI_OK;
}

size_t coli_inkling_decode_required_workspace(
    const coli_inkling_config_t *config, uint32_t position,
    size_t q4_workspace_bytes)
{
    if (!config || position >= config->max_context_tokens) return 0;
    size_t total = 0;
    size_t q4_aligned = 0;
    uint32_t max_heads = max_u32(config->num_heads, config->swa_num_heads);
    uint32_t max_kv_heads =
        max_u32(config->num_key_value_heads, config->swa_num_key_value_heads);
    uint32_t max_head_dim = max_u32(config->head_dim, config->swa_head_dim);
    size_t hidden = config->hidden_size;
    size_t qdim = (size_t)max_heads * max_head_dim;
    size_t kvdim = (size_t)max_kv_heads * max_head_dim;
    size_t rel = (size_t)max_heads * config->d_rel;
    size_t rel_bias = (size_t)max_heads * max_u32(config->rel_extent,
                                                  config->sliding_window);
    size_t intermediate = max_u32(config->dense_intermediate_size,
                                  config->moe_intermediate_size);
    size_t selected = config->experts_per_token + config->shared_experts;
    if (!align_float(q4_workspace_bytes, &q4_aligned)) return 0;
    size_t tmp = intermediate > config->num_experts ? intermediate
                                                    : config->num_experts;
    return add_floats(&total, hidden) && add_floats(&total, hidden) &&
                   add_floats(&total, hidden) && add_floats(&total, hidden) &&
                   add_floats(&total, hidden) && add_floats(&total, qdim) &&
                   add_floats(&total, kvdim) && add_floats(&total, kvdim) &&
                   add_floats(&total, rel) &&
                   add_floats(&total, (size_t)config->d_rel *
                                          max_u32(config->rel_extent,
                                                  config->sliding_window)) &&
                   add_floats(&total, rel_bias) &&
                   add_floats(&total, 1u) &&
                   add_floats(&total, kvdim) && add_floats(&total, kvdim) &&
                   add_floats(&total, kvdim * (config->conv_kernel - 1u)) &&
                   add_floats(&total, kvdim * (config->conv_kernel - 1u)) &&
                   add_floats(&total, kvdim * config->conv_kernel) &&
                   add_floats(&total, kvdim * config->conv_kernel) &&
                   add_floats(&total, qdim) && add_floats(&total, hidden) &&
                   add_floats(&total, hidden * (config->conv_kernel - 1u)) &&
                   add_floats(&total, hidden * (config->conv_kernel - 1u)) &&
                   add_floats(&total, hidden * config->conv_kernel) &&
                   add_floats(&total, intermediate) &&
                   add_floats(&total, intermediate) &&
                   add_floats(&total, tmp) &&
                   add_floats(&total, selected * hidden) &&
                   add_floats(&total, config->num_experts +
                                          config->shared_experts) &&
                   add_size(total, q4_aligned, &total)
               ? total
               : 0;
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
        float acc = input[channel] *
                    weights[(size_t)channel * kernel + kernel - 1u];
        float *history = state + (size_t)channel * (kernel - 1u);
        for (uint32_t tap = 0; tap + 1u < kernel; ++tap)
            acc += history[tap] * weights[(size_t)channel * kernel + tap];
        output[channel] = input[channel] + acc;
        for (uint32_t i = 0; i + 2u < kernel; ++i)
            history[i] = history[i + 1u];
        history[kernel - 2u] = input[channel];
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
        !relative_bias || !score_workspace || !key_scratch || !value_scratch ||
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
    const size_t kv_values = (size_t)kv_heads * head_dim;
    const uint64_t layer_key_bytes =
        layout->variable_layer_capacities
            ? layout->layer_key_token_bytes[layer]
            : layout->key_token_bytes;
    const uint64_t layer_value_bytes =
        layout->variable_layer_capacities
            ? layout->layer_value_token_bytes[layer]
            : layout->value_token_bytes;
    if (layer_key_bytes % sizeof(float) != 0 ||
        layer_value_bytes % sizeof(float) != 0 ||
        layer_key_bytes / sizeof(float) > SIZE_MAX ||
        layer_value_bytes / sizeof(float) > SIZE_MAX)
        return COLI_ERR_FORMAT;
    const size_t key_stride = (size_t)(layer_key_bytes / sizeof(float));
    const size_t value_stride = (size_t)(layer_value_bytes / sizeof(float));
    if (heads == 0 || kv_heads == 0 || heads % kv_heads != 0 ||
        head_dim == 0 || output_count < (size_t)heads * head_dim ||
        score_count < 1u || key_scratch_count < key_stride ||
        value_scratch_count < value_stride || kv_values != key_stride ||
        kv_values != value_stride ||
        relative_bias_count < extent)
        return COLI_ERR_ARGUMENT;

    memset(key_scratch, 0, key_stride * sizeof(*key_scratch));
    memset(value_scratch, 0, value_stride * sizeof(*value_scratch));
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
        for (uint32_t token = start; token <= position; ++token) {
            status = coli_kv_cache_read_key(state, layer, token, key_scratch);
            if (status != COLI_OK) return status;
            const float *k_head = key_scratch + (size_t)kv_head * head_dim;
            float score = 0.0f;
            for (uint32_t d = 0; d < head_dim; ++d)
                score += q_head[d] * k_head[d];
            uint32_t distance = position - token;
            const bool per_head_bias =
                relative_bias_count >= (size_t)heads * extent;
            const size_t bias_index =
                per_head_bias ? (size_t)head * extent + distance : distance;
            const float bias =
                distance < extent ? relative_bias[bias_index] : 0.0f;
            score = (score * scale + bias) * tau;
            if (score > max_score) max_score = score;
        }
        float sum = 0.0f;
        float *out_head = output + (size_t)head * head_dim;
        memset(out_head, 0, head_dim * sizeof(*out_head));
        for (uint32_t token = start; token <= position; ++token) {
            status = coli_kv_cache_read_key(state, layer, token, key_scratch);
            if (status != COLI_OK) return status;
            const float *k_head = key_scratch + (size_t)kv_head * head_dim;
            float score = 0.0f;
            for (uint32_t d = 0; d < head_dim; ++d)
                score += q_head[d] * k_head[d];
            uint32_t distance = position - token;
            const bool per_head_bias =
                relative_bias_count >= (size_t)heads * extent;
            const size_t bias_index =
                per_head_bias ? (size_t)head * extent + distance : distance;
            const float bias =
                distance < extent ? relative_bias[bias_index] : 0.0f;
            score = (score * scale + bias) * tau;
            score_workspace[0] = expf(score - max_score);
            sum += score_workspace[0];
            status = coli_kv_cache_read_value(state, layer, token,
                                              value_scratch);
            if (status != COLI_OK) return status;
            const float *v_head = value_scratch + (size_t)kv_head * head_dim;
            for (uint32_t d = 0; d < head_dim; ++d)
                out_head[d] += score_workspace[0] * v_head[d];
        }
        if (!(sum > 0.0f)) return COLI_ERR_RANGE;
        for (uint32_t d = 0; d < head_dim; ++d) out_head[d] /= sum;
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

static coli_status_t read_conv_state(coli_kv_cache_t *cache, uint32_t layer,
                                     float *first, float *second)
{
    coli_status_t status = coli_kv_cache_read_token(cache, layer, 0, first,
                                                    second);
    if (status == COLI_ERR_RANGE || status == COLI_ERR_NOT_FOUND) {
        const coli_kv_cache_layout_t *layout = coli_kv_cache_get_layout(cache);
        if (!layout || layer >= layout->layers) return COLI_ERR_ARGUMENT;
        const size_t first_count =
            (size_t)(layout->layer_key_token_bytes[layer] / sizeof(float));
        const size_t second_count =
            (size_t)(layout->layer_value_token_bytes[layer] / sizeof(float));
        memset(first, 0, first_count * sizeof(float));
        memset(second, 0, second_count * sizeof(float));
        return COLI_OK;
    }
    return status;
}

static void swiglu_intermediate(const float *gate, const float *up,
                                uint32_t count, float *output)
{
    for (uint32_t i = 0; i < count; ++i) {
        float g = gate[i];
        float sig = g >= 0.0f ? 1.0f / (1.0f + expf(-g))
                              : expf(g) / (1.0f + expf(g));
        output[i] = g * sig * up[i];
    }
}

static coli_status_t q4_expert_project(
    const coli_model_t *model, uint32_t bundle_id, uint32_t expert,
    const float *input, size_t input_count, float *output,
    size_t output_count, void *workspace, size_t workspace_bytes,
    coli_q4_stats_t *stats)
{
    const bmoq_tensor_t *bundle;
    const bmoq_tensor_t *scale_bundle;
    coli_status_t status = find_q4_pair(model, bundle_id, &bundle,
                                        &scale_bundle);
    if (status != COLI_OK) return status;
    bmoq_tensor_t weights;
    bmoq_tensor_t scales;
    status = coli_model_q4_expert_view(bundle, scale_bundle, expert, &weights,
                                       &scales);
    if (status != COLI_OK) return status;
    return coli_q4_matvec(model, &weights, &scales, input, input_count, output,
                          output_count, workspace, workspace_bytes, stats);
}

static coli_status_t q4_argmax_limited(
    const coli_model_t *model, uint32_t tensor_id, const float *input,
    size_t input_count, uint32_t row_count, float divisor, void *workspace,
    size_t workspace_bytes, uint32_t *out_row, float *out_value,
    coli_q4_stats_t *stats)
{
    if (!out_row || !out_value || row_count == 0 || !(divisor > 0.0f))
        return COLI_ERR_ARGUMENT;
    memset(stats, 0, sizeof(*stats));
    float value = 0.0f;
    bool have = false;
    for (uint32_t row = 0; row < row_count; ++row) {
        coli_q4_stats_t q4;
        coli_status_t status = q4_project_rows(
            model, tensor_id, row, 1u, input, input_count, &value, 1u,
            workspace, workspace_bytes, &q4);
        if (status != COLI_OK) return status;
        accumulate_q4(stats, &q4);
        value /= divisor;
        if (!have || value > *out_value) {
            *out_value = value;
            *out_row = row;
            have = true;
        }
    }
    return have ? COLI_OK : COLI_ERR_RANGE;
}

static coli_status_t compute_relative_bias(
    const coli_model_t *model, uint32_t layer, const coli_inkling_config_t *cfg,
    uint32_t heads, uint32_t extent, const float *rel, float *relp,
    float *bias, size_t bias_count)
{
    if (bias_count < (size_t)heads * extent) return COLI_ERR_ARGUMENT;
    coli_status_t status =
        read_dense_f32(model, coli_inkling_rel_proj_id(layer), relp,
                       (size_t)cfg->d_rel * extent);
    if (status != COLI_OK) return status;
    for (uint32_t head = 0; head < heads; ++head) {
        for (uint32_t distance = 0; distance < extent; ++distance) {
            float sum = 0.0f;
            for (uint32_t d = 0; d < cfg->d_rel; ++d) {
                sum += rel[(size_t)head * cfg->d_rel + d] *
                       relp[(size_t)d * extent + distance];
            }
            bias[(size_t)head * extent + distance] = sum;
        }
    }
    return COLI_OK;
}

static coli_status_t inkling_sparse_mlp(
    const coli_model_t *model, const coli_inkling_config_t *cfg,
    uint32_t layer, const float *input, float *output, float *gate, float *up,
    float *tmp, float *expert_outputs, float *router_logits, void *q4_workspace,
    size_t q4_workspace_bytes, float *row_scratch, size_t row_scratch_count,
    coli_inkling_layer_stats_t *stats)
{
    coli_q4_stats_t q4;
    coli_status_t status = dense_f32_matvec(
        model, coli_inkling_router_id(layer), input, cfg->hidden_size,
        router_logits, cfg->num_experts + cfg->shared_experts, row_scratch,
        row_scratch_count);
    if (status != COLI_OK) return status;
    status = read_dense_f32(model, coli_inkling_router_bias_id(layer), tmp,
                            cfg->num_experts);
    if (status != COLI_OK) return status;
    float global_scale = 1.0f;
    status = read_scalar_f32(model, coli_inkling_router_scale_id(layer),
                             &global_scale);
    if (status != COLI_OK) return status;
    status = coli_inkling_moe_route(cfg, router_logits, tmp, global_scale,
                                    &stats->moe);
    if (status != COLI_OK) return status;

    for (size_t i = 0; i < stats->moe.selected_count; ++i) {
        const uint32_t expert = stats->moe.selected_experts[i];
        status = q4_expert_project(
            model, coli_inkling_routed_gate_bundle_id(layer), expert, input,
            cfg->hidden_size, gate, cfg->moe_intermediate_size, q4_workspace,
            q4_workspace_bytes, &q4);
        if (status != COLI_OK) return status;
        accumulate_q4(&stats->mlp_q4, &q4);
        status = q4_expert_project(
            model, coli_inkling_routed_up_bundle_id(layer), expert, input,
            cfg->hidden_size, up, cfg->moe_intermediate_size, q4_workspace,
            q4_workspace_bytes, &q4);
        if (status != COLI_OK) return status;
        accumulate_q4(&stats->mlp_q4, &q4);
        swiglu_intermediate(gate, up, cfg->moe_intermediate_size, tmp);
        status = q4_expert_project(
            model, coli_inkling_routed_down_bundle_id(layer), expert, tmp,
            cfg->moe_intermediate_size,
            expert_outputs + i * cfg->hidden_size, cfg->hidden_size,
            q4_workspace, q4_workspace_bytes, &q4);
        if (status != COLI_OK) return status;
        accumulate_q4(&stats->mlp_q4, &q4);
    }

    float *shared_outputs =
        expert_outputs + stats->moe.selected_count * cfg->hidden_size;
    for (size_t shared = 0; shared < stats->moe.shared_count; ++shared) {
        uint32_t first = (uint32_t)(shared * cfg->moe_intermediate_size);
        status = q4_project_rows(
            model, coli_inkling_shared_gate_id(layer), first,
            cfg->moe_intermediate_size, input, cfg->hidden_size, gate,
            cfg->moe_intermediate_size, q4_workspace, q4_workspace_bytes, &q4);
        if (status != COLI_OK) return status;
        accumulate_q4(&stats->mlp_q4, &q4);
        status = q4_project_rows(
            model, coli_inkling_shared_up_id(layer), first,
            cfg->moe_intermediate_size, input, cfg->hidden_size, up,
            cfg->moe_intermediate_size, q4_workspace, q4_workspace_bytes, &q4);
        if (status != COLI_OK) return status;
        accumulate_q4(&stats->mlp_q4, &q4);
        swiglu_intermediate(gate, up, cfg->moe_intermediate_size, tmp);
        status = q4_project_rows(
            model, coli_inkling_shared_down_id(layer),
            (uint32_t)(shared * cfg->hidden_size), cfg->hidden_size, tmp,
            cfg->moe_intermediate_size, shared_outputs + shared * cfg->hidden_size,
            cfg->hidden_size, q4_workspace, q4_workspace_bytes, &q4);
        if (status != COLI_OK) return status;
        accumulate_q4(&stats->mlp_q4, &q4);
    }
    return coli_inkling_moe_combine(cfg, &stats->moe, expert_outputs,
                                    stats->moe.selected_count * cfg->hidden_size,
                                    shared_outputs,
                                    stats->moe.shared_count * cfg->hidden_size,
                                    output, cfg->hidden_size);
}

static coli_status_t inkling_layer_decode(
    const coli_model_t *model, const coli_inkling_config_t *cfg,
    uint32_t layer, uint32_t position, const float *input, float *output,
    coli_kv_cache_t *kv_cache, coli_kv_cache_t *conv_kv_cache,
    coli_kv_cache_t *conv_residual_cache, void *workspace,
    size_t workspace_bytes, coli_inkling_layer_stats_t *stats)
{
    if (!model || !cfg || !input || !output || !kv_cache || !conv_kv_cache ||
        !conv_residual_cache || !workspace || !stats ||
        layer >= cfg->num_layers || position >= cfg->max_context_tokens)
        return COLI_ERR_ARGUMENT;
    memset(stats, 0, sizeof(*stats));
    void *cursor = workspace;
    size_t remaining = workspace_bytes;
    const uint32_t heads = layer_heads(cfg, layer);
    const uint32_t kv_heads = layer_kv_heads(cfg, layer);
    const uint32_t head_dim = layer_head_dim(cfg, layer);
    const uint32_t extent = layer_rel_extent(cfg, layer);
    const uint32_t qdim = heads * head_dim;
    const uint32_t kvdim = kv_heads * head_dim;
    const uint32_t intermediate =
        is_sparse_layer(cfg, layer) ? cfg->moe_intermediate_size
                                    : cfg->dense_intermediate_size;

    float *norm;
    float *norm_weight;
    float *query;
    float *key;
    float *value;
    float *rel;
    float *relp;
    float *bias;
    float *scores;
    float *key_scratch;
    float *value_scratch;
    float *k_hist;
    float *v_hist;
    float *k_conv_weight;
    float *v_conv_weight;
    float *attn;
    float *projected;
    float *attn_hist;
    float *mlp_hist;
    float *residual_conv_weight;
    float *gate;
    float *up;
    float *tmp;
    float *expert_outputs;
    float *router_logits;
    size_t tmp_count = intermediate > cfg->num_experts ? intermediate
                                                       : cfg->num_experts;
    if (!carve_floats(&cursor, &remaining, cfg->hidden_size, &norm) ||
        !carve_floats(&cursor, &remaining, cfg->hidden_size, &norm_weight) ||
        !carve_floats(&cursor, &remaining, qdim, &query) ||
        !carve_floats(&cursor, &remaining, kvdim, &key) ||
        !carve_floats(&cursor, &remaining, kvdim, &value) ||
        !carve_floats(&cursor, &remaining, (size_t)heads * cfg->d_rel, &rel) ||
        !carve_floats(&cursor, &remaining, (size_t)cfg->d_rel * extent,
                      &relp) ||
        !carve_floats(&cursor, &remaining, (size_t)heads * extent, &bias) ||
        !carve_floats(&cursor, &remaining, 1u, &scores) ||
        !carve_floats(&cursor, &remaining, kvdim, &key_scratch) ||
        !carve_floats(&cursor, &remaining, kvdim, &value_scratch) ||
        !carve_floats(&cursor, &remaining,
                      coli_inkling_conv_state_floats(cfg, kvdim), &k_hist) ||
        !carve_floats(&cursor, &remaining,
                      coli_inkling_conv_state_floats(cfg, kvdim), &v_hist) ||
        !carve_floats(&cursor, &remaining, (size_t)kvdim * cfg->conv_kernel,
                      &k_conv_weight) ||
        !carve_floats(&cursor, &remaining, (size_t)kvdim * cfg->conv_kernel,
                      &v_conv_weight) ||
        !carve_floats(&cursor, &remaining, qdim, &attn) ||
        !carve_floats(&cursor, &remaining, cfg->hidden_size, &projected) ||
        !carve_floats(&cursor, &remaining,
                      coli_inkling_conv_state_floats(cfg, cfg->hidden_size),
                      &attn_hist) ||
        !carve_floats(&cursor, &remaining,
                      coli_inkling_conv_state_floats(cfg, cfg->hidden_size),
                      &mlp_hist) ||
        !carve_floats(&cursor, &remaining,
                      (size_t)cfg->hidden_size * cfg->conv_kernel,
                      &residual_conv_weight) ||
        !carve_floats(&cursor, &remaining, intermediate, &gate) ||
        !carve_floats(&cursor, &remaining, intermediate, &up) ||
        !carve_floats(&cursor, &remaining, tmp_count, &tmp) ||
        !carve_floats(&cursor, &remaining,
                      (cfg->experts_per_token + cfg->shared_experts) *
                          cfg->hidden_size,
                      &expert_outputs) ||
        !carve_floats(&cursor, &remaining,
                      cfg->num_experts + cfg->shared_experts,
                      &router_logits))
        return COLI_ERR_RANGE;
    void *q4_workspace = cursor;
    const size_t q4_workspace_bytes = remaining;

    coli_status_t status =
        read_dense_f32(model, coli_inkling_input_norm_id(layer), norm_weight,
                       cfg->hidden_size);
    if (status != COLI_OK) return status;
    status = coli_ops_rmsnorm(input, norm_weight, norm, cfg->hidden_size,
                              cfg->rms_norm_epsilon);
    if (status != COLI_OK) return status;

    coli_q4_stats_t q4;
    status = q4_project(model, coli_inkling_q_proj_id(layer), norm,
                        cfg->hidden_size, query, qdim, q4_workspace,
                        q4_workspace_bytes, &q4);
    if (status != COLI_OK) return status;
    accumulate_q4(&stats->attention_q4, &q4);
    status = q4_project(model, coli_inkling_k_proj_id(layer), norm,
                        cfg->hidden_size, key, kvdim, q4_workspace,
                        q4_workspace_bytes, &q4);
    if (status != COLI_OK) return status;
    accumulate_q4(&stats->attention_q4, &q4);
    status = q4_project(model, coli_inkling_v_proj_id(layer), norm,
                        cfg->hidden_size, value, kvdim, q4_workspace,
                        q4_workspace_bytes, &q4);
    if (status != COLI_OK) return status;
    accumulate_q4(&stats->attention_q4, &q4);
    status = q4_project(model, coli_inkling_r_proj_id(layer), norm,
                        cfg->hidden_size, rel, (size_t)heads * cfg->d_rel,
                        q4_workspace, q4_workspace_bytes, &q4);
    if (status != COLI_OK) return status;
    accumulate_q4(&stats->attention_q4, &q4);

    status = read_conv_state(conv_kv_cache, layer, k_hist, v_hist);
    if (status != COLI_OK) return status;
    status = read_dense_f32(model, coli_inkling_k_conv_id(layer), k_conv_weight,
                            (size_t)kvdim * cfg->conv_kernel);
    if (status != COLI_OK) return status;
    status = coli_inkling_short_conv_step(key, k_conv_weight, kvdim,
                                          cfg->conv_kernel, k_hist,
                                          coli_inkling_conv_state_floats(cfg,
                                                                         kvdim),
                                          key, kvdim);
    if (status != COLI_OK) return status;
    status = read_dense_f32(model, coli_inkling_v_conv_id(layer), v_conv_weight,
                            (size_t)kvdim * cfg->conv_kernel);
    if (status != COLI_OK) return status;
    status = coli_inkling_short_conv_step(value, v_conv_weight, kvdim,
                                          cfg->conv_kernel, v_hist,
                                          coli_inkling_conv_state_floats(cfg,
                                                                         kvdim),
                                          value, kvdim);
    if (status != COLI_OK) return status;
    status = coli_kv_cache_write_token(conv_kv_cache, layer, 0, k_hist,
                                       v_hist);
    if (status != COLI_OK) return status;

    status = read_dense_f32(model, coli_inkling_q_norm_id(layer), norm_weight,
                            head_dim);
    if (status != COLI_OK) return status;
    status = coli_inkling_qk_rmsnorm(query, norm_weight, heads, head_dim,
                                     cfg->rms_norm_epsilon);
    if (status != COLI_OK) return status;
    status = read_dense_f32(model, coli_inkling_k_norm_id(layer), norm_weight,
                            head_dim);
    if (status != COLI_OK) return status;
    status = coli_inkling_qk_rmsnorm(key, norm_weight, kv_heads, head_dim,
                                     cfg->rms_norm_epsilon);
    if (status != COLI_OK) return status;
    status = compute_relative_bias(model, layer, cfg, heads, extent, rel, relp,
                                   bias, (size_t)heads * extent);
    if (status != COLI_OK) return status;
    status = coli_inkling_attention_decode(
        cfg, kv_cache, layer, is_local_layer(cfg, layer), position, query, key,
        value, bias, (size_t)heads * extent, attn, qdim, scores,
        1u, key_scratch, kvdim, value_scratch, kvdim);
    if (status != COLI_OK) return status;

    status = q4_project(model, coli_inkling_o_proj_id(layer), attn, qdim,
                        projected, cfg->hidden_size, q4_workspace,
                        q4_workspace_bytes, &q4);
    if (status != COLI_OK) return status;
    accumulate_q4(&stats->attention_q4, &q4);
    status = read_conv_state(conv_residual_cache, layer, attn_hist,
                             mlp_hist);
    if (status != COLI_OK) return status;
    status = read_dense_f32(model, coli_inkling_attn_conv_id(layer),
                            residual_conv_weight,
                            (size_t)cfg->hidden_size * cfg->conv_kernel);
    if (status != COLI_OK) return status;
    status = coli_inkling_short_conv_step(
        projected, residual_conv_weight, cfg->hidden_size, cfg->conv_kernel,
        attn_hist,
        coli_inkling_conv_state_floats(cfg, cfg->hidden_size), projected,
        cfg->hidden_size);
    if (status != COLI_OK) return status;
    status = coli_ops_residual_add(input, projected, output,
                                   cfg->hidden_size);
    if (status != COLI_OK) return status;

    status = read_dense_f32(model, coli_inkling_post_attention_norm_id(layer),
                            norm_weight, cfg->hidden_size);
    if (status != COLI_OK) return status;
    status = coli_ops_rmsnorm(output, norm_weight, norm, cfg->hidden_size,
                              cfg->rms_norm_epsilon);
    if (status != COLI_OK) return status;

    if (is_sparse_layer(cfg, layer)) {
        status = inkling_sparse_mlp(model, cfg, layer, norm, projected, gate,
                                    up, tmp, expert_outputs, router_logits,
                                    q4_workspace, q4_workspace_bytes,
                                    norm_weight, cfg->hidden_size, stats);
    } else {
        status = q4_project(model, coli_inkling_dense_gate_id(layer), norm,
                            cfg->hidden_size, gate,
                            cfg->dense_intermediate_size, q4_workspace,
                            q4_workspace_bytes, &q4);
        if (status != COLI_OK) return status;
        accumulate_q4(&stats->mlp_q4, &q4);
        status = q4_project(model, coli_inkling_dense_up_id(layer), norm,
                            cfg->hidden_size, up,
                            cfg->dense_intermediate_size, q4_workspace,
                            q4_workspace_bytes, &q4);
        if (status != COLI_OK) return status;
        accumulate_q4(&stats->mlp_q4, &q4);
        float scale = 1.0f;
        status = read_scalar_f32(model, coli_inkling_dense_scale_id(layer),
                                 &scale);
        if (status != COLI_OK) return status;
        const bmoq_tensor_t *weights;
        const bmoq_tensor_t *scales;
        status = find_q4_pair(model, coli_inkling_dense_down_id(layer),
                              &weights, &scales);
        if (status != COLI_OK) return status;
        swiglu_intermediate(gate, up, cfg->dense_intermediate_size, tmp);
        status = coli_q4_matvec(model, weights, scales, tmp,
                                cfg->dense_intermediate_size, projected,
                                cfg->hidden_size, q4_workspace,
                                q4_workspace_bytes, &q4);
        if (status != COLI_OK) return status;
        for (uint32_t h = 0; h < cfg->hidden_size; ++h) projected[h] *= scale;
        accumulate_q4(&stats->mlp_q4, &q4);
    }
    if (status != COLI_OK) return status;

    status = read_dense_f32(model, coli_inkling_mlp_conv_id(layer),
                            residual_conv_weight,
                            (size_t)cfg->hidden_size * cfg->conv_kernel);
    if (status != COLI_OK) return status;
    status = coli_inkling_short_conv_step(
        projected, residual_conv_weight, cfg->hidden_size, cfg->conv_kernel,
        mlp_hist,
        coli_inkling_conv_state_floats(cfg, cfg->hidden_size), projected,
        cfg->hidden_size);
    if (status != COLI_OK) return status;
    status = coli_kv_cache_write_token(conv_residual_cache, layer, 0,
                                       attn_hist, mlp_hist);
    if (status != COLI_OK) return status;
    status = coli_ops_residual_add(output, projected, output,
                                   cfg->hidden_size);
    if (status != COLI_OK) return status;
    stats->peak_workspace_bytes = workspace_bytes - q4_workspace_bytes;
    if (stats->peak_workspace_bytes < stats->attention_q4.peak_workspace_bytes)
        stats->peak_workspace_bytes = stats->attention_q4.peak_workspace_bytes;
    if (stats->peak_workspace_bytes < stats->mlp_q4.peak_workspace_bytes)
        stats->peak_workspace_bytes = stats->mlp_q4.peak_workspace_bytes;
    return COLI_OK;
}

coli_status_t coli_inkling_decode_next_token(
    const coli_model_t *model, const coli_inkling_config_t *config,
    uint32_t input_token_id, uint32_t position, coli_kv_cache_t *kv_cache,
    coli_kv_cache_t *conv_kv_cache, coli_kv_cache_t *conv_residual_cache,
    void *workspace, size_t workspace_bytes, uint32_t *out_token_id,
    coli_inkling_decode_stats_t *stats)
{
    if (!model || !config || !kv_cache || !conv_kv_cache ||
        !conv_residual_cache || !workspace || !out_token_id || !stats ||
        input_token_id >= config->vocab_size ||
        position >= config->max_context_tokens)
        return COLI_ERR_ARGUMENT;
    memset(stats, 0, sizeof(*stats));
    void *cursor = workspace;
    size_t remaining = workspace_bytes;
    float *state;
    float *next;
    float *norm_weight;
    if (!carve_floats(&cursor, &remaining, config->hidden_size, &state) ||
        !carve_floats(&cursor, &remaining, config->hidden_size, &next) ||
        !carve_floats(&cursor, &remaining, config->hidden_size, &norm_weight))
        return COLI_ERR_RANGE;
    void *layer_workspace = cursor;
    size_t layer_workspace_bytes = remaining;

    const bmoq_tensor_t *embed =
        coli_model_find(model, COLI_INKLING_TENSOR_EMBED_TOKENS);
    const bmoq_tensor_t *embed_scales = coli_model_find(
        model, coli_inkling_scale_id(COLI_INKLING_TENSOR_EMBED_TOKENS));
    coli_status_t status = coli_q4_dequantize_row(
        model, embed, embed_scales, input_token_id, state, config->hidden_size,
        layer_workspace, layer_workspace_bytes, &stats->embedding_q4);
    if (status != COLI_OK) return status;
    status = read_dense_f32(model, COLI_INKLING_TENSOR_EMBED_NORM,
                            norm_weight, config->hidden_size);
    if (status != COLI_OK) return status;
    status = coli_ops_rmsnorm(state, norm_weight, state, config->hidden_size,
                              config->rms_norm_epsilon);
    if (status != COLI_OK) return status;

    for (uint32_t layer = 0; layer < config->num_layers; ++layer) {
        coli_inkling_layer_stats_t layer_stats;
        status = inkling_layer_decode(
            model, config, layer, position, state, next, kv_cache,
            conv_kv_cache, conv_residual_cache, layer_workspace,
            layer_workspace_bytes, &layer_stats);
        if (status != COLI_OK) return status;
        stats->last_layer = layer_stats;
        ++stats->layers_executed;
        float *swap = state;
        state = next;
        next = swap;
    }

    status = read_dense_f32(model, COLI_INKLING_TENSOR_FINAL_NORM,
                            norm_weight, config->hidden_size);
    if (status != COLI_OK) return status;
    status = coli_ops_rmsnorm(state, norm_weight, state, config->hidden_size,
                              config->rms_norm_epsilon);
    if (status != COLI_OK) return status;
    status = q4_argmax_limited(
        model, COLI_INKLING_TENSOR_LM_HEAD, state, config->hidden_size,
        config->unpadded_vocab_size, config->logits_mup_width_multiplier,
        layer_workspace, layer_workspace_bytes, out_token_id,
        &stats->selected_logit, &stats->lm_head_q4);
    if (status != COLI_OK) return status;
    stats->peak_workspace_bytes = workspace_bytes - layer_workspace_bytes;
    if (stats->peak_workspace_bytes < stats->last_layer.peak_workspace_bytes)
        stats->peak_workspace_bytes = stats->last_layer.peak_workspace_bytes;
    return COLI_OK;
}
