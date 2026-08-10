#pragma once

#include <stddef.h>
#include <stdint.h>
#include "coli_store.h"

#ifdef __cplusplus
extern "C" {
#endif

#define BMOQ_HEADER_BYTES 4096u
#define BMOQ_VERSION 1u
#define BMOQ_MAX_TENSORS 8192u
#define BMOQ_TENSOR_NAME_BYTES 16u
#define BMOQ_DATA_ALIGNMENT 4096u
#define BMOQ_MODEL_CONFIG_OFFSET 32u

typedef enum {
    BMOQ_DTYPE_OPAQUE = 0,
    BMOQ_DTYPE_F32 = 1,
    BMOQ_DTYPE_Q4_SYM = 2,
} bmoq_dtype_t;

typedef enum {
    BMOQ_LAYOUT_OPAQUE = 0,
    /** Two signed 4-bit values per byte, low nibble first, no row padding. */
    BMOQ_LAYOUT_Q4_ROW_MAJOR = 1,
    /** Little-endian float32 scale for every row/group pair. */
    BMOQ_LAYOUT_GROUP_SCALES_F32 = 2,
    /** Dense little-endian float32 values in row-major order. */
    BMOQ_LAYOUT_DENSE_F32 = 3,
} bmoq_layout_t;

typedef enum {
    BMOQ_MODEL_ARCH_GENERIC = 0,
    BMOQ_MODEL_ARCH_OLMOE = 1,
} bmoq_model_arch_t;

typedef struct {
    uint32_t arch;
    uint32_t flags;
    uint32_t hidden_size;
    uint32_t intermediate_size;
    uint32_t num_hidden_layers;
    uint32_t num_attention_heads;
    uint32_t num_key_value_heads;
    uint32_t num_experts;
    uint32_t num_experts_per_tok;
    uint32_t vocab_size;
    uint32_t max_position_embeddings;
    uint32_t rope_theta;
    uint32_t eos_token_id;
    uint32_t pad_token_id;
    uint32_t quant_group;
    uint32_t manifest_tensor_id;
} bmoq_model_config_t;

typedef struct {
    uint32_t tensor_id;
    uint16_t dtype;
    uint16_t quant_group;
    uint32_t dimensions[4];
    uint64_t data_offset;
    uint64_t byte_length;
    uint32_t layout;
    uint32_t crc32;
    char name[BMOQ_TENSOR_NAME_BYTES];
} bmoq_tensor_t;

typedef struct {
    coli_store_t *store;
    uint32_t tensor_count;
    bmoq_tensor_t *tensors;
    bmoq_model_config_t config;
} coli_model_t;

coli_status_t coli_model_open(coli_store_t *store, coli_model_t *model);
const bmoq_tensor_t *coli_model_find(const coli_model_t *model,
                                     uint32_t tensor_id);
coli_status_t coli_tensor_read(const coli_model_t *model,
                               const bmoq_tensor_t *tensor,
                               uint64_t tensor_offset, void *destination,
                               size_t length);
void coli_model_close(coli_model_t *model);

#ifdef __cplusplus
}
#endif
