#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "coli_generate.h"
#include "coli_olmoe.h"

#define HIDDEN 4u
#define HEADS 2u
#define HEAD_DIM 2u
#define EXPERTS 2u
#define TOP_K 2u
#define VOCAB 256u
#define GROUP 2u
#define DIRECTORY_OFFSET BMOQ_HEADER_BYTES
#define CTOK_HEADER 128u
#define CTOK_ENTRY 24u

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

static void add_dense(entry_t *entries, size_t *count, uint32_t id,
                      uint64_t *offset, const char *name)
{
    entries[*count] = (entry_t){id, BMOQ_DTYPE_F32, 0, HIDDEN, 1,
                                BMOQ_LAYOUT_DENSE_F32, *offset,
                                HIDDEN * sizeof(float), name};
    *offset = align_up(*offset + entries[*count].bytes);
    ++*count;
}

static void add_q4(entry_t *entries, size_t *count, uint32_t id, uint32_t rows,
                   uint32_t columns, uint64_t *offset, const char *name)
{
    entries[*count] = (entry_t){id, BMOQ_DTYPE_Q4_SYM, GROUP, rows, columns,
                                BMOQ_LAYOUT_Q4_ROW_MAJOR, *offset,
                                (uint64_t)rows * columns / 2u, name};
    *offset = align_up(*offset + entries[*count].bytes);
    ++*count;
    entries[*count] = (entry_t){coli_olmoe_scale_id(id), BMOQ_DTYPE_F32, GROUP,
                                rows, columns / GROUP,
                                BMOQ_LAYOUT_GROUP_SCALES_F32, *offset,
                                (uint64_t)rows * (columns / GROUP) *
                                    sizeof(float),
                                "scale"};
    *offset = align_up(*offset + entries[*count].bytes);
    ++*count;
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

static void write_dense(FILE *file, const entry_t *entry)
{
    assert(fseeko(file, (off_t)entry->offset, SEEK_SET) == 0);
    for (uint32_t i = 0; i < entry->rows * entry->columns; ++i) {
        float value = 1.0f;
        assert(fwrite(&value, 1, sizeof(value), file) == sizeof(value));
    }
}

static void write_q4_identity(FILE *file, const entry_t *entry)
{
    assert(fseeko(file, (off_t)entry->offset, SEEK_SET) == 0);
    for (uint32_t row = 0; row < entry->rows; ++row) {
        for (uint32_t column = 0; column < entry->columns; column += 2) {
            int low = row == column ? 7 : 0;
            int high = row == column + 1u ? 7 : 0;
            uint8_t packed = (uint8_t)(low | (high << 4));
            assert(fwrite(&packed, 1, sizeof(packed), file) == sizeof(packed));
        }
    }
}

static void write_q4_scales(FILE *file, const entry_t *entry)
{
    assert(fseeko(file, (off_t)entry->offset, SEEK_SET) == 0);
    for (uint32_t i = 0; i < entry->rows * entry->columns; ++i) {
        float scale = 1.0f / 7.0f;
        assert(fwrite(&scale, 1, sizeof(scale), file) == sizeof(scale));
    }
}

static void write_model(const char *path)
{
    entry_t entries[64];
    size_t count = 0;
    uint64_t offset = align_up(DIRECTORY_OFFSET + sizeof(entries));
    add_q4(entries, &count, COLI_OLMOE_TENSOR_EMBED_TOKENS, VOCAB, HIDDEN,
           &offset, "tok_emb");
    add_dense(entries, &count, COLI_OLMOE_TENSOR_FINAL_NORM, &offset, "norm");
    add_q4(entries, &count, COLI_OLMOE_TENSOR_LM_HEAD, VOCAB, HIDDEN, &offset,
           "lm_head");
    add_dense(entries, &count, coli_olmoe_input_norm_id(0), &offset, "in_norm");
    add_dense(entries, &count, coli_olmoe_post_attention_norm_id(0), &offset,
              "postnorm");
    add_q4(entries, &count, coli_olmoe_q_proj_id(0), HIDDEN, HIDDEN, &offset,
           "q");
    add_q4(entries, &count, coli_olmoe_k_proj_id(0), HIDDEN, HIDDEN, &offset,
           "k");
    add_q4(entries, &count, coli_olmoe_v_proj_id(0), HIDDEN, HIDDEN, &offset,
           "v");
    add_q4(entries, &count, coli_olmoe_o_proj_id(0), HIDDEN, HIDDEN, &offset,
           "o");
    add_q4(entries, &count, coli_olmoe_router_id(0), EXPERTS, HIDDEN, &offset,
           "router");
    for (uint32_t expert = 0; expert < EXPERTS; ++expert) {
        add_q4(entries, &count, coli_olmoe_expert_gate_id(0, expert), HIDDEN,
               HIDDEN, &offset, "gate");
        add_q4(entries, &count, coli_olmoe_expert_up_id(0, expert), HIDDEN,
               HIDDEN, &offset, "up");
        add_q4(entries, &count, coli_olmoe_expert_down_id(0, expert), HIDDEN,
               HIDDEN, &offset, "down");
    }

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
    put_u32(header + 32, BMOQ_MODEL_ARCH_OLMOE);
    put_u32(header + 40, HIDDEN);
    put_u32(header + 44, HIDDEN);
    put_u32(header + 48, 1);
    put_u32(header + 52, HEADS);
    put_u32(header + 56, HEADS);
    put_u32(header + 60, EXPERTS);
    put_u32(header + 64, TOP_K);
    put_u32(header + 68, VOCAB);
    put_u32(header + 72, 8);
    put_u32(header + 76, 10000);
    put_u32(header + 80, 255);
    put_u32(header + 84, 1);
    put_u32(header + 88, GROUP);
    put_u32(header + 92, COLI_OLMOE_MANIFEST_TENSOR_ID);
    assert(fwrite(header, 1, sizeof(header), file) == sizeof(header));
    for (size_t i = 0; i < count; ++i) write_entry(file, &entries[i]);
    for (size_t i = 0; i < count; ++i) {
        if (entries[i].layout == BMOQ_LAYOUT_DENSE_F32)
            write_dense(file, &entries[i]);
        else if (entries[i].layout == BMOQ_LAYOUT_Q4_ROW_MAJOR)
            write_q4_identity(file, &entries[i]);
        else
            write_q4_scales(file, &entries[i]);
    }
    assert(fclose(file) == 0);
}

static void write_ctok(const char *path)
{
    FILE *file = fopen(path, "wb");
    assert(file);
    uint8_t header[CTOK_HEADER] = {0};
    memcpy(header, "CTOK", 4);
    put_u16(header + 4, COLI_TOKENIZER_VERSION);
    put_u16(header + 6, CTOK_HEADER);
    put_u32(header + 8, VOCAB);
    put_u32(header + 12, 0);
    put_u32(header + 16, CTOK_ENTRY);
    put_u32(header + 20, COLI_TOKENIZER_MERGE_BYTES);
    put_u64(header + 24, CTOK_HEADER);
    put_u64(header + 32, CTOK_HEADER + VOCAB * CTOK_ENTRY);
    put_u64(header + 40, CTOK_HEADER + VOCAB * CTOK_ENTRY + VOCAB);
    put_u32(header + 48, 256);
    put_u32(header + 52, 1);
    put_u32(header + 56, 255);
    put_u16(header + 60, COLI_TOKENIZER_MAX_TOKEN_BYTES);
    assert(fwrite(header, 1, sizeof(header), file) == sizeof(header));
    for (uint32_t token = 0; token < VOCAB; ++token) {
        uint8_t entry[CTOK_ENTRY] = {0};
        put_u64(entry, CTOK_HEADER + VOCAB * CTOK_ENTRY + token);
        put_u16(entry + 8, 1);
        put_u16(entry + 10, 0x0002u);
        put_u32(entry + 12, token);
        assert(fwrite(entry, 1, sizeof(entry), file) == sizeof(entry));
    }
    for (uint32_t token = 0; token < VOCAB; ++token) {
        uint8_t value = (uint8_t)token;
        assert(fwrite(&value, 1, 1, file) == 1);
    }
    assert(fclose(file) == 0);
}

typedef struct {
    uint8_t bytes[8];
    size_t count;
} capture_t;

static void capture_chunk(void *context, const uint8_t *bytes, size_t count)
{
    capture_t *capture = context;
    assert(capture->count + count <= sizeof(capture->bytes));
    memcpy(capture->bytes + capture->count, bytes, count);
    capture->count += count;
}

int main(void)
{
    char model_path[] = "/tmp/coli-generate-model-XXXXXX";
    char tokenizer_path[] = "/tmp/coli-generate-ctok-XXXXXX";
    int fd = mkstemp(model_path);
    assert(fd >= 0);
    close(fd);
    fd = mkstemp(tokenizer_path);
    assert(fd >= 0);
    close(fd);
    write_model(model_path);
    write_ctok(tokenizer_path);

    coli_store_t *model_store = NULL;
    coli_store_t *tokenizer_store = NULL;
    assert(coli_store_open_file(model_path, &model_store) == COLI_OK);
    assert(coli_store_open_file(tokenizer_path, &tokenizer_store) == COLI_OK);
    const uint8_t prompt[] = {0};
    capture_t capture = {0};
    coli_generate_config_t config = {
        .prompt = prompt,
        .prompt_bytes = sizeof(prompt),
        .context_tokens = 3,
        .max_prompt_tokens = 1,
        .max_new_tokens = 2,
        .workspace_bytes = 4096,
        .decoded_chunk_bytes = 4,
        .log_chunk = capture_chunk,
        .callback_context = &capture,
    };
    coli_generate_result_t result;
    assert(coli_generate_olmoe_greedy(model_store, tokenizer_store, &config,
                                      &result) == COLI_OK);
    assert(result.stage == COLI_GENERATE_STAGE_DONE);
    assert(result.prompt_tokens == 1);
    assert(result.generated_tokens == 2);
    assert(result.decoded_bytes == 2);
    assert(capture.count == 2);
    assert(capture.bytes[0] == 0 && capture.bytes[1] == 0);

    coli_store_close(tokenizer_store);
    coli_store_close(model_store);
    unlink(tokenizer_path);
    unlink(model_path);
    puts("coli_generate OLMoE BMOQ+CTOK prompt-to-text: PASS");
    return 0;
}
