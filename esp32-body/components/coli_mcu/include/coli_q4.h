#pragma once

#include <stddef.h>
#include <stdint.h>

#include "coli_model.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    uint64_t weight_bytes_read;
    uint64_t scale_bytes_read;
    uint32_t storage_reads;
    uint32_t page_boundary_crossings;
    size_t peak_workspace_bytes;
} coli_q4_stats_t;

/**
 * Compute output = Q4(weights) * input with caller-owned fixed workspace.
 *
 * `weights` must be BMOQ_LAYOUT_Q4_ROW_MAJOR. `scales` must contain one
 * float32 scale per row and quantization group. The kernel allocates no heap
 * memory and reads only bounded contiguous group tiles through coli_tensor_read.
 */
coli_status_t coli_q4_matvec(const coli_model_t *model,
                             const bmoq_tensor_t *weights,
                             const bmoq_tensor_t *scales,
                             const float *input, size_t input_count,
                             float *output, size_t output_count,
                             void *workspace, size_t workspace_bytes,
                             coli_q4_stats_t *stats);

coli_status_t coli_q4_dequantize_row(const coli_model_t *model,
                                     const bmoq_tensor_t *weights,
                                     const bmoq_tensor_t *scales,
                                     uint32_t row,
                                     float *output, size_t output_count,
                                     void *workspace, size_t workspace_bytes,
                                     coli_q4_stats_t *stats);

coli_status_t coli_q4_argmax(const coli_model_t *model,
                             const bmoq_tensor_t *weights,
                             const bmoq_tensor_t *scales,
                             const float *input, size_t input_count,
                             void *workspace, size_t workspace_bytes,
                             uint32_t *out_row, float *out_value,
                             coli_q4_stats_t *stats);

#ifdef __cplusplus
}
#endif
