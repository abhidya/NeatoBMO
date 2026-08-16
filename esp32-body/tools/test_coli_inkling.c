#include <assert.h>
#include <math.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "coli_inkling.h"
#include "coli_store.h"

#define CONFIG_OFFSET 8192u
#define DECODE_HIDDEN 2u
#define DECODE_HEADS 2u
#define DECODE_KV_HEADS 1u
#define DECODE_HEAD_DIM 2u
#define DECODE_QDIM (DECODE_HEADS * DECODE_HEAD_DIM)
#define DECODE_KVDIM (DECODE_KV_HEADS * DECODE_HEAD_DIM)
#define DECODE_D_REL 1u
#define DECODE_VOCAB 4u
#define DECODE_GROUP 2u

typedef struct {
    uint32_t id;
    uint16_t dtype;
    uint16_t group;
    uint32_t dims[4];
    uint32_t layout;
    uint64_t offset;
    uint64_t bytes;
    const char *name;
} decode_entry_t;

static void put_u16(uint8_t *p, uint16_t value)
{
    p[0] = (uint8_t)value;
    p[1] = (uint8_t)(value >> 8);
}

static void put_u32(uint8_t *p, uint32_t value)
{
    for (unsigned i = 0; i < 4; ++i) p[i] = (uint8_t)(value >> (i * 8));
}

static void put_u64(uint8_t *p, uint64_t value)
{
    put_u32(p, (uint32_t)value);
    put_u32(p + 4, (uint32_t)(value >> 32));
}

static uint64_t align_up_u64(uint64_t value)
{
    return (value + BMOQ_DATA_ALIGNMENT - 1u) / BMOQ_DATA_ALIGNMENT *
           BMOQ_DATA_ALIGNMENT;
}

static void write_decode_entry(FILE *file, const decode_entry_t *entry)
{
    uint8_t raw[64] = {0};
    put_u32(raw, entry->id);
    put_u16(raw + 4, entry->dtype);
    put_u16(raw + 6, entry->group);
    put_u32(raw + 8, entry->dims[0]);
    put_u32(raw + 12, entry->dims[1]);
    put_u32(raw + 16, entry->dims[2]);
    put_u32(raw + 20, entry->dims[3]);
    put_u64(raw + 24, entry->offset);
    put_u64(raw + 32, entry->bytes);
    put_u32(raw + 40, entry->layout);
    strncpy((char *)raw + 48, entry->name, BMOQ_TENSOR_NAME_BYTES);
    assert(fwrite(raw, 1, sizeof(raw), file) == sizeof(raw));
}

static void append_decode_dense(decode_entry_t *entries, size_t *count,
                                uint32_t id, uint32_t rows,
                                uint32_t columns, uint64_t *offset,
                                const char *name)
{
    entries[*count] = (decode_entry_t){
        id, BMOQ_DTYPE_F32, 0, {rows, columns, 1, 1},
        BMOQ_LAYOUT_DENSE_F32, *offset,
        (uint64_t)rows * columns * sizeof(float), name};
    *offset = align_up_u64(*offset + entries[*count].bytes);
    ++*count;
}

static void append_decode_q4(decode_entry_t *entries, size_t *count,
                             uint32_t id, uint32_t rows, uint32_t columns,
                             uint64_t *offset, const char *name)
{
    entries[*count] = (decode_entry_t){
        id, BMOQ_DTYPE_Q4_SYM, DECODE_GROUP, {rows, columns, 1, 1},
        BMOQ_LAYOUT_Q4_ROW_MAJOR, *offset, (uint64_t)rows * columns / 2u,
        name};
    *offset = align_up_u64(*offset + entries[*count].bytes);
    ++*count;
    entries[*count] = (decode_entry_t){
        coli_inkling_scale_id(id), BMOQ_DTYPE_F32, DECODE_GROUP,
        {rows, columns / DECODE_GROUP, 1, 1},
        BMOQ_LAYOUT_GROUP_SCALES_F32, *offset,
        (uint64_t)rows * (columns / DECODE_GROUP) * sizeof(float), "scale"};
    *offset = align_up_u64(*offset + entries[*count].bytes);
    ++*count;
}

static void append_decode_q4_bundle(decode_entry_t *entries, size_t *count,
                                    uint32_t id, uint32_t experts,
                                    uint32_t rows, uint32_t columns,
                                    uint64_t *offset, const char *name)
{
    entries[*count] = (decode_entry_t){
        id, BMOQ_DTYPE_Q4_SYM, DECODE_GROUP, {experts, rows, columns, 1},
        BMOQ_LAYOUT_Q4_EXPERT_BUNDLE, *offset,
        (uint64_t)experts * rows * columns / 2u, name};
    *offset = align_up_u64(*offset + entries[*count].bytes);
    ++*count;
    entries[*count] = (decode_entry_t){
        coli_inkling_scale_id(id), BMOQ_DTYPE_F32, DECODE_GROUP,
        {experts, rows, columns / DECODE_GROUP, 1},
        BMOQ_LAYOUT_EXPERT_GROUP_SCALES_F32, *offset,
        (uint64_t)experts * rows * (columns / DECODE_GROUP) * sizeof(float),
        "scale"};
    *offset = align_up_u64(*offset + entries[*count].bytes);
    ++*count;
}

static size_t add_entry(uint8_t *buffer, size_t cursor, uint32_t key,
                        uint16_t type, const void *value, uint16_t count)
{
    const uint32_t bytes = (uint32_t)count * sizeof(uint32_t);
    put_u32(buffer + cursor, key);
    put_u16(buffer + cursor + 4u, type);
    put_u16(buffer + cursor + 6u, count);
    put_u32(buffer + cursor + 8u, bytes);
    memcpy(buffer + cursor + BMOQ_CONFIG_ENTRY_BYTES, value, bytes);
    return cursor + BMOQ_CONFIG_ENTRY_BYTES + bytes;
}

static size_t add_u32(uint8_t *buffer, size_t cursor, uint32_t key,
                      uint32_t value)
{
    return add_entry(buffer, cursor, key, BMOQ_CONFIG_U32, &value, 1);
}

static size_t add_f32(uint8_t *buffer, size_t cursor, uint32_t key,
                      float value)
{
    return add_entry(buffer, cursor, key, BMOQ_CONFIG_F32, &value, 1);
}

static void write_f32_value(FILE *file, float value)
{
    assert(fwrite(&value, 1, sizeof(value), file) == sizeof(value));
}

static void write_decode_dense(FILE *file, const decode_entry_t *entry)
{
    assert(fseeko(file, (off_t)entry->offset, SEEK_SET) == 0);
    for (uint32_t row = 0; row < entry->dims[0]; ++row) {
        for (uint32_t column = 0; column < entry->dims[1]; ++column) {
            float value = 0.0f;
            if (strstr(entry->name, "norm") || strstr(entry->name, "scale"))
                value = 1.0f;
            else if (strstr(entry->name, "conv"))
                value = (column & 1u) ? 1.0f : 0.0f;
            else if (strstr(entry->name, "router"))
                value = row == column ? 1.0f : 0.25f;
            else if (strstr(entry->name, "rbias"))
                value = row == 0 ? 1.0f : 0.0f;
            else if (strstr(entry->name, "relp"))
                value = 0.0f;
            write_f32_value(file, value);
        }
    }
}

static void write_decode_q4(FILE *file, const decode_entry_t *entry)
{
    assert(fseeko(file, (off_t)entry->offset, SEEK_SET) == 0);
    const uint32_t rows = entry->layout == BMOQ_LAYOUT_Q4_EXPERT_BUNDLE
                              ? entry->dims[0] * entry->dims[1]
                              : entry->dims[0];
    const uint32_t columns = entry->layout == BMOQ_LAYOUT_Q4_EXPERT_BUNDLE
                                 ? entry->dims[2]
                                 : entry->dims[1];
    for (uint32_t row = 0; row < rows; ++row) {
        for (uint32_t column = 0; column < columns; column += 2u) {
            uint8_t low = (row % columns) == column ? 7u : 0u;
            uint8_t high = (row % columns) == column + 1u ? 7u : 0u;
            uint8_t packed = (uint8_t)(low | (uint8_t)(high << 4));
            assert(fwrite(&packed, 1, sizeof(packed), file) == sizeof(packed));
        }
    }
}

static void write_decode_scales(FILE *file, const decode_entry_t *entry)
{
    assert(fseeko(file, (off_t)entry->offset, SEEK_SET) == 0);
    uint64_t count = entry->bytes / sizeof(float);
    for (uint64_t i = 0; i < count; ++i) write_f32_value(file, 1.0f / 7.0f);
}

static size_t build_decode_config(uint8_t *config, size_t config_bytes)
{
    memset(config, 0, config_bytes);
    memcpy(config, "BCFG", 4);
    put_u16(config + 4, 1);
    put_u16(config + 6, BMOQ_CONFIG_HEADER_BYTES);
    put_u32(config + 8, 21);
    put_u32(config + 12, (uint32_t)config_bytes);
    size_t cursor = BMOQ_CONFIG_HEADER_BYTES;
    cursor = add_u32(config, cursor, BMOQ_CONFIG_UNPADDED_VOCAB_SIZE,
                     DECODE_VOCAB);
    cursor = add_u32(config, cursor, BMOQ_CONFIG_HEAD_DIM, DECODE_HEAD_DIM);
    cursor = add_u32(config, cursor, BMOQ_CONFIG_SWA_NUM_ATTENTION_HEADS,
                     DECODE_HEADS);
    cursor = add_u32(config, cursor, BMOQ_CONFIG_SWA_NUM_KEY_VALUE_HEADS,
                     DECODE_KV_HEADS);
    cursor = add_u32(config, cursor, BMOQ_CONFIG_SWA_HEAD_DIM,
                     DECODE_HEAD_DIM);
    cursor = add_u32(config, cursor, BMOQ_CONFIG_SLIDING_WINDOW, 2);
    cursor = add_u32(config, cursor, BMOQ_CONFIG_D_REL, DECODE_D_REL);
    cursor = add_u32(config, cursor, BMOQ_CONFIG_REL_EXTENT, 3);
    cursor = add_u32(config, cursor, BMOQ_CONFIG_CONV_KERNEL, 2);
    cursor = add_u32(config, cursor, BMOQ_CONFIG_MOE_INTERMEDIATE_SIZE, 2);
    cursor = add_u32(config, cursor, BMOQ_CONFIG_DENSE_INTERMEDIATE_SIZE, 2);
    cursor = add_u32(config, cursor, BMOQ_CONFIG_DENSE_MLP_INDEX, 1);
    cursor = add_u32(config, cursor, BMOQ_CONFIG_SHARED_EXPERTS_INKLING, 1);
    cursor = add_f32(config, cursor, BMOQ_CONFIG_RMS_NORM_EPS, 1.0e-6f);
    cursor = add_f32(config, cursor, BMOQ_CONFIG_ROUTED_SCALE, 1.0f);
    cursor = add_f32(config, cursor,
                     BMOQ_CONFIG_LOGITS_MUP_WIDTH_MULTIPLIER, 1.0f);
    cursor = add_u32(config, cursor, BMOQ_CONFIG_LOG_SCALING_N_FLOOR, 2);
    cursor = add_f32(config, cursor, BMOQ_CONFIG_LOG_SCALING_ALPHA, 0.0f);
    uint32_t stops[] = {3};
    cursor = add_entry(config, cursor, BMOQ_CONFIG_STOP_TOKEN_IDS,
                       BMOQ_CONFIG_U32_ARRAY, stops, 1);
    uint32_t local_layers[] = {1};
    cursor = add_entry(config, cursor, BMOQ_CONFIG_LOCAL_LAYER_IDS,
                       BMOQ_CONFIG_U32_ARRAY, local_layers, 1);
    uint32_t sparse_layers[] = {1};
    cursor = add_entry(config, cursor, BMOQ_CONFIG_SPARSE_LAYER_IDS,
                       BMOQ_CONFIG_U32_ARRAY, sparse_layers, 1);
    return cursor;
}

static void write_config_tensor_entry(FILE *file, size_t config_bytes)
{
    uint8_t tensor[64] = {0};
    put_u32(tensor, BMOQ_CONFIG_TENSOR_ID);
    put_u16(tensor + 4, BMOQ_DTYPE_OPAQUE);
    put_u32(tensor + 8, (uint32_t)config_bytes);
    put_u32(tensor + 12, 1);
    put_u32(tensor + 16, 1);
    put_u32(tensor + 20, 1);
    put_u64(tensor + 24, CONFIG_OFFSET);
    put_u64(tensor + 32, config_bytes);
    put_u32(tensor + 40, BMOQ_LAYOUT_OPAQUE);
    memcpy(tensor + 48, "config.v2", 9);
    assert(fwrite(tensor, 1, sizeof(tensor), file) == sizeof(tensor));
}

static void write_fixture(const char *path)
{
    uint8_t config[352] = {0};
    memcpy(config, "BCFG", 4);
    put_u16(config + 4, 1);
    put_u16(config + 6, BMOQ_CONFIG_HEADER_BYTES);
    put_u32(config + 8, 21);
    put_u32(config + 12, sizeof(config));
    size_t cursor = BMOQ_CONFIG_HEADER_BYTES;
    cursor = add_u32(config, cursor, BMOQ_CONFIG_UNPADDED_VOCAB_SIZE, 8);
    cursor = add_u32(config, cursor, BMOQ_CONFIG_HEAD_DIM, 2);
    cursor = add_u32(config, cursor, BMOQ_CONFIG_SWA_NUM_ATTENTION_HEADS, 2);
    cursor = add_u32(config, cursor, BMOQ_CONFIG_SWA_NUM_KEY_VALUE_HEADS, 1);
    cursor = add_u32(config, cursor, BMOQ_CONFIG_SWA_HEAD_DIM, 2);
    cursor = add_u32(config, cursor, BMOQ_CONFIG_SLIDING_WINDOW, 2);
    cursor = add_u32(config, cursor, BMOQ_CONFIG_D_REL, 2);
    cursor = add_u32(config, cursor, BMOQ_CONFIG_REL_EXTENT, 4);
    cursor = add_u32(config, cursor, BMOQ_CONFIG_CONV_KERNEL, 3);
    cursor = add_u32(config, cursor, BMOQ_CONFIG_MOE_INTERMEDIATE_SIZE, 2);
    cursor = add_u32(config, cursor, BMOQ_CONFIG_DENSE_INTERMEDIATE_SIZE, 3);
    cursor = add_u32(config, cursor, BMOQ_CONFIG_DENSE_MLP_INDEX, 1);
    cursor = add_u32(config, cursor, BMOQ_CONFIG_SHARED_EXPERTS_INKLING, 1);
    cursor = add_f32(config, cursor, BMOQ_CONFIG_RMS_NORM_EPS, 1.0e-6f);
    cursor = add_f32(config, cursor, BMOQ_CONFIG_ROUTED_SCALE, 2.0f);
    cursor = add_f32(config, cursor,
                     BMOQ_CONFIG_LOGITS_MUP_WIDTH_MULTIPLIER, 4.0f);
    cursor = add_u32(config, cursor, BMOQ_CONFIG_LOG_SCALING_N_FLOOR, 2);
    cursor = add_f32(config, cursor, BMOQ_CONFIG_LOG_SCALING_ALPHA, 0.5f);
    uint32_t stops[] = {6};
    cursor = add_entry(config, cursor, BMOQ_CONFIG_STOP_TOKEN_IDS,
                       BMOQ_CONFIG_U32_ARRAY, stops, 1);
    uint32_t local_layers[] = {1};
    cursor = add_entry(config, cursor, BMOQ_CONFIG_LOCAL_LAYER_IDS,
                       BMOQ_CONFIG_U32_ARRAY, local_layers, 1);
    uint32_t sparse_layers[] = {1};
    cursor = add_entry(config, cursor, BMOQ_CONFIG_SPARSE_LAYER_IDS,
                       BMOQ_CONFIG_U32_ARRAY, sparse_layers, 1);
    assert(cursor == sizeof(config));

    FILE *file = fopen(path, "wb");
    assert(file);
    uint8_t header[BMOQ_HEADER_BYTES] = {0};
    memcpy(header, "BMOQ", 4);
    put_u16(header + 4, BMOQ_VERSION_EXTENDED_CONFIG);
    put_u32(header + 8, 0x01020304u);
    put_u32(header + 12, BMOQ_HEADER_BYTES);
    put_u32(header + 16, 1);
    put_u32(header + 20, 64);
    put_u64(header + 24, BMOQ_HEADER_BYTES);
    uint8_t *fixed = header + BMOQ_MODEL_CONFIG_OFFSET;
    put_u32(fixed, BMOQ_MODEL_ARCH_INKLING);
    put_u32(fixed + 8, 4);
    put_u32(fixed + 12, 3);
    put_u32(fixed + 16, 2);
    put_u32(fixed + 20, 2);
    put_u32(fixed + 24, 1);
    put_u32(fixed + 28, 4);
    put_u32(fixed + 32, 2);
    put_u32(fixed + 36, 10);
    put_u32(fixed + 40, 512);
    assert(fwrite(header, 1, sizeof(header), file) == sizeof(header));
    write_config_tensor_entry(file, sizeof(config));
    assert(fseeko(file, CONFIG_OFFSET, SEEK_SET) == 0);
    assert(fwrite(config, 1, sizeof(config), file) == sizeof(config));
    assert(fclose(file) == 0);
}

static void write_fixture_without_layer_lists(const char *path)
{
    uint8_t config[320] = {0};
    memcpy(config, "BCFG", 4);
    put_u16(config + 4, 1);
    put_u16(config + 6, BMOQ_CONFIG_HEADER_BYTES);
    put_u32(config + 8, 19);
    put_u32(config + 12, sizeof(config));
    size_t cursor = BMOQ_CONFIG_HEADER_BYTES;
    cursor = add_u32(config, cursor, BMOQ_CONFIG_UNPADDED_VOCAB_SIZE, 8);
    cursor = add_u32(config, cursor, BMOQ_CONFIG_HEAD_DIM, 2);
    cursor = add_u32(config, cursor, BMOQ_CONFIG_SWA_NUM_ATTENTION_HEADS, 2);
    cursor = add_u32(config, cursor, BMOQ_CONFIG_SWA_NUM_KEY_VALUE_HEADS, 1);
    cursor = add_u32(config, cursor, BMOQ_CONFIG_SWA_HEAD_DIM, 2);
    cursor = add_u32(config, cursor, BMOQ_CONFIG_SLIDING_WINDOW, 2);
    cursor = add_u32(config, cursor, BMOQ_CONFIG_D_REL, 2);
    cursor = add_u32(config, cursor, BMOQ_CONFIG_REL_EXTENT, 4);
    cursor = add_u32(config, cursor, BMOQ_CONFIG_CONV_KERNEL, 3);
    cursor = add_u32(config, cursor, BMOQ_CONFIG_MOE_INTERMEDIATE_SIZE, 2);
    cursor = add_u32(config, cursor, BMOQ_CONFIG_DENSE_INTERMEDIATE_SIZE, 3);
    cursor = add_u32(config, cursor, BMOQ_CONFIG_DENSE_MLP_INDEX, 1);
    cursor = add_u32(config, cursor, BMOQ_CONFIG_SHARED_EXPERTS_INKLING, 1);
    cursor = add_f32(config, cursor, BMOQ_CONFIG_RMS_NORM_EPS, 1.0e-6f);
    cursor = add_f32(config, cursor, BMOQ_CONFIG_ROUTED_SCALE, 2.0f);
    cursor = add_f32(config, cursor,
                     BMOQ_CONFIG_LOGITS_MUP_WIDTH_MULTIPLIER, 4.0f);
    cursor = add_u32(config, cursor, BMOQ_CONFIG_LOG_SCALING_N_FLOOR, 2);
    cursor = add_f32(config, cursor, BMOQ_CONFIG_LOG_SCALING_ALPHA, 0.5f);
    uint32_t stops[] = {6};
    cursor = add_entry(config, cursor, BMOQ_CONFIG_STOP_TOKEN_IDS,
                       BMOQ_CONFIG_U32_ARRAY, stops, 1);
    assert(cursor == sizeof(config));

    FILE *file = fopen(path, "wb");
    assert(file);
    uint8_t header[BMOQ_HEADER_BYTES] = {0};
    memcpy(header, "BMOQ", 4);
    put_u16(header + 4, BMOQ_VERSION_EXTENDED_CONFIG);
    put_u32(header + 8, 0x01020304u);
    put_u32(header + 12, BMOQ_HEADER_BYTES);
    put_u32(header + 16, 1);
    put_u32(header + 20, 64);
    put_u64(header + 24, BMOQ_HEADER_BYTES);
    uint8_t *fixed = header + BMOQ_MODEL_CONFIG_OFFSET;
    put_u32(fixed, BMOQ_MODEL_ARCH_INKLING);
    put_u32(fixed + 8, 4);
    put_u32(fixed + 12, 3);
    put_u32(fixed + 16, 2);
    put_u32(fixed + 20, 2);
    put_u32(fixed + 24, 1);
    put_u32(fixed + 28, 4);
    put_u32(fixed + 32, 2);
    put_u32(fixed + 36, 10);
    put_u32(fixed + 40, 512);
    assert(fwrite(header, 1, sizeof(header), file) == sizeof(header));
    write_config_tensor_entry(file, sizeof(config));
    assert(fseeko(file, CONFIG_OFFSET, SEEK_SET) == 0);
    assert(fwrite(config, 1, sizeof(config), file) == sizeof(config));
    assert(fclose(file) == 0);
}

static void append_decode_layer(decode_entry_t *entries, size_t *count,
                                uint32_t layer, bool sparse,
                                uint64_t *offset)
{
    append_decode_dense(entries, count, coli_inkling_input_norm_id(layer),
                        DECODE_HIDDEN, 1, offset, "in_norm");
    append_decode_dense(entries, count,
                        coli_inkling_post_attention_norm_id(layer),
                        DECODE_HIDDEN, 1, offset, "postnorm");
    append_decode_q4(entries, count, coli_inkling_q_proj_id(layer),
                     DECODE_QDIM, DECODE_HIDDEN, offset, "q");
    append_decode_q4(entries, count, coli_inkling_k_proj_id(layer),
                     DECODE_KVDIM, DECODE_HIDDEN, offset, "k");
    append_decode_q4(entries, count, coli_inkling_v_proj_id(layer),
                     DECODE_KVDIM, DECODE_HIDDEN, offset, "v");
    append_decode_q4(entries, count, coli_inkling_r_proj_id(layer),
                     DECODE_HEADS * DECODE_D_REL,
                     DECODE_HIDDEN, offset, "r");
    append_decode_q4(entries, count, coli_inkling_o_proj_id(layer),
                     DECODE_HIDDEN, DECODE_QDIM, offset, "o");
    append_decode_dense(entries, count, coli_inkling_q_norm_id(layer),
                        DECODE_HEAD_DIM, 1, offset, "qnorm");
    append_decode_dense(entries, count, coli_inkling_k_norm_id(layer),
                        DECODE_HEAD_DIM, 1, offset, "knorm");
    append_decode_dense(entries, count, coli_inkling_rel_proj_id(layer), 1,
                        sparse ? 2 : 3, offset, "relp");
    append_decode_dense(entries, count, coli_inkling_k_conv_id(layer),
                        DECODE_KVDIM, 2, offset, "kconv");
    append_decode_dense(entries, count, coli_inkling_v_conv_id(layer),
                        DECODE_KVDIM, 2, offset, "vconv");
    append_decode_dense(entries, count, coli_inkling_attn_conv_id(layer),
                        DECODE_HIDDEN, 2, offset, "aconv");
    append_decode_dense(entries, count, coli_inkling_mlp_conv_id(layer),
                        DECODE_HIDDEN, 2, offset, "mconv");
    if (!sparse) {
        append_decode_q4(entries, count, coli_inkling_dense_gate_id(layer), 2,
                         DECODE_HIDDEN, offset, "dgate");
        append_decode_q4(entries, count, coli_inkling_dense_up_id(layer), 2,
                         DECODE_HIDDEN, offset, "dup");
        append_decode_q4(entries, count, coli_inkling_dense_down_id(layer),
                         DECODE_HIDDEN, 2, offset, "ddown");
        append_decode_dense(entries, count, coli_inkling_dense_scale_id(layer),
                            1, 1, offset, "dscale");
    } else {
        append_decode_dense(entries, count, coli_inkling_router_id(layer), 3,
                            DECODE_HIDDEN, offset, "router");
        append_decode_dense(entries, count,
                            coli_inkling_router_bias_id(layer), 2, 1, offset,
                            "rbias");
        append_decode_dense(entries, count,
                            coli_inkling_router_scale_id(layer), 1, 1, offset,
                            "rscale");
        append_decode_q4(entries, count, coli_inkling_shared_gate_id(layer), 2,
                         DECODE_HIDDEN, offset, "shgate");
        append_decode_q4(entries, count, coli_inkling_shared_up_id(layer), 2,
                         DECODE_HIDDEN, offset, "shup");
        append_decode_q4(entries, count, coli_inkling_shared_down_id(layer),
                         DECODE_HIDDEN, 2, offset, "shdown");
        append_decode_q4_bundle(entries, count,
                                coli_inkling_routed_gate_bundle_id(layer), 2,
                                2, DECODE_HIDDEN, offset, "egate");
        append_decode_q4_bundle(entries, count,
                                coli_inkling_routed_up_bundle_id(layer), 2, 2,
                                DECODE_HIDDEN, offset, "eup");
        append_decode_q4_bundle(entries, count,
                                coli_inkling_routed_down_bundle_id(layer), 2,
                                DECODE_HIDDEN, 2, offset, "edown");
    }
}

static void write_decode_fixture(const char *path)
{
    uint8_t config[352];
    const size_t config_bytes = build_decode_config(config, sizeof(config));
    assert(config_bytes == sizeof(config));
    decode_entry_t entries[128];
    size_t count = 0;
    uint64_t offset = align_up_u64(BMOQ_HEADER_BYTES + sizeof(entries));
    entries[count] = (decode_entry_t){
        BMOQ_CONFIG_TENSOR_ID, BMOQ_DTYPE_OPAQUE, 0, {config_bytes, 1, 1, 1},
        BMOQ_LAYOUT_OPAQUE, offset, config_bytes, "config.v2"};
    offset = align_up_u64(offset + config_bytes);
    ++count;
    append_decode_q4(entries, &count, COLI_INKLING_TENSOR_EMBED_TOKENS,
                     DECODE_VOCAB, DECODE_HIDDEN, &offset, "tok_emb");
    append_decode_dense(entries, &count, COLI_INKLING_TENSOR_EMBED_NORM,
                        DECODE_HIDDEN, 1, &offset, "emb_norm");
    append_decode_dense(entries, &count, COLI_INKLING_TENSOR_FINAL_NORM,
                        DECODE_HIDDEN, 1, &offset, "norm");
    append_decode_q4(entries, &count, COLI_INKLING_TENSOR_LM_HEAD,
                     DECODE_VOCAB, DECODE_HIDDEN, &offset, "lm_head");
    append_decode_layer(entries, &count, 0, false, &offset);
    append_decode_layer(entries, &count, 1, true, &offset);

    FILE *file = fopen(path, "wb");
    assert(file);
    uint8_t header[BMOQ_HEADER_BYTES] = {0};
    memcpy(header, "BMOQ", 4);
    put_u16(header + 4, BMOQ_VERSION_EXTENDED_CONFIG);
    put_u32(header + 8, 0x01020304u);
    put_u32(header + 12, BMOQ_HEADER_BYTES);
    put_u32(header + 16, (uint32_t)count);
    put_u32(header + 20, 64);
    put_u64(header + 24, BMOQ_HEADER_BYTES);
    uint8_t *fixed = header + BMOQ_MODEL_CONFIG_OFFSET;
    put_u32(fixed, BMOQ_MODEL_ARCH_INKLING);
    put_u32(fixed + 8, DECODE_HIDDEN);
    put_u32(fixed + 12, 2);
    put_u32(fixed + 16, 2);
    put_u32(fixed + 20, DECODE_HEADS);
    put_u32(fixed + 24, DECODE_KV_HEADS);
    put_u32(fixed + 28, 2);
    put_u32(fixed + 32, 1);
    put_u32(fixed + 36, DECODE_VOCAB);
    put_u32(fixed + 40, 4);
    put_u32(fixed + 48, 3);
    put_u32(fixed + 56, DECODE_GROUP);
    assert(fwrite(header, 1, sizeof(header), file) == sizeof(header));
    for (size_t i = 0; i < count; ++i) write_decode_entry(file, &entries[i]);
    for (size_t i = 0; i < count; ++i) {
        if (entries[i].id == BMOQ_CONFIG_TENSOR_ID) {
            assert(fseeko(file, (off_t)entries[i].offset, SEEK_SET) == 0);
            assert(fwrite(config, 1, sizeof(config), file) == sizeof(config));
        } else if (entries[i].layout == BMOQ_LAYOUT_DENSE_F32) {
            write_decode_dense(file, &entries[i]);
        } else if (entries[i].layout == BMOQ_LAYOUT_Q4_ROW_MAJOR ||
                   entries[i].layout == BMOQ_LAYOUT_Q4_EXPERT_BUNDLE) {
            write_decode_q4(file, &entries[i]);
        } else if (entries[i].layout == BMOQ_LAYOUT_GROUP_SCALES_F32 ||
                   entries[i].layout ==
                       BMOQ_LAYOUT_EXPERT_GROUP_SCALES_F32) {
            write_decode_scales(file, &entries[i]);
        }
    }
    assert(fclose(file) == 0);
}

static void open_fixture(const char *path, coli_store_t **store,
                         coli_model_t *model)
{
    assert(coli_store_open_file(path, store) == COLI_OK);
    assert(coli_model_open(*store, model) == COLI_OK);
}

static void assert_close(float actual, float expected)
{
    if (fabsf(actual - expected) >= 1.0e-5f) {
        fprintf(stderr, "assert_close failed: actual=%f expected=%f\n",
                actual, expected);
        assert(fabsf(actual - expected) < 1.0e-5f);
    }
}

static void reference_attention(const float *query, const float *keys,
                                const float *values, uint32_t heads,
                                uint32_t kv_heads, uint32_t head_dim,
                                uint32_t token_count, uint32_t start,
                                const float *bias, float tau, float *output)
{
    const float scale = 1.0f / (float)head_dim;
    for (uint32_t head = 0; head < heads; ++head) {
        const uint32_t kv_head = head / (heads / kv_heads);
        float scores[4];
        float max_score = -1.0e30f;
        uint32_t used = 0;
        for (uint32_t token = start; token < token_count; ++token) {
            float score = 0.0f;
            const float *q = query + (size_t)head * head_dim;
            const float *k = keys + (size_t)token * kv_heads * head_dim +
                             (size_t)kv_head * head_dim;
            for (uint32_t d = 0; d < head_dim; ++d) score += q[d] * k[d];
            score = (score * scale + bias[token_count - 1u - token]) * tau;
            scores[used++] = score;
            if (score > max_score) max_score = score;
        }
        float sum = 0.0f;
        for (uint32_t i = 0; i < used; ++i) {
            scores[i] = expf(scores[i] - max_score);
            sum += scores[i];
        }
        float *out = output + (size_t)head * head_dim;
        memset(out, 0, head_dim * sizeof(*out));
        used = 0;
        for (uint32_t token = start; token < token_count; ++token) {
            const float weight = scores[used++] / sum;
            const float *v = values + (size_t)token * kv_heads * head_dim +
                             (size_t)kv_head * head_dim;
            for (uint32_t d = 0; d < head_dim; ++d) out[d] += weight * v[d];
        }
    }
}

static void test_config_and_state(const coli_inkling_config_t *config)
{
    assert(config->hidden_size == 4);
    assert(config->sliding_window == 2);
    assert(config->max_context_tokens == 512);
    assert(config->shared_experts == 1);
    assert(config->moe_intermediate_size == 2);
    assert(config->dense_intermediate_size == 3);
    assert(config->local_layer_count == 1);
    assert(config->sparse_layer_count == 1);
    assert(coli_inkling_is_stop_token(config, 6));
    assert(!coli_inkling_is_stop_token(config, 5));
    assert_close(coli_inkling_global_tau(config, 2), 1.0f);
    assert(coli_inkling_global_tau(config, 4) > 1.0f);
    coli_kv_cache_layout_t layout;
    assert(coli_inkling_state_layout(config, &layout) == COLI_OK);
    assert(layout.total_bytes > 0);
    assert(layout.max_tokens == 512);
    assert(layout.variable_layer_capacities == 1);
    assert(layout.layer_token_capacities[0] == 512);
    assert(layout.layer_token_capacities[1] == 2);
    assert(layout.total_bytes == 8224u);
}

static void test_production_shape_state_is_packed(void)
{
    coli_inkling_config_t config;
    memset(&config, 0, sizeof(config));
    config.hidden_size = 6144;
    config.num_layers = 66;
    config.vocab_size = 201024;
    config.unpadded_vocab_size = 200064;
    config.num_heads = 64;
    config.num_key_value_heads = 8;
    config.head_dim = 128;
    config.swa_num_heads = 64;
    config.swa_num_key_value_heads = 16;
    config.swa_head_dim = 128;
    config.sliding_window = 512;
    config.d_rel = 16;
    config.rel_extent = 1024;
    config.conv_kernel = 4;
    config.num_experts = 256;
    config.experts_per_token = 6;
    config.shared_experts = 2;
    config.moe_intermediate_size = 3072;
    config.dense_intermediate_size = 24576;
    config.dense_mlp_index = 1;
    config.max_context_tokens = 1048576;
    config.rms_norm_epsilon = 1.0e-6f;
    config.route_scale = 8.0f;
    config.logits_mup_width_multiplier = 24.0f;
    for (uint32_t layer = 0; layer < config.num_layers; ++layer) {
        if (layer % 6u != 5u)
            config.local_layer_ids[config.local_layer_count++] = layer;
        if (layer >= 2u)
            config.sparse_layer_ids[config.sparse_layer_count++] = layer;
    }

    coli_kv_cache_layout_t layout;
    assert(coli_inkling_state_layout(&config, &layout) == COLI_OK);
    const uint64_t local_token_bytes = (uint64_t)16u * 128u * sizeof(float);
    const uint64_t global_token_bytes = (uint64_t)8u * 128u * sizeof(float);
    const uint64_t local_layers = 55u;
    const uint64_t global_layers = 11u;
    const uint64_t expected =
        local_layers * 512u * local_token_bytes * 2u +
        global_layers * 1048576u * global_token_bytes * 2u;
    const uint64_t uniform =
        66ull * 1048576ull * local_token_bytes * 2ull;
    assert(layout.total_bytes == expected);
    assert(layout.total_bytes < uniform / 5u);
    assert(layout.layer_token_capacities[0] == 512);
    assert(layout.layer_token_capacities[5] == 1048576);
    assert(layout.layer_key_token_bytes[0] == local_token_bytes);
    assert(layout.layer_key_token_bytes[5] == global_token_bytes);
    size_t workspace_start =
        coli_inkling_decode_required_workspace(&config, 0, 4096);
    size_t workspace_long = coli_inkling_decode_required_workspace(
        &config, config.max_context_tokens - 1u, 4096);
    assert(workspace_start > 0);
    assert(workspace_start == workspace_long);
}

static void test_attention(const coli_inkling_config_t *config, bool file_backed)
{
    coli_kv_cache_layout_t layout;
    assert(coli_inkling_state_layout(config, &layout) == COLI_OK);
    coli_kv_cache_t *cache = NULL;
    uint8_t *memory = NULL;
    char path[] = "/tmp/coli_inkling_kv_XXXXXX";
    int fd = -1;
    if (file_backed) {
        fd = mkstemp(path);
        assert(fd >= 0);
        close(fd);
        assert(coli_kv_cache_open_file(&layout, path, 64, &cache) ==
               COLI_OK);
    } else {
        memory = calloc(1, (size_t)layout.total_bytes);
        assert(memory);
        assert(coli_kv_cache_open_ram(&layout, memory,
                                      (size_t)layout.total_bytes, &cache) ==
               COLI_OK);
    }

    const float query[] = {0.5f, -0.25f, 0.75f, 0.125f};
    const float keys[] = {1.0f, 0.0f, 0.0f, 1.0f, -0.5f, 0.75f};
    const float values[] = {0.25f, 1.0f, 2.0f, -1.0f, 0.5f, 0.5f};
    const float bias[] = {0.0f, 0.1f, -0.2f, 0.0f};
    float output[4] = {0};
    float expected[4] = {0};
    float scores[512];
    float key_scratch[2];
    float value_scratch[2];

    for (uint32_t pos = 0; pos < 3; ++pos) {
        assert(coli_inkling_attention_decode(
                   config, cache, 0, false, pos, query, keys + pos * 2,
                   values + pos * 2, bias, 4, output, 4, scores, 512,
                   key_scratch, 2, value_scratch, 2) == COLI_OK);
    }
    reference_attention(query, keys, values, 2, 1, 2, 3, 0, bias,
                        coli_inkling_global_tau(config, 3), expected);
    for (size_t i = 0; i < 4; ++i) assert_close(output[i], expected[i]);

    for (uint32_t pos = 0; pos < 3; ++pos) {
        assert(coli_inkling_attention_decode(
                   config, cache, 1, true, pos, query, keys + pos * 2,
                   values + pos * 2, bias, 2, output, 4, scores, 512,
                   key_scratch, 2, value_scratch, 2) == COLI_OK);
    }
    reference_attention(query, keys, values, 2, 1, 2, 3, 1, bias, 1.0f,
                        expected);
    for (size_t i = 0; i < 4; ++i) assert_close(output[i], expected[i]);

    coli_kv_cache_stats_t stats;
    coli_kv_cache_stats(cache, &stats);
    assert(stats.write_count > 0);
    if (file_backed) assert(stats.resident_bytes == 64);
    coli_kv_cache_close(cache);
    free(memory);
    if (file_backed) assert(remove(path) == 0);
}

static void test_conv_norm_mlp_and_logits(void)
{
    const float input[] = {1.0f, -2.0f};
    const float weights[] = {0.5f, 0.25f, -0.125f, -1.0f, 0.5f, 0.25f};
    float state[4] = {2.0f, -1.0f, 1.0f, 0.5f};
    float output[2] = {0};
    assert(coli_inkling_short_conv_step(input, weights, 2, 3, state, 4,
                                        output, 2) == COLI_OK);
    assert_close(output[0], 1.625f);
    assert_close(output[1], -3.25f);
    assert_close(state[0], -1.0f);
    assert_close(state[1], 1.0f);
    assert_close(state[2], 0.5f);
    assert_close(state[3], -2.0f);

    float heads[] = {3.0f, 4.0f, 1.0f, 2.0f};
    const float norm[] = {1.0f, 2.0f};
    assert(coli_inkling_qk_rmsnorm(heads, norm, 2, 2, 1.0e-6f) ==
           COLI_OK);
    assert(heads[1] > heads[0]);

    const float gate[] = {1.0f, -0.5f, 0.25f};
    const float up[] = {2.0f, 1.0f, -1.0f};
    const float down[] = {1.0f, 0.5f, -0.25f, -1.0f, 0.25f, 0.5f};
    assert(coli_inkling_dense_swiglu(gate, up, down, 2, 3, 2.0f, output,
                                     2) == COLI_OK);
    assert(output[0] != 0.0f);
    assert(output[1] != 0.0f);

    const float hidden[] = {1.0f, 2.0f};
    const float lm_head[] = {0.0f, 1.0f, 3.0f, 0.0f, -100.0f, 100.0f};
    uint32_t token = 99;
    float logit = 0.0f;
    assert(coli_inkling_logits_argmax(hidden, lm_head, 3, 2, 2, 2.0f,
                                      &token, &logit) == COLI_OK);
    assert(token == 1);
    assert_close(logit, 1.5f);
}

static coli_status_t scripted_next_token(void *context,
                                         uint32_t previous_token_id,
                                         size_t position,
                                         uint32_t *out_token_id)
{
    (void)context;
    (void)position;
    *out_token_id = previous_token_id + 1u;
    return COLI_OK;
}

static void test_moe_and_generation(const coli_inkling_config_t *base)
{
    coli_inkling_config_t config = *base;
    config.num_experts = 4;
    config.experts_per_token = 2;
    config.shared_experts = 1;
    config.hidden_size = 2;
    config.route_scale = 2.0f;
    const float logits[] = {-2.0f, 1.0f, 0.5f, 4.0f, 0.25f};
    const float bias[] = {0.0f, 0.0f, 1.0f, -1.0f};
    coli_inkling_moe_stats_t stats;
    assert(coli_inkling_moe_route(&config, logits, bias, 0.5f, &stats) ==
           COLI_OK);
    assert(stats.selected_count == 2);
    assert(stats.selected_experts[0] == 2);
    assert(stats.selected_experts[1] == 1);
    const float experts[] = {1.0f, 0.0f, 0.0f, 2.0f};
    const float shared[] = {0.5f, 0.5f};
    float output[2] = {0};
    assert(coli_inkling_moe_combine(&config, &stats, experts, 4, shared, 2,
                                    output, 2) == COLI_OK);
    assert(output[0] > 0.0f);
    assert(output[1] > 0.0f);

    uint32_t generated[8] = {0};
    size_t count = 0;
    assert(coli_inkling_generate_greedy_stream(base, 3, 0, 8, generated, 8,
                                              &count, scripted_next_token,
                                              NULL, NULL, NULL) ==
           COLI_OK);
    assert(count == 2);
    assert(generated[0] == 4);
    assert(generated[1] == 5);
}

static void test_tensor_backed_decode_fixture(void)
{
    char path[] = "/tmp/coli_inkling_decode_XXXXXX";
    int fd = mkstemp(path);
    assert(fd >= 0);
    close(fd);
    write_decode_fixture(path);

    coli_store_t *store = NULL;
    coli_model_t model;
    open_fixture(path, &store, &model);
    coli_inkling_config_t config;
    assert(coli_inkling_config_load(&model, &config) == COLI_OK);
    assert(config.num_layers == 2);

    coli_kv_cache_layout_t kv_layout;
    assert(coli_inkling_state_layout(&config, &kv_layout) == COLI_OK);
    uint8_t *kv_memory = calloc(1, (size_t)kv_layout.total_bytes);
    assert(kv_memory);
    coli_kv_cache_t *kv_cache = NULL;
    assert(coli_kv_cache_open_ram(&kv_layout, kv_memory,
                                  (size_t)kv_layout.total_bytes,
                                  &kv_cache) == COLI_OK);

    coli_kv_cache_layout_t conv_kv_layout;
    coli_kv_cache_layout_t conv_res_layout;
    assert(coli_inkling_conv_state_layouts(&config, &conv_kv_layout,
                                           &conv_res_layout) == COLI_OK);
    char conv_kv_path[] = "/tmp/coli_inkling_conv_kv_XXXXXX";
    char conv_res_path[] = "/tmp/coli_inkling_conv_res_XXXXXX";
    int conv_fd = mkstemp(conv_kv_path);
    assert(conv_fd >= 0);
    close(conv_fd);
    conv_fd = mkstemp(conv_res_path);
    assert(conv_fd >= 0);
    close(conv_fd);
    coli_kv_cache_t *conv_kv = NULL;
    coli_kv_cache_t *conv_res = NULL;
    assert(coli_kv_cache_open_file(&conv_kv_layout, conv_kv_path, 64,
                                   &conv_kv) == COLI_OK);
    assert(coli_kv_cache_open_file(&conv_res_layout, conv_res_path, 64,
                                   &conv_res) == COLI_OK);

    size_t workspace_bytes =
        coli_inkling_decode_required_workspace(&config, 1, 64);
    void *workspace = calloc(1, workspace_bytes);
    assert(workspace);
    uint32_t token = 99;
    coli_inkling_decode_stats_t stats;
    assert(coli_inkling_decode_next_token(&model, &config, 0, 0, kv_cache,
                                          conv_kv, conv_res, workspace,
                                          workspace_bytes, &token,
                                          &stats) == COLI_OK);
    assert(stats.layers_executed == 2);
    assert(stats.last_layer.moe.selected_count == 1);
    float first_hist[2] = {0};
    float scratch[2] = {0};
    assert(coli_kv_cache_read_key(conv_kv, 0, 0, first_hist) == COLI_OK);
    assert(coli_kv_cache_read_value(conv_kv, 0, 0, scratch) == COLI_OK);
    assert(fabsf(first_hist[0]) + fabsf(first_hist[1]) > 0.0f);
    assert(fabsf(scratch[0]) + fabsf(scratch[1]) > 0.0f);

    uint32_t token2 = 99;
    assert(coli_inkling_decode_next_token(&model, &config, 1, 1, kv_cache,
                                          conv_kv, conv_res, workspace,
                                          workspace_bytes, &token2,
                                          &stats) == COLI_OK);
    float second_hist[2] = {0};
    assert(coli_kv_cache_read_key(conv_kv, 0, 0, second_hist) == COLI_OK);
    assert(fabsf(second_hist[0] - first_hist[0]) +
               fabsf(second_hist[1] - first_hist[1]) >
           0.0f);
    assert(token < DECODE_VOCAB);
    assert(token2 < DECODE_VOCAB);

    free(workspace);
    coli_kv_cache_close(conv_res);
    coli_kv_cache_close(conv_kv);
    coli_kv_cache_close(kv_cache);
    free(kv_memory);
    coli_model_close(&model);
    coli_store_close(store);
    assert(remove(conv_res_path) == 0);
    assert(remove(conv_kv_path) == 0);
    assert(remove(path) == 0);
}

static void test_missing_layer_lists_are_valid(void)
{
    char path[] = "/tmp/coli_inkling_no_lists_XXXXXX";
    int fd = mkstemp(path);
    assert(fd >= 0);
    close(fd);
    write_fixture_without_layer_lists(path);
    coli_store_t *store = NULL;
    coli_model_t model;
    open_fixture(path, &store, &model);
    coli_inkling_config_t config;
    assert(coli_inkling_config_load(&model, &config) == COLI_OK);
    assert(config.local_layer_count == 0);
    assert(config.sparse_layer_count == 0);
    coli_model_close(&model);
    coli_store_close(store);
    assert(remove(path) == 0);
}

int main(void)
{
    char path[] = "/tmp/coli_inkling_model_XXXXXX";
    int fd = mkstemp(path);
    assert(fd >= 0);
    close(fd);
    write_fixture(path);

    coli_store_t *store = NULL;
    coli_model_t model;
    open_fixture(path, &store, &model);
    coli_inkling_config_t config;
    assert(coli_inkling_config_load(&model, &config) == COLI_OK);
    test_config_and_state(&config);
    test_production_shape_state_is_packed();
    test_attention(&config, false);
    test_attention(&config, true);
    test_conv_norm_mlp_and_logits();
    test_moe_and_generation(&config);
    test_tensor_backed_decode_fixture();
    test_missing_layer_lists_are_valid();
    coli_model_close(&model);
    coli_store_close(store);
    assert(remove(path) == 0);
    puts("coli_inkling ok");
    return 0;
}
