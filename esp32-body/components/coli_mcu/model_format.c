#include "coli_model.h"

#include <stdbool.h>
#include <stdlib.h>
#include <string.h>

#define BMOQ_DIRECTORY_ENTRY_BYTES 64u
#define BMOQ_ENDIAN_MARKER 0x01020304u

static uint16_t read_u16(const uint8_t *p)
{
    return (uint16_t)p[0] | ((uint16_t)p[1] << 8);
}

static uint32_t read_u32(const uint8_t *p)
{
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) |
           ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

static uint64_t read_u64(const uint8_t *p)
{
    return (uint64_t)read_u32(p) | ((uint64_t)read_u32(p + 4) << 32);
}

static bool multiply_u64(uint64_t left, uint64_t right, uint64_t *product)
{
    if (left && right > UINT64_MAX / left) return false;
    *product = left * right;
    return true;
}

static bool validate_known_tensor(const bmoq_tensor_t *tensor)
{
    uint64_t elements = 1;
    for (size_t d = 0; d < 4; ++d) {
        uint32_t dimension = tensor->dimensions[d] ? tensor->dimensions[d] : 1;
        if (!multiply_u64(elements, dimension, &elements)) return false;
    }
    if (tensor->layout == BMOQ_LAYOUT_DENSE_F32) {
        return tensor->dtype == BMOQ_DTYPE_F32 &&
               elements <= UINT64_MAX / sizeof(float) &&
               tensor->byte_length == elements * sizeof(float);
    }
    if (tensor->layout == BMOQ_LAYOUT_Q4_ROW_MAJOR) {
        if (tensor->dtype != BMOQ_DTYPE_Q4_SYM || tensor->dimensions[0] == 0 ||
            tensor->dimensions[1] == 0 || tensor->dimensions[2] != 1 ||
            tensor->dimensions[3] != 1 || tensor->quant_group < 2 ||
            (tensor->quant_group & 1u) != 0 ||
            tensor->dimensions[1] % tensor->quant_group != 0)
            return false;
        return multiply_u64(tensor->dimensions[0], tensor->dimensions[1],
                            &elements) &&
               (elements & 1u) == 0 && tensor->byte_length == elements / 2;
    }
    if (tensor->layout == BMOQ_LAYOUT_GROUP_SCALES_F32) {
        if (tensor->dtype != BMOQ_DTYPE_F32 || tensor->dimensions[0] == 0 ||
            tensor->dimensions[1] == 0 || tensor->dimensions[2] != 1 ||
            tensor->dimensions[3] != 1 || tensor->quant_group < 2 ||
            (tensor->quant_group & 1u) != 0)
            return false;
        return multiply_u64(tensor->dimensions[0], tensor->dimensions[1],
                            &elements) &&
               elements <= UINT64_MAX / sizeof(float) &&
               tensor->byte_length == elements * sizeof(float);
    }
    return true;
}

static void read_model_config(const uint8_t *raw, bmoq_model_config_t *config)
{
    config->arch = read_u32(raw);
    config->flags = read_u32(raw + 4);
    config->hidden_size = read_u32(raw + 8);
    config->intermediate_size = read_u32(raw + 12);
    config->num_hidden_layers = read_u32(raw + 16);
    config->num_attention_heads = read_u32(raw + 20);
    config->num_key_value_heads = read_u32(raw + 24);
    config->num_experts = read_u32(raw + 28);
    config->num_experts_per_tok = read_u32(raw + 32);
    config->vocab_size = read_u32(raw + 36);
    config->max_position_embeddings = read_u32(raw + 40);
    config->rope_theta = read_u32(raw + 44);
    config->eos_token_id = read_u32(raw + 48);
    config->pad_token_id = read_u32(raw + 52);
    config->quant_group = read_u32(raw + 56);
    config->manifest_tensor_id = read_u32(raw + 60);
}

coli_status_t coli_model_open(coli_store_t *store, coli_model_t *model)
{
    if (!store || !model) return COLI_ERR_ARGUMENT;
    memset(model, 0, sizeof(*model));

    uint8_t header[32];
    coli_status_t status = coli_store_read_at(store, 0, header, sizeof(header));
    if (status != COLI_OK) return status;
    if (memcmp(header, "BMOQ", 4) != 0 || read_u16(header + 4) != BMOQ_VERSION ||
        read_u32(header + 8) != BMOQ_ENDIAN_MARKER ||
        read_u32(header + 12) != BMOQ_HEADER_BYTES)
        return COLI_ERR_FORMAT;

    uint32_t count = read_u32(header + 16);
    uint32_t entry_size = read_u32(header + 20);
    uint64_t directory_offset = read_u64(header + 24);
    uint64_t directory_bytes = (uint64_t)count * entry_size;
    if (count > BMOQ_MAX_TENSORS || entry_size != BMOQ_DIRECTORY_ENTRY_BYTES ||
        directory_offset < BMOQ_HEADER_BYTES ||
        directory_offset > coli_store_size(store) ||
        directory_bytes > coli_store_size(store) - directory_offset)
        return COLI_ERR_FORMAT;

    bmoq_tensor_t *tensors = calloc(count, sizeof(*tensors));
    if (count && !tensors) return COLI_ERR_NO_MEMORY;
    uint8_t raw[BMOQ_DIRECTORY_ENTRY_BYTES];
    uint64_t previous_end = directory_offset + directory_bytes;
    for (uint32_t i = 0; i < count; ++i) {
        status = coli_store_read_at(store, directory_offset + (uint64_t)i * entry_size,
                                    raw, sizeof(raw));
        if (status != COLI_OK) goto fail;
        bmoq_tensor_t *tensor = &tensors[i];
        tensor->tensor_id = read_u32(raw);
        tensor->dtype = read_u16(raw + 4);
        tensor->quant_group = read_u16(raw + 6);
        for (size_t d = 0; d < 4; ++d)
            tensor->dimensions[d] = read_u32(raw + 8 + d * 4);
        tensor->data_offset = read_u64(raw + 24);
        tensor->byte_length = read_u64(raw + 32);
        tensor->layout = read_u32(raw + 40);
        tensor->crc32 = read_u32(raw + 44);
        memcpy(tensor->name, raw + 48, sizeof(tensor->name));
        if (tensor->data_offset % BMOQ_DATA_ALIGNMENT != 0 ||
            tensor->data_offset < previous_end ||
            tensor->data_offset > coli_store_size(store) ||
            tensor->byte_length > coli_store_size(store) - tensor->data_offset ||
            !validate_known_tensor(tensor)) {
            status = COLI_ERR_FORMAT;
            goto fail;
        }
        previous_end = tensor->data_offset + tensor->byte_length;
    }
    model->store = store;
    model->tensor_count = count;
    model->tensors = tensors;
    uint8_t config[sizeof(model->config)];
    status = coli_store_read_at(store, BMOQ_MODEL_CONFIG_OFFSET, config,
                                sizeof(config));
    if (status != COLI_OK) goto fail;
    read_model_config(config, &model->config);
    return COLI_OK;

fail:
    free(tensors);
    memset(model, 0, sizeof(*model));
    return status;
}

const bmoq_tensor_t *coli_model_find(const coli_model_t *model,
                                     uint32_t tensor_id)
{
    if (!model) return NULL;
    for (uint32_t i = 0; i < model->tensor_count; ++i)
        if (model->tensors[i].tensor_id == tensor_id) return &model->tensors[i];
    return NULL;
}

coli_status_t coli_tensor_read(const coli_model_t *model,
                               const bmoq_tensor_t *tensor,
                               uint64_t tensor_offset, void *destination,
                               size_t length)
{
    if (!model || !model->store || !tensor) return COLI_ERR_ARGUMENT;
    if (tensor_offset > tensor->byte_length ||
        length > tensor->byte_length - tensor_offset)
        return COLI_ERR_RANGE;
    return coli_store_read_at(model->store, tensor->data_offset + tensor_offset,
                              destination, length);
}

void coli_model_close(coli_model_t *model)
{
    if (!model) return;
    free(model->tensors);
    memset(model, 0, sizeof(*model));
}
