#include <assert.h>
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "coli_gemma.h"

#define HIDDEN 4u
#define HEADS 2u
#define KV_HEADS 1u
#define HEAD_DIM 2u
#define ATTN 4u
#define INTERMEDIATE 4u
#define VOCAB 8u
#define GROUP 2u
#define DIRECTORY_OFFSET BMOQ_HEADER_BYTES

typedef struct {
    uint32_t id;
    uint16_t dtype;
    uint16_t group;
    uint32_t rows;
    uint32_t columns;
    uint32_t layout;
    uint64_t offset;
    uint64_t bytes;
    const char *name;
} entry_t;

static void put_u16(uint8_t *p, uint16_t value)
{
    p[0] = (uint8_t)value;
    p[1] = (uint8_t)(value >> 8);
}

static void put_u32(uint8_t *p, uint32_t value)
{
    for (unsigned i = 0; i < 4; ++i) p[i] = (uint8_t)(value >> (i * 8u));
}

static void put_u64(uint8_t *p, uint64_t value)
{
    put_u32(p, (uint32_t)value);
    put_u32(p + 4, (uint32_t)(value >> 32));
}

static uint64_t align_up(uint64_t value)
{
    return (value + BMOQ_DATA_ALIGNMENT - 1u) / BMOQ_DATA_ALIGNMENT *
           BMOQ_DATA_ALIGNMENT;
}

static void write_entry(FILE *file, const entry_t *entry)
{
    uint8_t raw[64] = {0};
    put_u32(raw, entry->id);
    put_u16(raw + 4, entry->dtype);
    put_u16(raw + 6, entry->group);
    put_u32(raw + 8, entry->rows);
    put_u32(raw + 12, entry->columns);
    put_u32(raw + 16, 1);
    put_u32(raw + 20, 1);
    put_u64(raw + 24, entry->offset);
    put_u64(raw + 32, entry->bytes);
    put_u32(raw + 40, entry->layout);
    strncpy((char *)raw + 48, entry->name, BMOQ_TENSOR_NAME_BYTES);
    assert(fwrite(raw, 1, sizeof(raw), file) == sizeof(raw));
}

static float identity_value(uint32_t row, uint32_t column)
{
    return row == column ? 1.0f : 0.0f;
}

static void write_dense_zero(FILE *file, const entry_t *entry)
{
    assert(fseeko(file, (off_t)entry->offset, SEEK_SET) == 0);
    for (uint32_t i = 0; i < entry->rows * entry->columns; ++i) {
        float value = 0.0f;
        assert(fwrite(&value, 1, sizeof(value), file) == sizeof(value));
    }
}

static void write_q4_identity(FILE *file, const entry_t *entry)
{
    assert(fseeko(file, (off_t)entry->offset, SEEK_SET) == 0);
    for (uint32_t row = 0; row < entry->rows; ++row) {
        for (uint32_t column = 0; column < entry->columns; column += 2) {
            int low = identity_value(row, column) > 0.0f ? 7 : 0;
            int high = identity_value(row, column + 1u) > 0.0f ? 7 : 0;
            uint8_t packed = (uint8_t)(low | (high << 4));
            assert(fwrite(&packed, 1, sizeof(packed), file) == sizeof(packed));
        }
    }
}

static void write_q4_scales(FILE *file, const entry_t *entry)
{
    assert(fseeko(file, (off_t)entry->offset, SEEK_SET) == 0);
    for (uint32_t row = 0; row < entry->rows; ++row) {
        for (uint32_t group = 0; group < entry->columns; ++group) {
            float scale = 1.0f / 7.0f;
            assert(fwrite(&scale, 1, sizeof(scale), file) == sizeof(scale));
        }
    }
}

static void append_dense(entry_t *entries, size_t *count, uint32_t id,
                         uint32_t rows, uint64_t *offset, const char *name)
{
    entries[*count] = (entry_t){id, BMOQ_DTYPE_F32, 0, rows, 1,
                                BMOQ_LAYOUT_DENSE_F32, *offset,
                                rows * sizeof(float), name};
    *offset = align_up(*offset + entries[*count].bytes);
    ++*count;
}

static void append_q4_pair(entry_t *entries, size_t *count, uint32_t id,
                           uint32_t rows, uint32_t columns, uint64_t *offset,
                           const char *name)
{
    entries[*count] = (entry_t){id, BMOQ_DTYPE_Q4_SYM, GROUP, rows, columns,
                                BMOQ_LAYOUT_Q4_ROW_MAJOR, *offset,
                                (uint64_t)rows * columns / 2u, name};
    *offset = align_up(*offset + entries[*count].bytes);
    ++*count;
    entries[*count] = (entry_t){coli_gemma_scale_id(id), BMOQ_DTYPE_F32, GROUP,
                                rows, columns / GROUP,
                                BMOQ_LAYOUT_GROUP_SCALES_F32, *offset,
                                (uint64_t)rows * (columns / GROUP) *
                                    sizeof(float),
                                "scale"};
    *offset = align_up(*offset + entries[*count].bytes);
    ++*count;
}

static void write_fixture(const char *path)
{
    entry_t entries[64];
    size_t count = 0;
    uint64_t offset = align_up(DIRECTORY_OFFSET + sizeof(entries));
    append_q4_pair(entries, &count, COLI_GEMMA_TENSOR_EMBED_TOKENS, VOCAB,
                   HIDDEN, &offset, "tok_emb");
    append_dense(entries, &count, COLI_GEMMA_TENSOR_FINAL_NORM, HIDDEN, &offset,
                 "norm");
    append_q4_pair(entries, &count, COLI_GEMMA_TENSOR_LM_HEAD, VOCAB, HIDDEN,
                   &offset, "lm_head");
    append_dense(entries, &count, coli_gemma_attn_norm_id(0), HIDDEN, &offset,
                 "attnn");
    append_dense(entries, &count, coli_gemma_post_attention_norm_id(0), HIDDEN,
                 &offset, "panorm");
    append_dense(entries, &count, coli_gemma_ffn_norm_id(0), HIDDEN, &offset,
                 "ffnn");
    append_dense(entries, &count, coli_gemma_post_ffw_norm_id(0), HIDDEN,
                 &offset, "pfnorm");
    append_dense(entries, &count, coli_gemma_q_norm_id(0), HEAD_DIM, &offset,
                 "qnorm");
    append_dense(entries, &count, coli_gemma_k_norm_id(0), HEAD_DIM, &offset,
                 "knorm");
    append_q4_pair(entries, &count, coli_gemma_q_proj_id(0), ATTN, HIDDEN,
                   &offset, "q");
    append_q4_pair(entries, &count, coli_gemma_k_proj_id(0), HEAD_DIM, HIDDEN,
                   &offset, "k");
    append_q4_pair(entries, &count, coli_gemma_v_proj_id(0), HEAD_DIM, HIDDEN,
                   &offset, "v");
    append_q4_pair(entries, &count, coli_gemma_o_proj_id(0), HIDDEN, ATTN,
                   &offset, "o");
    append_q4_pair(entries, &count, coli_gemma_ffn_gate_id(0), INTERMEDIATE,
                   HIDDEN, &offset, "gate");
    append_q4_pair(entries, &count, coli_gemma_ffn_up_id(0), INTERMEDIATE,
                   HIDDEN, &offset, "up");
    append_q4_pair(entries, &count, coli_gemma_ffn_down_id(0), HIDDEN,
                   INTERMEDIATE, &offset, "down");

    FILE *file = fopen(path, "wb");
    assert(file);
    uint8_t header[BMOQ_HEADER_BYTES] = {0};
    memcpy(header, "BMOQ", 4);
    put_u16(header + 4, BMOQ_VERSION);
    put_u32(header + 8, 0x01020304u);
    put_u32(header + 12, BMOQ_HEADER_BYTES);
    put_u32(header + 16, (uint32_t)count);
    put_u32(header + 20, 64);
    put_u64(header + 24, DIRECTORY_OFFSET);
    put_u32(header + 32, COLI_GEMMA3_ARCH_ID);
    put_u32(header + 40, HIDDEN);
    put_u32(header + 44, INTERMEDIATE);
    put_u32(header + 48, 1);
    put_u32(header + 52, HEADS);
    put_u32(header + 56, KV_HEADS);
    put_u32(header + 60, 4);
    put_u32(header + 68, VOCAB);
    put_u32(header + 72, 16);
    put_u32(header + 76, 1000000);
    put_u32(header + 80, 7);
    put_u32(header + 84, 0);
    put_u32(header + 88, GROUP);
    put_u32(header + 92, COLI_GEMMA3_MANIFEST_TENSOR_ID);
    assert(fwrite(header, 1, sizeof(header), file) == sizeof(header));
    for (size_t i = 0; i < count; ++i) write_entry(file, &entries[i]);
    for (size_t i = 0; i < count; ++i) {
        if (entries[i].layout == BMOQ_LAYOUT_DENSE_F32)
            write_dense_zero(file, &entries[i]);
        else if (entries[i].layout == BMOQ_LAYOUT_Q4_ROW_MAJOR)
            write_q4_identity(file, &entries[i]);
        else if (entries[i].layout == BMOQ_LAYOUT_GROUP_SCALES_F32)
            write_q4_scales(file, &entries[i]);
    }
    assert(fclose(file) == 0);
}

int main(void)
{
    char path[] = "/tmp/coli-gemma-XXXXXX";
    int fd = mkstemp(path);
    assert(fd >= 0);
    close(fd);
    write_fixture(path);

    coli_store_t *store = NULL;
    assert(coli_store_open_file(path, &store) == COLI_OK);
    coli_model_t model;
    assert(coli_model_open(store, &model) == COLI_OK);
    coli_kv_cache_layout_t kv_layout;
    assert(coli_ops_kv_cache_layout(1, KV_HEADS, HEAD_DIM, 4, sizeof(float),
                                    &kv_layout) == COLI_OK);
    uint8_t kv_cache[1u * 2u * 4u * KV_HEADS * HEAD_DIM * sizeof(float)] = {0};
    uint8_t workspace[8192];

    float output[HIDDEN] = {1.0f, 1.0f, 1.0f, 1.0f};
    const float zero[HIDDEN] = {0};
    coli_gemma_layer_stats_t layer_stats;
    assert(coli_gemma_layer_decode(&model, 0, 0, zero, HIDDEN, output, HIDDEN,
                                   kv_cache, sizeof(kv_cache), &kv_layout,
                                   workspace, sizeof(workspace),
                                   &layer_stats) == COLI_OK);
    for (size_t i = 0; i < HIDDEN; ++i)
        assert(fabsf(output[i]) < 0.0005f);
    float *cached_key = (float *)kv_cache;
    for (size_t i = 0; i < KV_HEADS * HEAD_DIM; ++i)
        assert(fabsf(cached_key[i]) < 0.0005f);
    assert(layer_stats.attention_q4.storage_reads > 0);
    assert(layer_stats.ffn_q4.storage_reads > 0);

    memset(kv_cache, 0, sizeof(kv_cache));
    uint32_t next_token = UINT32_MAX;
    coli_gemma_decode_stats_t decode_stats;
    assert(coli_gemma_decode_next_token(&model, 0, 0, kv_cache,
                                        sizeof(kv_cache), &kv_layout,
                                        workspace, sizeof(workspace),
                                        &next_token, &decode_stats) == COLI_OK);
    assert(next_token == 0);
    assert(decode_stats.layers_executed == 1);
    assert(decode_stats.embedding_q4.storage_reads > 0);
    assert(decode_stats.lm_head_q4.storage_reads > 0);

    memset(kv_cache, 0, sizeof(kv_cache));
    const uint32_t prompt_ids[] = {0};
    uint32_t generated_ids[3] = {UINT32_MAX, UINT32_MAX, UINT32_MAX};
    size_t generated_count = 0;
    coli_gemma_generate_stats_t generate_stats;
    assert(coli_gemma_generate_greedy(&model, prompt_ids, 1, generated_ids, 3,
                                      2, &generated_count, kv_cache,
                                      sizeof(kv_cache), &kv_layout, workspace,
                                      sizeof(workspace),
                                      &generate_stats) == COLI_OK);
    assert(generated_count == 3);
    assert(generated_ids[0] == 0);
    assert(generated_ids[1] == 0);
    assert(generated_ids[2] == 0);
    assert(generate_stats.prompt_tokens_consumed == 1);
    assert(generate_stats.generated_tokens == 2);

    coli_model_close(&model);
    coli_store_close(store);
    unlink(path);
    puts("Gemma single layer decode: PASS");
    return 0;
}
