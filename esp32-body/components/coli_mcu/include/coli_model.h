#pragma once

#include <stddef.h>
#include <stdint.h>
#include "coli_store.h"

#ifdef __cplusplus
extern "C" {
#endif

#define BMOQ_HEADER_BYTES 4096u
#define BMOQ_VERSION 1u
#define BMOQ_VERSION_EXTENDED_CONFIG 2u
#define BMOQ_MAX_TENSORS 8192u
#define BMOQ_TENSOR_NAME_BYTES 16u
#define BMOQ_DATA_ALIGNMENT 4096u
#define BMOQ_MODEL_CONFIG_OFFSET 32u
#define BMOQ_CONFIG_TENSOR_ID 0x32474643u
#define BMOQ_CONFIG_HEADER_BYTES 16u
#define BMOQ_CONFIG_ENTRY_BYTES 12u
#define BMOQ_CONFIG_MAX_BYTES (1024u * 1024u)
#define BMOQ_CONFIG_MAX_ENTRIES 256u

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
    BMOQ_MODEL_ARCH_GEMMA3 = 2,
    BMOQ_MODEL_ARCH_GLM52 = 3,
    BMOQ_MODEL_ARCH_INKLING = 4,
    BMOQ_MODEL_ARCH_KIMI_K3 = 5,
    BMOQ_MODEL_ARCH_DEEPSEEK_V4 = 6,
} bmoq_model_arch_t;

typedef enum {
    BMOQ_CONFIG_U32 = 1,
    BMOQ_CONFIG_I32 = 2,
    BMOQ_CONFIG_F32 = 3,
    BMOQ_CONFIG_BOOL = 4,
    BMOQ_CONFIG_U32_ARRAY = 5,
} bmoq_config_type_t;

typedef enum {
    BMOQ_CONFIG_STOP_TOKEN_IDS = 1u,
    BMOQ_CONFIG_RMS_NORM_EPS = 2u,
    BMOQ_CONFIG_ATTENTION_SCALE = 3u,
    BMOQ_CONFIG_ROUTED_SCALE = 4u,
    BMOQ_CONFIG_MOE_INTERMEDIATE_SIZE = 1000u,
    BMOQ_CONFIG_FIRST_DENSE_LAYERS = 1001u,
    BMOQ_CONFIG_Q_LORA_RANK = 1002u,
    BMOQ_CONFIG_KV_LORA_RANK = 1003u,
    BMOQ_CONFIG_QK_NOPE_HEAD_DIM = 1004u,
    BMOQ_CONFIG_QK_ROPE_HEAD_DIM = 1005u,
    BMOQ_CONFIG_V_HEAD_DIM = 1006u,
    BMOQ_CONFIG_SHARED_EXPERTS = 1007u,
    BMOQ_CONFIG_EXPERT_GROUPS = 1008u,
    BMOQ_CONFIG_TOPK_GROUPS = 1009u,
    BMOQ_CONFIG_NORMALIZE_TOPK = 1010u,
    BMOQ_CONFIG_SLIDING_WINDOW = 2000u,
    BMOQ_CONFIG_LAYER_PATTERN = 2001u,
    BMOQ_CONFIG_LINEAR_ATTENTION_PATTERN = 3000u,
    BMOQ_CONFIG_SPARSE_ATTENTION_WINDOW = 4000u,
} bmoq_config_key_t;

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
    uint16_t format_version;
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

/** Read one bounded BMOQ-v2 binary config value without loading the manifest. */
coli_status_t coli_model_config_read(const coli_model_t *model, uint32_t key,
                                     bmoq_config_type_t expected_type,
                                     void *destination,
                                     size_t destination_bytes,
                                     size_t *out_value_count);

#ifdef __cplusplus
}
#endif
