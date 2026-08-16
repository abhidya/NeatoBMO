#include "coli_q4.h"

#include <limits.h>
#include <stdbool.h>
#include <string.h>

static float read_f32_le(const uint8_t *bytes)
{
    uint32_t bits = (uint32_t)bytes[0] | ((uint32_t)bytes[1] << 8) |
                    ((uint32_t)bytes[2] << 16) |
                    ((uint32_t)bytes[3] << 24);
    float value;
    memcpy(&value, &bits, sizeof(value));
    return value;
}

static int8_t unpack_q4(uint8_t packed, bool high)
{
    uint8_t nibble = high ? packed >> 4 : packed & 0x0fu;
    return nibble < 8 ? (int8_t)nibble : (int8_t)(nibble - 16);
}

static void count_read(coli_q4_stats_t *stats, uint64_t absolute_offset,
                       size_t length, bool scale)
{
    if (scale)
        stats->scale_bytes_read += length;
    else
        stats->weight_bytes_read += length;
    ++stats->storage_reads;
    if (length && absolute_offset / BMOQ_DATA_ALIGNMENT !=
                      (absolute_offset + length - 1) / BMOQ_DATA_ALIGNMENT)
        ++stats->page_boundary_crossings;
}

static bool compatible_tensors(const bmoq_tensor_t *weights,
                               const bmoq_tensor_t *scales)
{
    if (!weights || !scales || weights->dtype != BMOQ_DTYPE_Q4_SYM ||
        weights->layout != BMOQ_LAYOUT_Q4_ROW_MAJOR ||
        scales->dtype != BMOQ_DTYPE_F32 ||
        scales->layout != BMOQ_LAYOUT_GROUP_SCALES_F32 ||
        weights->quant_group != scales->quant_group ||
        weights->quant_group < 2 || (weights->quant_group & 1u) != 0 ||
        weights->dimensions[0] == 0 || weights->dimensions[1] == 0 ||
        weights->dimensions[2] != 1 || weights->dimensions[3] != 1 ||
        weights->dimensions[1] % weights->quant_group != 0)
        return false;
    uint32_t groups = weights->dimensions[1] / weights->quant_group;
    uint64_t weight_bytes =
        (uint64_t)weights->dimensions[0] * weights->dimensions[1] / 2;
    uint64_t scale_bytes =
        (uint64_t)weights->dimensions[0] * groups * sizeof(float);
    return weights->byte_length == weight_bytes &&
           scales->byte_length == scale_bytes &&
           scales->dimensions[0] == weights->dimensions[0] &&
           scales->dimensions[1] == groups && scales->dimensions[2] == 1 &&
           scales->dimensions[3] == 1;
}

static coli_status_t setup_tiling(const bmoq_tensor_t *weights,
                                  const bmoq_tensor_t *scales,
                                  size_t vector_count,
                                  void *workspace,
                                  size_t workspace_bytes,
                                  coli_q4_stats_t *stats,
                                  uint32_t *out_groups_per_row,
                                  size_t *out_group_weight_bytes,
                                  size_t *out_tile_groups,
                                  uint8_t **out_bytes)
{
    if (!workspace || !stats || !compatible_tensors(weights, scales))
        return COLI_ERR_ARGUMENT;
    const uint32_t columns = weights->dimensions[1];
    const uint32_t group_size = weights->quant_group;
    const uint32_t groups_per_row = columns / group_size;
    const size_t group_weight_bytes = group_size / 2;
    const size_t group_workspace_bytes = group_weight_bytes + sizeof(float);
    if (vector_count != columns || workspace_bytes < group_workspace_bytes)
        return COLI_ERR_RANGE;
    size_t tile_groups = workspace_bytes / group_workspace_bytes;
    if (tile_groups > groups_per_row) tile_groups = groups_per_row;
    if (tile_groups > UINT32_MAX) tile_groups = UINT32_MAX;
    memset(stats, 0, sizeof(*stats));
    stats->peak_workspace_bytes = tile_groups * group_workspace_bytes;
    *out_groups_per_row = groups_per_row;
    *out_group_weight_bytes = group_weight_bytes;
    *out_tile_groups = tile_groups;
    *out_bytes = workspace;
    return COLI_OK;
}

static coli_status_t read_group_tile(const coli_model_t *model,
                                     const bmoq_tensor_t *weights,
                                     const bmoq_tensor_t *scales,
                                     uint32_t row,
                                     uint32_t first_group,
                                     uint32_t group_count,
                                     uint32_t groups_per_row,
                                     size_t group_weight_bytes,
                                     uint8_t *bytes,
                                     uint8_t **out_scale_data,
                                     coli_q4_stats_t *stats)
{
    size_t weight_bytes = (size_t)group_count * group_weight_bytes;
    size_t scale_bytes = (size_t)group_count * sizeof(float);
    uint64_t row_weight_bytes = (uint64_t)weights->dimensions[1] / 2;
    uint64_t row_scale_bytes = (uint64_t)groups_per_row * sizeof(float);
    uint64_t weight_offset = (uint64_t)row * row_weight_bytes +
                             (uint64_t)first_group * group_weight_bytes;
    coli_status_t status =
        coli_tensor_read(model, weights, weight_offset, bytes, weight_bytes);
    if (status != COLI_OK) return status;
    count_read(stats, weights->data_offset + weight_offset, weight_bytes, false);
    uint8_t *scale_data = bytes + weight_bytes;
    uint64_t scale_offset = (uint64_t)row * row_scale_bytes +
                            (uint64_t)first_group * sizeof(float);
    status = coli_tensor_read(model, scales, scale_offset, scale_data,
                              scale_bytes);
    if (status != COLI_OK) return status;
    count_read(stats, scales->data_offset + scale_offset, scale_bytes, true);
    *out_scale_data = scale_data;
    return COLI_OK;
}

coli_status_t coli_q4_matvec(const coli_model_t *model,
                             const bmoq_tensor_t *weights,
                             const bmoq_tensor_t *scales,
                             const float *input, size_t input_count,
                             float *output, size_t output_count,
                             void *workspace, size_t workspace_bytes,
                             coli_q4_stats_t *stats)
{
    if (!weights) return COLI_ERR_ARGUMENT;
    return coli_q4_matvec_rows(model, weights, scales, 0,
                               weights->dimensions[0], input, input_count,
                               output, output_count, workspace,
                               workspace_bytes, stats);
}

coli_status_t coli_q4_matvec_rows(
    const coli_model_t *model, const bmoq_tensor_t *weights,
    const bmoq_tensor_t *scales, uint32_t first_row, uint32_t row_count,
    const float *input, size_t input_count, float *output,
    size_t output_count, void *workspace, size_t workspace_bytes,
    coli_q4_stats_t *stats)
{
    if (!model || !input || !output) return COLI_ERR_ARGUMENT;
    uint32_t groups_per_row;
    size_t group_weight_bytes;
    size_t tile_groups;
    uint8_t *bytes;
    coli_status_t status =
        setup_tiling(weights, scales, input_count, workspace, workspace_bytes,
                     stats, &groups_per_row, &group_weight_bytes, &tile_groups,
                     &bytes);
    if (status != COLI_OK) return status;
    const uint32_t rows = weights->dimensions[0];
    const uint32_t group_size = weights->quant_group;
    if (row_count == 0 || first_row > rows || row_count > rows - first_row ||
        output_count != row_count)
        return COLI_ERR_RANGE;

    for (uint32_t output_row = 0; output_row < row_count; ++output_row) {
        const uint32_t row = first_row + output_row;
        float sum = 0.0f;
        for (uint32_t first_group = 0; first_group < groups_per_row;) {
            uint32_t remaining = groups_per_row - first_group;
            uint32_t group_count = remaining < tile_groups ? remaining : tile_groups;
            uint8_t *scale_data = NULL;
            status = read_group_tile(model, weights, scales, row, first_group,
                                     group_count, groups_per_row,
                                     group_weight_bytes, bytes, &scale_data,
                                     stats);
            if (status != COLI_OK) return status;

            for (uint32_t group = 0; group < group_count; ++group) {
                float scale = read_f32_le(scale_data + group * sizeof(float));
                const uint8_t *packed = bytes + group * group_weight_bytes;
                uint32_t column = (first_group + group) * group_size;
                for (uint32_t within = 0; within < group_size; ++within) {
                    int8_t q = unpack_q4(packed[within / 2], (within & 1u) != 0);
                    sum += (float)q * scale * input[column + within];
                }
            }
            first_group += group_count;
        }
        output[output_row] = sum;
    }
    return COLI_OK;
}

coli_status_t coli_q4_transposed_rows(
    const coli_model_t *model, const bmoq_tensor_t *weights,
    const bmoq_tensor_t *scales, uint32_t first_row, uint32_t row_count,
    const float *input, size_t input_count, float *output,
    size_t output_count, void *workspace, size_t workspace_bytes,
    coli_q4_stats_t *stats)
{
    if (!model || !weights || !input || !output) return COLI_ERR_ARGUMENT;
    uint32_t groups_per_row;
    size_t group_weight_bytes;
    size_t tile_groups;
    uint8_t *bytes;
    coli_status_t status = setup_tiling(
        weights, scales, output_count, workspace, workspace_bytes, stats,
        &groups_per_row, &group_weight_bytes, &tile_groups, &bytes);
    if (status != COLI_OK) return status;
    const uint32_t rows = weights->dimensions[0];
    const uint32_t group_size = weights->quant_group;
    if (row_count == 0 || first_row > rows || row_count > rows - first_row ||
        input_count != row_count || output_count > SIZE_MAX / sizeof(*output))
        return COLI_ERR_RANGE;

    memset(output, 0, output_count * sizeof(*output));
    for (uint32_t input_row = 0; input_row < row_count; ++input_row) {
        const uint32_t row = first_row + input_row;
        const float coefficient = input[input_row];
        for (uint32_t first_group = 0; first_group < groups_per_row;) {
            uint32_t remaining = groups_per_row - first_group;
            uint32_t group_count =
                remaining < tile_groups ? remaining : tile_groups;
            uint8_t *scale_data = NULL;
            status = read_group_tile(model, weights, scales, row, first_group,
                                     group_count, groups_per_row,
                                     group_weight_bytes, bytes, &scale_data,
                                     stats);
            if (status != COLI_OK) return status;
            for (uint32_t group = 0; group < group_count; ++group) {
                const float scale =
                    read_f32_le(scale_data + group * sizeof(float));
                const uint8_t *packed = bytes + group * group_weight_bytes;
                const uint32_t column =
                    (first_group + group) * group_size;
                for (uint32_t within = 0; within < group_size; ++within) {
                    const int8_t q = unpack_q4(
                        packed[within / 2], (within & 1u) != 0);
                    output[column + within] +=
                        coefficient * (float)q * scale;
                }
            }
            first_group += group_count;
        }
    }
    return COLI_OK;
}

coli_status_t coli_q4_dequantize_row(const coli_model_t *model,
                                     const bmoq_tensor_t *weights,
                                     const bmoq_tensor_t *scales,
                                     uint32_t row,
                                     float *output, size_t output_count,
                                     void *workspace, size_t workspace_bytes,
                                     coli_q4_stats_t *stats)
{
    if (!model || !output) return COLI_ERR_ARGUMENT;
    if (!weights) return COLI_ERR_ARGUMENT;
    if (row >= weights->dimensions[0]) return COLI_ERR_RANGE;
    uint32_t groups_per_row;
    size_t group_weight_bytes;
    size_t tile_groups;
    uint8_t *bytes;
    coli_status_t status =
        setup_tiling(weights, scales, output_count, workspace, workspace_bytes,
                     stats, &groups_per_row, &group_weight_bytes, &tile_groups,
                     &bytes);
    if (status != COLI_OK) return status;
    const uint32_t columns = weights->dimensions[1];
    const uint32_t group_size = weights->quant_group;
    for (uint32_t first_group = 0; first_group < groups_per_row;) {
        uint32_t remaining = groups_per_row - first_group;
        uint32_t group_count = remaining < tile_groups ? remaining : tile_groups;
        uint8_t *scale_data = NULL;
        status = read_group_tile(model, weights, scales, row, first_group,
                                 group_count, groups_per_row,
                                 group_weight_bytes, bytes, &scale_data, stats);
        if (status != COLI_OK) return status;
        for (uint32_t group = 0; group < group_count; ++group) {
            float scale = read_f32_le(scale_data + group * sizeof(float));
            const uint8_t *packed = bytes + group * group_weight_bytes;
            uint32_t column = (first_group + group) * group_size;
            for (uint32_t within = 0; within < group_size; ++within) {
                int8_t q = unpack_q4(packed[within / 2], (within & 1u) != 0);
                output[column + within] = (float)q * scale;
            }
        }
        first_group += group_count;
    }
    return columns == output_count ? COLI_OK : COLI_ERR_RANGE;
}

coli_status_t coli_q4_argmax(const coli_model_t *model,
                             const bmoq_tensor_t *weights,
                             const bmoq_tensor_t *scales,
                             const float *input, size_t input_count,
                             void *workspace, size_t workspace_bytes,
                             uint32_t *out_row, float *out_value,
                             coli_q4_stats_t *stats)
{
    if (!model || !input || !out_row || !out_value) return COLI_ERR_ARGUMENT;
    uint32_t groups_per_row;
    size_t group_weight_bytes;
    size_t tile_groups;
    uint8_t *bytes;
    coli_status_t status =
        setup_tiling(weights, scales, input_count, workspace, workspace_bytes,
                     stats, &groups_per_row, &group_weight_bytes, &tile_groups,
                     &bytes);
    if (status != COLI_OK) return status;
    const uint32_t rows = weights->dimensions[0];
    const uint32_t group_size = weights->quant_group;

    float best = 0.0f;
    uint32_t best_row = 0;
    bool have_best = false;
    for (uint32_t row = 0; row < rows; ++row) {
        float sum = 0.0f;
        for (uint32_t first_group = 0; first_group < groups_per_row;) {
            uint32_t remaining = groups_per_row - first_group;
            uint32_t group_count = remaining < tile_groups ? remaining : tile_groups;
            uint8_t *scale_data = NULL;
            status = read_group_tile(model, weights, scales, row, first_group,
                                     group_count, groups_per_row,
                                     group_weight_bytes, bytes, &scale_data,
                                     stats);
            if (status != COLI_OK) return status;
            for (uint32_t group = 0; group < group_count; ++group) {
                float scale = read_f32_le(scale_data + group * sizeof(float));
                const uint8_t *packed = bytes + group * group_weight_bytes;
                uint32_t column = (first_group + group) * group_size;
                for (uint32_t within = 0; within < group_size; ++within) {
                    int8_t q = unpack_q4(packed[within / 2],
                                         (within & 1u) != 0);
                    sum += (float)q * scale * input[column + within];
                }
            }
            first_group += group_count;
        }
        if (!have_best || sum > best) {
            best = sum;
            best_row = row;
            have_best = true;
        }
    }
    *out_row = best_row;
    *out_value = best;
    return COLI_OK;
}
