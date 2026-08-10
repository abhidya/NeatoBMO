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

coli_status_t coli_q4_matvec(const coli_model_t *model,
                             const bmoq_tensor_t *weights,
                             const bmoq_tensor_t *scales,
                             const float *input, size_t input_count,
                             float *output, size_t output_count,
                             void *workspace, size_t workspace_bytes,
                             coli_q4_stats_t *stats)
{
    if (!model || !input || !output || !workspace || !stats ||
        !compatible_tensors(weights, scales))
        return COLI_ERR_ARGUMENT;
    const uint32_t rows = weights->dimensions[0];
    const uint32_t columns = weights->dimensions[1];
    const uint32_t group_size = weights->quant_group;
    const uint32_t groups_per_row = columns / group_size;
    const size_t group_weight_bytes = group_size / 2;
    const size_t group_workspace_bytes = group_weight_bytes + sizeof(float);
    if (input_count != columns || output_count != rows ||
        workspace_bytes < group_workspace_bytes)
        return COLI_ERR_RANGE;

    size_t tile_groups = workspace_bytes / group_workspace_bytes;
    if (tile_groups > groups_per_row) tile_groups = groups_per_row;
    if (tile_groups > UINT32_MAX) tile_groups = UINT32_MAX;
    memset(stats, 0, sizeof(*stats));
    stats->peak_workspace_bytes = tile_groups * group_workspace_bytes;
    uint8_t *bytes = workspace;
    const uint64_t row_weight_bytes = (uint64_t)columns / 2;
    const uint64_t row_scale_bytes = (uint64_t)groups_per_row * sizeof(float);

    for (uint32_t row = 0; row < rows; ++row) {
        float sum = 0.0f;
        for (uint32_t first_group = 0; first_group < groups_per_row;) {
            uint32_t remaining = groups_per_row - first_group;
            uint32_t group_count = remaining < tile_groups ? remaining : tile_groups;
            size_t weight_bytes = (size_t)group_count * group_weight_bytes;
            size_t scale_bytes = (size_t)group_count * sizeof(float);
            uint64_t weight_offset = (uint64_t)row * row_weight_bytes +
                                     (uint64_t)first_group * group_weight_bytes;
            coli_status_t status = coli_tensor_read(model, weights, weight_offset,
                                                     bytes, weight_bytes);
            if (status != COLI_OK) return status;
            count_read(stats, weights->data_offset + weight_offset, weight_bytes,
                       false);
            uint8_t *scale_data = bytes + weight_bytes;
            uint64_t scale_offset = (uint64_t)row * row_scale_bytes +
                                    (uint64_t)first_group * sizeof(float);
            status = coli_tensor_read(model, scales, scale_offset, scale_data,
                                      scale_bytes);
            if (status != COLI_OK) return status;
            count_read(stats, scales->data_offset + scale_offset, scale_bytes,
                       true);

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
        output[row] = sum;
    }
    return COLI_OK;
}
