#include "coli_ops.h"

#include <float.h>
#include <math.h>
#include <stdbool.h>
#include <string.h>

static bool mul_u64(uint64_t a, uint64_t b, uint64_t *out)
{
    if (!out) return false;
    if (a != 0 && b > UINT64_MAX / a) return false;
    *out = a * b;
    return true;
}

static bool add_u64(uint64_t a, uint64_t b, uint64_t *out)
{
    if (!out || b > UINT64_MAX - a) return false;
    *out = a + b;
    return true;
}

coli_status_t coli_ops_rmsnorm(const float *input, const float *weight,
                               float *output, size_t count, float epsilon)
{
    if (!input || !weight || !output || count == 0 || !(epsilon > 0.0f))
        return COLI_ERR_ARGUMENT;
    double sum_squares = 0.0;
    for (size_t i = 0; i < count; ++i)
        sum_squares += (double)input[i] * (double)input[i];
    const float scale = 1.0f / sqrtf((float)(sum_squares / (double)count) +
                                     epsilon);
    for (size_t i = 0; i < count; ++i)
        output[i] = input[i] * scale * weight[i];
    return COLI_OK;
}

coli_status_t coli_ops_residual_add(const float *left, const float *right,
                                    float *output, size_t count)
{
    if (!left || !right || !output || count == 0) return COLI_ERR_ARGUMENT;
    for (size_t i = 0; i < count; ++i) output[i] = left[i] + right[i];
    return COLI_OK;
}

coli_status_t coli_ops_silu_gated(const float *gate, const float *up,
                                  float *output, size_t count)
{
    if (!gate || !up || !output || count == 0) return COLI_ERR_ARGUMENT;
    for (size_t i = 0; i < count; ++i) {
        const float sigmoid = 1.0f / (1.0f + expf(-gate[i]));
        output[i] = gate[i] * sigmoid * up[i];
    }
    return COLI_OK;
}

coli_status_t coli_ops_rope_apply(float *query, float *key, uint32_t heads,
                                  uint32_t head_dim, uint32_t position,
                                  float theta_base)
{
    if (!query || !key || heads == 0 || head_dim == 0 ||
        (head_dim & 1u) != 0 || !(theta_base > 1.0f))
        return COLI_ERR_ARGUMENT;

    for (uint32_t head = 0; head < heads; ++head) {
        float *q_head = query + (size_t)head * head_dim;
        float *k_head = key + (size_t)head * head_dim;
        for (uint32_t d = 0; d < head_dim; d += 2) {
            const float exponent = (float)d / (float)head_dim;
            const float angle = (float)position / powf(theta_base, exponent);
            const float cosine = cosf(angle);
            const float sine = sinf(angle);
            const float q0 = q_head[d];
            const float q1 = q_head[d + 1];
            const float k0 = k_head[d];
            const float k1 = k_head[d + 1];
            q_head[d] = q0 * cosine - q1 * sine;
            q_head[d + 1] = q0 * sine + q1 * cosine;
            k_head[d] = k0 * cosine - k1 * sine;
            k_head[d + 1] = k0 * sine + k1 * cosine;
        }
    }
    return COLI_OK;
}

coli_status_t coli_ops_attention_decode(const float *query, const float *keys,
                                        const float *values,
                                        uint32_t token_count, uint32_t heads,
                                        uint32_t head_dim, float *output,
                                        float *score_workspace,
                                        size_t score_count)
{
    if (!query || !keys || !values || !output || !score_workspace ||
        token_count == 0 || heads == 0 || head_dim == 0 ||
        score_count < token_count)
        return COLI_ERR_ARGUMENT;

    const float inv_sqrt_dim = 1.0f / sqrtf((float)head_dim);
    const size_t head_values = head_dim;
    const size_t token_values = (size_t)heads * head_dim;
    for (uint32_t head = 0; head < heads; ++head) {
        const float *q_head = query + (size_t)head * head_dim;
        float max_score = -FLT_MAX;
        for (uint32_t token = 0; token < token_count; ++token) {
            const float *k_head =
                keys + (size_t)token * token_values + (size_t)head * head_dim;
            float score = 0.0f;
            for (uint32_t d = 0; d < head_dim; ++d)
                score += q_head[d] * k_head[d];
            score *= inv_sqrt_dim;
            score_workspace[token] = score;
            if (score > max_score) max_score = score;
        }

        float sum_exp = 0.0f;
        for (uint32_t token = 0; token < token_count; ++token) {
            const float value = expf(score_workspace[token] - max_score);
            score_workspace[token] = value;
            sum_exp += value;
        }
        if (!(sum_exp > 0.0f)) return COLI_ERR_RANGE;

        float *out_head = output + (size_t)head * head_values;
        memset(out_head, 0, head_values * sizeof(*out_head));
        for (uint32_t token = 0; token < token_count; ++token) {
            const float weight = score_workspace[token] / sum_exp;
            const float *v_head =
                values + (size_t)token * token_values + (size_t)head * head_dim;
            for (uint32_t d = 0; d < head_dim; ++d)
                out_head[d] += weight * v_head[d];
        }
    }
    return COLI_OK;
}

coli_status_t coli_ops_kv_cache_layout(uint32_t layers, uint32_t heads,
                                       uint32_t head_dim, uint32_t max_tokens,
                                       size_t bytes_per_value,
                                       coli_kv_cache_layout_t *out_layout)
{
    if (!out_layout || layers == 0 || heads == 0 || head_dim == 0 ||
        max_tokens == 0 || bytes_per_value == 0)
        return COLI_ERR_ARGUMENT;

    uint64_t token_values;
    uint64_t token_bytes;
    uint64_t plane_bytes;
    uint64_t layer_stride;
    uint64_t total_bytes;
    if (!mul_u64(heads, head_dim, &token_values) ||
        !mul_u64(token_values, (uint64_t)bytes_per_value, &token_bytes) ||
        !mul_u64(token_bytes, max_tokens, &plane_bytes) ||
        !add_u64(plane_bytes, plane_bytes, &layer_stride) ||
        !mul_u64(layer_stride, layers, &total_bytes) ||
        total_bytes > (uint64_t)SIZE_MAX)
        return COLI_ERR_RANGE;

    out_layout->layers = layers;
    out_layout->heads = heads;
    out_layout->head_dim = head_dim;
    out_layout->max_tokens = max_tokens;
    out_layout->bytes_per_value = bytes_per_value;
    out_layout->key_token_bytes = token_bytes;
    out_layout->value_token_bytes = token_bytes;
    out_layout->key_layer_bytes = plane_bytes;
    out_layout->value_layer_bytes = plane_bytes;
    out_layout->layer_stride_bytes = layer_stride;
    out_layout->total_bytes = total_bytes;
    return COLI_OK;
}

coli_status_t coli_ops_kv_cache_token_ptrs(
    uint8_t *cache, size_t cache_bytes, const coli_kv_cache_layout_t *layout,
    uint32_t layer, uint32_t token, void **out_key, void **out_value)
{
    if (!cache || !layout || !out_key || !out_value ||
        cache_bytes < layout->total_bytes || layer >= layout->layers ||
        token >= layout->max_tokens)
        return COLI_ERR_ARGUMENT;

    uint64_t token_values;
    uint64_t token_bytes;
    uint64_t layer_offset;
    uint64_t token_offset;
    uint64_t key_offset;
    uint64_t value_base;
    uint64_t value_offset;
    if (!mul_u64(layout->heads, layout->head_dim, &token_values) ||
        !mul_u64(token_values, (uint64_t)layout->bytes_per_value,
                 &token_bytes) ||
        !mul_u64((uint64_t)layer, layout->layer_stride_bytes, &layer_offset) ||
        !mul_u64((uint64_t)token, token_bytes, &token_offset) ||
        !add_u64(layer_offset, token_offset, &key_offset) ||
        !add_u64(layer_offset, layout->key_layer_bytes, &value_base) ||
        !add_u64(value_base, token_offset, &value_offset) ||
        value_offset > UINT64_MAX - token_bytes ||
        value_offset + token_bytes > layout->total_bytes ||
        layout->total_bytes > (uint64_t)cache_bytes)
        return COLI_ERR_RANGE;

    *out_key = cache + (size_t)key_offset;
    *out_value = cache + (size_t)value_offset;
    return COLI_OK;
}
