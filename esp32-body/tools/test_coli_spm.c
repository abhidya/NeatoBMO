#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "coli_spm.h"

#define TEST_VOCAB_SIZE 264u
#define TEST_NODE_COUNT 9u
#define TEST_EDGE_COUNT 8u
#define TEST_ENTRY_OFFSET COLI_SPM_HEADER_BYTES
#define TEST_NODE_OFFSET (TEST_ENTRY_OFFSET + TEST_VOCAB_SIZE * COLI_SPM_ENTRY_BYTES)
#define TEST_EDGE_OFFSET (TEST_NODE_OFFSET + TEST_NODE_COUNT * COLI_SPM_NODE_BYTES)
#define TEST_PIECE_OFFSET (TEST_EDGE_OFFSET + TEST_EDGE_COUNT * COLI_SPM_EDGE_BYTES)
#define TEST_BYTE_BASE 8u
#define TEST_PAD_ID 0u
#define TEST_EOS_ID 1u
#define TEST_BOS_ID 2u
#define TEST_UNK_ID 3u
#define TEST_A_ID 4u
#define TEST_B_ID 5u
#define TEST_AB_ID 6u
#define TEST_SPACE_HI_ID 7u

typedef struct {
    uint32_t token_id;
    const uint8_t *bytes;
    uint16_t byte_length;
    uint16_t token_type;
    float score;
    uint16_t flags;
    uint16_t byte_value;
    uint64_t data_offset;
} fixture_token_t;

typedef struct {
    uint32_t first_edge;
    uint16_t edge_count;
    uint32_t token_id;
    float score;
} fixture_node_t;

typedef struct {
    uint8_t byte_value;
    uint32_t child_node;
} fixture_edge_t;

static void put_u16(uint8_t *p, uint16_t value)
{
    p[0] = (uint8_t)value;
    p[1] = (uint8_t)(value >> 8);
}

static void put_u32(uint8_t *p, uint32_t value)
{
    for (unsigned i = 0; i < 4; ++i) {
        p[i] = (uint8_t)(value >> (i * 8u));
    }
}

static void put_u64(uint8_t *p, uint64_t value)
{
    put_u32(p, (uint32_t)value);
    put_u32(p + 4, (uint32_t)(value >> 32));
}

static void put_f32(uint8_t *p, float value)
{
    uint32_t raw;
    memcpy(&raw, &value, sizeof(raw));
    put_u32(p, raw);
}

static uint32_t byte_token(uint8_t byte_value)
{
    return TEST_BYTE_BASE + byte_value;
}

static void write_entry(FILE *file, const fixture_token_t *token)
{
    uint8_t raw[COLI_SPM_ENTRY_BYTES] = {0};
    put_u64(raw, token->data_offset);
    put_u16(raw + 8, token->byte_length);
    put_u16(raw + 10, token->token_type);
    put_f32(raw + 12, token->score);
    put_u16(raw + 16, token->flags);
    put_u16(raw + 18, token->byte_value);
    assert(fseeko(file, TEST_ENTRY_OFFSET +
                            (uint64_t)token->token_id * COLI_SPM_ENTRY_BYTES,
                  SEEK_SET) == 0);
    assert(fwrite(raw, 1, sizeof(raw), file) == sizeof(raw));
}

static void write_fixture(const char *path)
{
    FILE *file = fopen(path, "wb");
    assert(file != NULL);

    uint8_t header[COLI_SPM_HEADER_BYTES] = {0};
    memcpy(header, COLI_SPM_MAGIC, 4);
    put_u16(header + 4, COLI_SPM_VERSION);
    put_u16(header + 6, COLI_SPM_HEADER_BYTES);
    put_u32(header + 8, TEST_VOCAB_SIZE);
    put_u32(header + 12, TEST_NODE_COUNT);
    put_u32(header + 16, TEST_EDGE_COUNT);
    put_u32(header + 20, COLI_SPM_ENTRY_BYTES);
    put_u32(header + 24, COLI_SPM_NODE_BYTES);
    put_u32(header + 28, COLI_SPM_EDGE_BYTES);
    put_u64(header + 32, TEST_ENTRY_OFFSET);
    put_u64(header + 40, TEST_NODE_OFFSET);
    put_u64(header + 48, TEST_EDGE_OFFSET);
    put_u64(header + 56, TEST_PIECE_OFFSET);
    put_u32(header + 64, TEST_BOS_ID);
    put_u32(header + 68, TEST_EOS_ID);
    put_u32(header + 72, TEST_PAD_ID);
    put_u32(header + 76, TEST_UNK_ID);
    put_u32(header + 80, 256u);
    put_u16(header + 84, COLI_SPM_MAX_PIECE_BYTES);
    header[86] = 1u;
    header[87] = 0u;
    header[88] = 0u;
    assert(fwrite(header, 1, sizeof(header), file) == sizeof(header));

    uint8_t empty_entry[COLI_SPM_ENTRY_BYTES] = {0};
    for (uint32_t token_id = 0; token_id < TEST_VOCAB_SIZE; ++token_id) {
        assert(fwrite(empty_entry, 1, sizeof(empty_entry), file) ==
               sizeof(empty_entry));
    }

    fixture_token_t tokens[264];
    size_t token_count = 0;
    uint64_t data_offset = TEST_PIECE_OFFSET;
    uint8_t byte_storage[256];
    const uint8_t space_hi[] = {0xE2u, 0x96u, 0x81u, 'h', 'i'};
    const struct {
        uint32_t token_id;
        const uint8_t *bytes;
        uint16_t byte_length;
        uint16_t token_type;
        float score;
        uint16_t flags;
    } fixed[] = {
        {TEST_PAD_ID, (const uint8_t *)"<pad>", 5, 3, -1000.0f, 1},
        {TEST_EOS_ID, (const uint8_t *)"<eos>", 5, 3, -1000.0f, 1},
        {TEST_BOS_ID, (const uint8_t *)"<bos>", 5, 3, -1000.0f, 1},
        {TEST_UNK_ID, (const uint8_t *)"<unk>", 5, 3, -1000.0f, 1},
        {TEST_A_ID, (const uint8_t *)"a", 1, 1, -1.0f, 0},
        {TEST_B_ID, (const uint8_t *)"b", 1, 1, -1.0f, 0},
        {TEST_AB_ID, (const uint8_t *)"ab", 2, 1, -0.1f, 0},
        {TEST_SPACE_HI_ID, space_hi, sizeof(space_hi), 1, -0.2f, 0},
    };
    for (size_t i = 0; i < sizeof(fixed) / sizeof(fixed[0]); ++i) {
        tokens[token_count++] = (fixture_token_t){
            .token_id = fixed[i].token_id,
            .bytes = fixed[i].bytes,
            .byte_length = fixed[i].byte_length,
            .token_type = fixed[i].token_type,
            .score = fixed[i].score,
            .flags = fixed[i].flags,
            .data_offset = data_offset,
        };
        data_offset += fixed[i].byte_length;
    }
    for (uint32_t byte_value = 0; byte_value < 256u; ++byte_value) {
        byte_storage[byte_value] = (uint8_t)byte_value;
        tokens[token_count++] = (fixture_token_t){
            .token_id = byte_token((uint8_t)byte_value),
            .bytes = &byte_storage[byte_value],
            .byte_length = 1,
            .token_type = 6,
            .score = 0.0f,
            .flags = 0x0002u,
            .byte_value = (uint16_t)byte_value,
            .data_offset = data_offset,
        };
        ++data_offset;
    }
    for (size_t i = 0; i < token_count; ++i) {
        write_entry(file, &tokens[i]);
    }

    const fixture_node_t nodes[] = {
        {0, 3, UINT32_MAX, 0.0f}, {3, 1, TEST_A_ID, -1.0f},
        {4, 0, TEST_B_ID, -1.0f}, {4, 0, TEST_AB_ID, -0.1f},
        {4, 1, UINT32_MAX, 0.0f}, {5, 1, UINT32_MAX, 0.0f},
        {6, 1, UINT32_MAX, 0.0f}, {7, 1, UINT32_MAX, 0.0f},
        {8, 0, TEST_SPACE_HI_ID, -0.2f},
    };
    assert(fseeko(file, TEST_NODE_OFFSET, SEEK_SET) == 0);
    for (size_t i = 0; i < sizeof(nodes) / sizeof(nodes[0]); ++i) {
        uint8_t raw[COLI_SPM_NODE_BYTES] = {0};
        put_u32(raw, nodes[i].first_edge);
        put_u16(raw + 4, nodes[i].edge_count);
        put_u32(raw + 8, nodes[i].token_id);
        put_f32(raw + 12, nodes[i].score);
        assert(fwrite(raw, 1, sizeof(raw), file) == sizeof(raw));
    }

    const fixture_edge_t edges[] = {
        {'a', 1}, {'b', 2}, {0xE2u, 4}, {'b', 3},
        {0x96u, 5}, {0x81u, 6}, {'h', 7}, {'i', 8},
    };
    assert(fseeko(file, TEST_EDGE_OFFSET, SEEK_SET) == 0);
    for (size_t i = 0; i < sizeof(edges) / sizeof(edges[0]); ++i) {
        uint8_t raw[COLI_SPM_EDGE_BYTES] = {0};
        raw[0] = edges[i].byte_value;
        put_u32(raw + 4, edges[i].child_node);
        assert(fwrite(raw, 1, sizeof(raw), file) == sizeof(raw));
    }

    assert(fseeko(file, TEST_PIECE_OFFSET, SEEK_SET) == 0);
    for (size_t i = 0; i < token_count; ++i) {
        assert(fwrite(tokens[i].bytes, 1, tokens[i].byte_length, file) ==
               tokens[i].byte_length);
    }
    assert(fclose(file) == 0);
}

static void assert_text(const uint8_t *bytes, size_t byte_count,
                        const char *expected)
{
    assert(byte_count == strlen(expected));
    assert(memcmp(bytes, expected, byte_count) == 0);
}

static void run_file_smoke(const char *path)
{
    coli_store_t *store = NULL;
    assert(coli_store_open_file(path, &store) == COLI_OK);
    coli_spm_t spm;
    assert(coli_spm_open(store, &spm) == COLI_OK);
    assert(spm.vocab_size == 262144u);
    assert(spm.bos_token_id == 2u);
    assert(spm.eos_token_id == 1u);
    assert(spm.pad_token_id == 0u);
    assert(spm.unk_token_id == 3u);

    const char prompt[] = "hello world";
    uint32_t token_ids[64];
    size_t token_count = 0;
    assert(coli_spm_encode(&spm, (const uint8_t *)prompt, strlen(prompt),
                           token_ids, sizeof(token_ids) / sizeof(token_ids[0]),
                           &token_count, COLI_SPM_ENCODE_DEFAULT) == COLI_OK);
    assert(token_count > 1);
    assert(token_ids[0] == 2u);

    uint8_t decoded[64];
    size_t decoded_bytes = 0;
    assert(coli_spm_decode(&spm, token_ids + 1, token_count - 1, decoded,
                           sizeof(decoded), &decoded_bytes) == COLI_OK);
    assert_text(decoded, decoded_bytes, prompt);

    coli_spm_close(&spm);
    coli_store_close(store);
    puts("CSPM real-file encode/decode smoke: PASS");
}

int main(int argc, char **argv)
{
    if (argc == 2) {
        run_file_smoke(argv[1]);
        return 0;
    }

    char path[] = "/tmp/coli-spm-XXXXXX";
    int fd = mkstemp(path);
    assert(fd >= 0);
    close(fd);
    write_fixture(path);

    coli_store_t *store = NULL;
    assert(coli_store_open_file(path, &store) == COLI_OK);
    coli_spm_t spm;
    assert(coli_spm_open(store, &spm) == COLI_OK);
    assert(spm.vocab_size == TEST_VOCAB_SIZE);
    assert(spm.bos_token_id == TEST_BOS_ID);
    assert(spm.eos_token_id == TEST_EOS_ID);
    assert(spm.pad_token_id == TEST_PAD_ID);
    assert(spm.unk_token_id == TEST_UNK_ID);

    uint32_t token_ids[32];
    size_t token_count = 0;
    assert(coli_spm_encode(&spm, (const uint8_t *)"ab hi", 5, token_ids,
                           sizeof(token_ids) / sizeof(token_ids[0]),
                           &token_count, COLI_SPM_ENCODE_DEFAULT) == COLI_OK);
    assert(token_count == 3);
    assert(token_ids[0] == TEST_BOS_ID);
    assert(token_ids[1] == TEST_AB_ID);
    assert(token_ids[2] == TEST_SPACE_HI_ID);

    uint8_t decoded[64];
    size_t decoded_bytes = 0;
    assert(coli_spm_decode(&spm, token_ids + 1, token_count - 1, decoded,
                           sizeof(decoded), &decoded_bytes) == COLI_OK);
    assert_text(decoded, decoded_bytes, "ab hi");

    const uint8_t raw_bytes[] = {'x', 0xffu, 'y'};
    assert(coli_spm_encode(&spm, raw_bytes, sizeof(raw_bytes), token_ids,
                           sizeof(token_ids) / sizeof(token_ids[0]),
                           &token_count, COLI_SPM_ENCODE_DEFAULT) == COLI_OK);
    assert(token_count == 4);
    assert(token_ids[0] == TEST_BOS_ID);
    assert(token_ids[2] == byte_token(0xffu));
    assert(coli_spm_decode(&spm, token_ids + 1, token_count - 1, decoded,
                           sizeof(decoded), &decoded_bytes) == COLI_OK);
    assert(decoded_bytes == sizeof(raw_bytes));
    assert(memcmp(decoded, raw_bytes, sizeof(raw_bytes)) == 0);

    const char special_text[] = "a<eos>";
    assert(coli_spm_encode(&spm, (const uint8_t *)special_text,
                           strlen(special_text), token_ids,
                           sizeof(token_ids) / sizeof(token_ids[0]),
                           &token_count,
                           COLI_SPM_ENCODE_ALLOW_SPECIAL) == COLI_OK);
    assert(token_count == 3);
    assert(token_ids[0] == TEST_BOS_ID);
    assert(token_ids[1] == TEST_A_ID);
    assert(token_ids[2] == TEST_EOS_ID);

    assert(coli_spm_encode(&spm, (const uint8_t *)"ab hi", 5, token_ids, 2,
                           &token_count, COLI_SPM_ENCODE_DEFAULT) ==
           COLI_ERR_RANGE);
    assert(coli_spm_decode(&spm, token_ids + 1, 1, decoded, 1,
                           &decoded_bytes) == COLI_ERR_RANGE);

    coli_spm_close(&spm);
    coli_store_close(store);
    unlink(path);
    puts("CSPM tokenizer Viterbi, whitespace, bytes, specials, bounds: PASS");
    return 0;
}
