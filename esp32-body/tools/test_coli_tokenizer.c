#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "coli_tokenizer.h"

#define TEST_VOCAB_SIZE (COLI_TOKENIZER_EOS_ID_OLMOE + 1u)
#define TEST_VOCAB_OFFSET COLI_TOKENIZER_HEADER_BYTES
#define TEST_TOKEN_DATA_OFFSET \
    (TEST_VOCAB_OFFSET + TEST_VOCAB_SIZE * COLI_TOKENIZER_ENTRY_BYTES)
#define TEST_BYTE_TOKEN_BASE 1000u
#define TEST_TOKEN_AB 2000u
#define TEST_TOKEN_ABC 2001u
#define TEST_TOKEN_SPACE_HI 2002u
#define TEST_TOKEN_HE 3000u
#define TEST_TOKEN_HEL 3001u
#define TEST_TOKEN_HELL 3002u
#define TEST_TOKEN_HELLO 3003u
#define TEST_TOKEN_WO 3004u
#define TEST_TOKEN_WOR 3005u
#define TEST_TOKEN_WORL 3006u
#define TEST_TOKEN_WORLD 3007u
#define TEST_TOKEN_HELLOWORLD 3008u
#define TEST_TOKEN_12 3009u
#define TEST_TOKEN_123 3010u
#define TEST_TOKEN_1234 3011u
#define TEST_TOKEN_CA 3012u
#define TEST_TOKEN_CAN 3013u
#define TEST_TOKEN_CAN_APOS 3014u
#define TEST_TOKEN_CANT 3015u
#define TEST_TOKEN_BANG_SLASH 3016u
#define TEST_TOKEN_BANG_SLASH_SLASH 3017u
#define TEST_TOKEN_BANG_SLASH_SLASH_NL 3018u
#define TEST_TOKEN_BANG_SLASH_SLASH_NL_SLASH 3019u
#define TEST_TOKEN_ROLE_USER 3020u
#define TEST_TOKEN_HAN_NI_PART 3021u
#define TEST_TOKEN_HAN_NI 3022u
#define TEST_TOKEN_HAN_HAO_PART 3023u
#define TEST_TOKEN_HAN_HAO 3024u
#define TEST_TOKEN_HAN_NIHAO 3025u
#define TEST_MERGE_COUNT 28u

typedef struct {
    uint32_t token_id;
    const uint8_t *bytes;
    uint16_t byte_length;
    uint16_t flags;
    uint32_t byte_value;
    uint64_t data_offset;
} fixture_token_t;

typedef struct {
    uint32_t left;
    uint32_t right;
    uint32_t result;
    uint32_t rank;
} fixture_merge_t;

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

static uint32_t byte_token(uint8_t byte_value)
{
    return TEST_BYTE_TOKEN_BASE + byte_value;
}

static void write_token_entry(FILE *file, const fixture_token_t *token)
{
    uint8_t entry[COLI_TOKENIZER_ENTRY_BYTES] = {0};
    put_u64(entry, token->data_offset);
    put_u16(entry + 8, token->byte_length);
    put_u16(entry + 10, token->flags);
    put_u32(entry + 12, token->byte_value);
    assert(fseeko(file, TEST_VOCAB_OFFSET +
                            (uint64_t)token->token_id *
                                COLI_TOKENIZER_ENTRY_BYTES,
                  SEEK_SET) == 0);
    assert(fwrite(entry, 1, sizeof(entry), file) == sizeof(entry));
}

static void write_fixture(const char *path, uint16_t pretokenizer_family,
                          bool write_special_index)
{
    FILE *file = fopen(path, "wb");
    assert(file);

    uint8_t header[COLI_TOKENIZER_HEADER_BYTES] = {0};
    memcpy(header, COLI_TOKENIZER_MAGIC, 4);
    put_u16(header + 4, COLI_TOKENIZER_VERSION);
    put_u16(header + 6, COLI_TOKENIZER_HEADER_BYTES);
    put_u32(header + 8, TEST_VOCAB_SIZE);
    put_u32(header + 12, TEST_MERGE_COUNT);
    put_u32(header + 16, COLI_TOKENIZER_ENTRY_BYTES);
    put_u32(header + 20, COLI_TOKENIZER_MERGE_BYTES);
    put_u64(header + 24, TEST_VOCAB_OFFSET);
    put_u64(header + 32, TEST_TOKEN_DATA_OFFSET);
    put_u32(header + 48, 256);
    put_u32(header + 52, COLI_TOKENIZER_PAD_ID_OLMOE);
    put_u32(header + 56, COLI_TOKENIZER_EOS_ID_OLMOE);
    put_u16(header + 60, COLI_TOKENIZER_MAX_TOKEN_BYTES);
    put_u16(header + 62, pretokenizer_family);
    assert(fwrite(header, 1, sizeof(header), file) == sizeof(header));

    uint8_t empty_entry[COLI_TOKENIZER_ENTRY_BYTES] = {0};
    for (uint32_t token_id = 0; token_id < TEST_VOCAB_SIZE; ++token_id) {
        assert(fwrite(empty_entry, 1, sizeof(empty_entry), file) ==
               sizeof(empty_entry));
    }

    fixture_token_t tokens[296];
    size_t token_count = 0;
    uint64_t data_offset = TEST_TOKEN_DATA_OFFSET;
    uint8_t byte_storage[256];
    for (uint32_t byte_value = 0; byte_value < 256u; ++byte_value) {
        byte_storage[byte_value] = (uint8_t)byte_value;
        tokens[token_count++] = (fixture_token_t){
            .token_id = byte_token((uint8_t)byte_value),
            .bytes = &byte_storage[byte_value],
            .byte_length = 1,
            .flags = 0x0002u,
            .byte_value = byte_value,
            .data_offset = data_offset,
        };
        ++data_offset;
    }
    tokens[token_count++] = (fixture_token_t){
        .token_id = TEST_TOKEN_AB,
        .bytes = (const uint8_t *)"ab",
        .byte_length = 2,
        .data_offset = data_offset,
    };
    data_offset += 2;
    tokens[token_count++] = (fixture_token_t){
        .token_id = TEST_TOKEN_ABC,
        .bytes = (const uint8_t *)"abc",
        .byte_length = 3,
        .data_offset = data_offset,
    };
    data_offset += 3;
    tokens[token_count++] = (fixture_token_t){
        .token_id = TEST_TOKEN_SPACE_HI,
        .bytes = (const uint8_t *)" hi",
        .byte_length = 3,
        .data_offset = data_offset,
    };
    data_offset += 3;
    const struct {
        uint32_t token_id;
        const char *text;
    } text_tokens[] = {
        {TEST_TOKEN_HE, "He"},
        {TEST_TOKEN_HEL, "Hel"},
        {TEST_TOKEN_HELL, "Hell"},
        {TEST_TOKEN_HELLO, "Hello"},
        {TEST_TOKEN_WO, "Wo"},
        {TEST_TOKEN_WOR, "Wor"},
        {TEST_TOKEN_WORL, "Worl"},
        {TEST_TOKEN_WORLD, "World"},
        {TEST_TOKEN_HELLOWORLD, "HelloWorld"},
        {TEST_TOKEN_12, "12"},
        {TEST_TOKEN_123, "123"},
        {TEST_TOKEN_1234, "1234"},
        {TEST_TOKEN_CA, "ca"},
        {TEST_TOKEN_CAN, "can"},
        {TEST_TOKEN_CAN_APOS, "can'"},
        {TEST_TOKEN_CANT, "can't"},
        {TEST_TOKEN_BANG_SLASH, "!/"},
        {TEST_TOKEN_BANG_SLASH_SLASH, "!//"},
        {TEST_TOKEN_BANG_SLASH_SLASH_NL, "!//\n"},
        {TEST_TOKEN_BANG_SLASH_SLASH_NL_SLASH, "!//\n/"},
        {TEST_TOKEN_HAN_NI_PART, "\xE4\xBD"},
        {TEST_TOKEN_HAN_NI, "\xE4\xBD\xA0"},
        {TEST_TOKEN_HAN_HAO_PART, "\xE5\xA5"},
        {TEST_TOKEN_HAN_HAO, "\xE5\xA5\xBD"},
        {TEST_TOKEN_HAN_NIHAO, "\xE4\xBD\xA0\xE5\xA5\xBD"},
    };
    for (size_t i = 0; i < sizeof(text_tokens) / sizeof(text_tokens[0]); ++i) {
        uint16_t length = (uint16_t)strlen(text_tokens[i].text);
        tokens[token_count++] = (fixture_token_t){
            .token_id = text_tokens[i].token_id,
            .bytes = (const uint8_t *)text_tokens[i].text,
            .byte_length = length,
            .data_offset = data_offset,
        };
        data_offset += length;
    }
    tokens[token_count++] = (fixture_token_t){
        .token_id = COLI_TOKENIZER_PAD_ID_OLMOE,
        .bytes = (const uint8_t *)"<|padding|>",
        .byte_length = sizeof("<|padding|>") - 1u,
        .flags = 0x0001u,
        .data_offset = data_offset,
    };
    data_offset += sizeof("<|padding|>") - 1u;
    tokens[token_count++] = (fixture_token_t){
        .token_id = COLI_TOKENIZER_EOS_ID_OLMOE,
        .bytes = (const uint8_t *)"<|endoftext|>",
        .byte_length = sizeof("<|endoftext|>") - 1u,
        .flags = 0x0001u,
        .data_offset = data_offset,
    };
    data_offset += sizeof("<|endoftext|>") - 1u;
    tokens[token_count++] = (fixture_token_t){
        .token_id = TEST_TOKEN_ROLE_USER,
        .bytes = (const uint8_t *)"<|user|>",
        .byte_length = sizeof("<|user|>") - 1u,
        .flags = 0x0001u,
        .data_offset = data_offset,
    };
    data_offset += sizeof("<|user|>") - 1u;

    for (size_t i = 0; i < token_count; ++i) {
        write_token_entry(file, &tokens[i]);
    }

    assert(fseeko(file, TEST_TOKEN_DATA_OFFSET, SEEK_SET) == 0);
    for (size_t i = 0; i < token_count; ++i) {
        assert(fwrite(tokens[i].bytes, 1, tokens[i].byte_length, file) ==
               tokens[i].byte_length);
    }

    uint64_t special_offset = data_offset;
    const uint32_t special_ids[] = {
        COLI_TOKENIZER_PAD_ID_OLMOE,
        COLI_TOKENIZER_EOS_ID_OLMOE,
        TEST_TOKEN_ROLE_USER,
    };
    if (write_special_index) {
        assert(fseeko(file, (off_t)special_offset, SEEK_SET) == 0);
        for (size_t i = 0; i < sizeof(special_ids) / sizeof(special_ids[0]);
             ++i) {
            uint8_t raw[4];
            put_u32(raw, special_ids[i]);
            assert(fwrite(raw, 1, sizeof(raw), file) == sizeof(raw));
        }
        assert(fseeko(file, 64, SEEK_SET) == 0);
        uint8_t special_header[12];
        put_u32(special_header, sizeof(special_ids) / sizeof(special_ids[0]));
        put_u64(special_header + 4, special_offset);
        assert(fwrite(special_header, 1, sizeof(special_header), file) ==
               sizeof(special_header));
    }

    uint64_t merge_offset = special_offset;
    if (write_special_index) {
        merge_offset += sizeof(special_ids);
    }
    assert(fseeko(file, 40, SEEK_SET) == 0);
    uint8_t merge_offset_raw[8];
    put_u64(merge_offset_raw, merge_offset);
    assert(fwrite(merge_offset_raw, 1, sizeof(merge_offset_raw), file) ==
           sizeof(merge_offset_raw));

    const fixture_merge_t merges[] = {
        {.left = byte_token(' '), .right = byte_token('h'),
         .result = TEST_TOKEN_SPACE_HI, .rank = 2},
        {.left = byte_token('!'), .right = byte_token('/'),
         .result = TEST_TOKEN_BANG_SLASH, .rank = 3},
        {.left = byte_token('1'), .right = byte_token('2'),
         .result = TEST_TOKEN_12, .rank = 3},
        {.left = byte_token('H'), .right = byte_token('e'),
         .result = TEST_TOKEN_HE, .rank = 3},
        {.left = byte_token('W'), .right = byte_token('o'),
         .result = TEST_TOKEN_WO, .rank = 3},
        {.left = byte_token('a'), .right = byte_token('b'),
         .result = TEST_TOKEN_AB, .rank = 1},
        {.left = byte_token('c'), .right = byte_token('a'),
         .result = TEST_TOKEN_CA, .rank = 3},
        {.left = byte_token(0xE4u), .right = byte_token(0xBDu),
         .result = TEST_TOKEN_HAN_NI_PART, .rank = 3},
        {.left = byte_token(0xE5u), .right = byte_token(0xA5u),
         .result = TEST_TOKEN_HAN_HAO_PART, .rank = 3},
        {.left = TEST_TOKEN_AB, .right = byte_token('c'),
         .result = TEST_TOKEN_ABC, .rank = 0},
        {.left = TEST_TOKEN_HE, .right = byte_token('l'),
         .result = TEST_TOKEN_HEL, .rank = 3},
        {.left = TEST_TOKEN_HEL, .right = byte_token('l'),
         .result = TEST_TOKEN_HELL, .rank = 3},
        {.left = TEST_TOKEN_HELL, .right = byte_token('o'),
         .result = TEST_TOKEN_HELLO, .rank = 3},
        {.left = TEST_TOKEN_HELLO, .right = TEST_TOKEN_WORLD,
         .result = TEST_TOKEN_HELLOWORLD, .rank = 3},
        {.left = TEST_TOKEN_WO, .right = byte_token('r'),
         .result = TEST_TOKEN_WOR, .rank = 3},
        {.left = TEST_TOKEN_WOR, .right = byte_token('l'),
         .result = TEST_TOKEN_WORL, .rank = 3},
        {.left = TEST_TOKEN_WORL, .right = byte_token('d'),
         .result = TEST_TOKEN_WORLD, .rank = 3},
        {.left = TEST_TOKEN_12, .right = byte_token('3'),
         .result = TEST_TOKEN_123, .rank = 3},
        {.left = TEST_TOKEN_123, .right = byte_token('4'),
         .result = TEST_TOKEN_1234, .rank = 3},
        {.left = TEST_TOKEN_CA, .right = byte_token('n'),
         .result = TEST_TOKEN_CAN, .rank = 3},
        {.left = TEST_TOKEN_CAN, .right = byte_token('\''),
         .result = TEST_TOKEN_CAN_APOS, .rank = 3},
        {.left = TEST_TOKEN_CAN_APOS, .right = byte_token('t'),
         .result = TEST_TOKEN_CANT, .rank = 3},
        {.left = TEST_TOKEN_BANG_SLASH, .right = byte_token('/'),
         .result = TEST_TOKEN_BANG_SLASH_SLASH, .rank = 3},
        {.left = TEST_TOKEN_BANG_SLASH_SLASH, .right = byte_token('\n'),
         .result = TEST_TOKEN_BANG_SLASH_SLASH_NL, .rank = 3},
        {.left = TEST_TOKEN_BANG_SLASH_SLASH_NL, .right = byte_token('/'),
         .result = TEST_TOKEN_BANG_SLASH_SLASH_NL_SLASH, .rank = 3},
        {.left = TEST_TOKEN_HAN_NI_PART, .right = byte_token(0xA0u),
         .result = TEST_TOKEN_HAN_NI, .rank = 3},
        {.left = TEST_TOKEN_HAN_NI, .right = TEST_TOKEN_HAN_HAO,
         .result = TEST_TOKEN_HAN_NIHAO, .rank = 3},
        {.left = TEST_TOKEN_HAN_HAO_PART, .right = byte_token(0xBDu),
         .result = TEST_TOKEN_HAN_HAO, .rank = 3},
    };
    assert(fseeko(file, (off_t)merge_offset, SEEK_SET) == 0);
    for (size_t i = 0; i < sizeof(merges) / sizeof(merges[0]); ++i) {
        uint8_t raw[COLI_TOKENIZER_MERGE_BYTES] = {0};
        put_u32(raw, merges[i].left);
        put_u32(raw + 4, merges[i].right);
        put_u32(raw + 8, merges[i].result);
        put_u32(raw + 12, merges[i].rank);
        assert(fwrite(raw, 1, sizeof(raw), file) == sizeof(raw));
    }

    assert(fclose(file) == 0);
}

static void assert_text(const uint8_t *bytes, size_t byte_count,
                        const char *expected)
{
    assert(byte_count == strlen(expected));
    assert(memcmp(bytes, expected, byte_count) == 0);
}

int main(void)
{
    char path[] = "/tmp/coli-tokenizer-XXXXXX";
    int fd = mkstemp(path);
    assert(fd >= 0);
    close(fd);
    write_fixture(path, COLI_TOKENIZER_PRETOKENIZER_BYTE_BPE, false);

    coli_store_t *store = NULL;
    assert(coli_store_open_file(path, &store) == COLI_OK);
    coli_tokenizer_t tokenizer;
    assert(coli_tokenizer_open(store, &tokenizer) == COLI_OK);
    assert(tokenizer.vocab_size == TEST_VOCAB_SIZE);
    assert(tokenizer.pad_token_id == COLI_TOKENIZER_PAD_ID_OLMOE);
    assert(tokenizer.eos_token_id == COLI_TOKENIZER_EOS_ID_OLMOE);
    assert(tokenizer.pretokenizer_family ==
           COLI_TOKENIZER_PRETOKENIZER_BYTE_BPE);
    assert(tokenizer.special_token_count == 2);

    uint32_t token_ids[32];
    size_t token_count = 0;
    const uint8_t text[] = "abc!";
    assert(coli_tokenizer_encode(&tokenizer, text, strlen((const char *)text),
                                 token_ids, sizeof(token_ids) / sizeof(token_ids[0]),
                                 &token_count,
                                 COLI_TOKENIZER_ENCODE_DEFAULT) == COLI_OK);
    assert(token_count == 2);
    assert(token_ids[0] == TEST_TOKEN_ABC);
    assert(token_ids[1] == byte_token('!'));

    uint8_t decoded[64];
    size_t decoded_bytes = 0;
    assert(coli_tokenizer_decode(&tokenizer, token_ids, token_count, decoded,
                                 sizeof(decoded), &decoded_bytes) == COLI_OK);
    assert_text(decoded, decoded_bytes, "abc!");

    const char mixed_case[] = "HelloWorld";
    assert(coli_tokenizer_encode(&tokenizer, (const uint8_t *)mixed_case,
                                 strlen(mixed_case), token_ids,
                                 sizeof(token_ids) / sizeof(token_ids[0]),
                                 &token_count,
                                 COLI_TOKENIZER_ENCODE_DEFAULT) == COLI_OK);
    assert(token_count == 1);
    assert(token_ids[0] == TEST_TOKEN_HELLOWORLD);

    const uint8_t bytes[] = {'x', 0xffu, 'y'};
    assert(coli_tokenizer_encode(&tokenizer, bytes, sizeof(bytes), token_ids,
                                 sizeof(token_ids) / sizeof(token_ids[0]),
                                 &token_count,
                                 COLI_TOKENIZER_ENCODE_DEFAULT) == COLI_OK);
    assert(token_count == 3);
    assert(token_ids[1] == byte_token(0xffu));
    assert(coli_tokenizer_decode(&tokenizer, token_ids, token_count, decoded,
                                 sizeof(decoded), &decoded_bytes) == COLI_OK);
    assert(decoded_bytes == sizeof(bytes));
    assert(memcmp(decoded, bytes, sizeof(bytes)) == 0);

    const char special_text[] = "hi<|endoftext|>";
    assert(coli_tokenizer_encode(&tokenizer, (const uint8_t *)special_text,
                                 strlen(special_text), token_ids,
                                 sizeof(token_ids) / sizeof(token_ids[0]),
                                 &token_count,
                                 COLI_TOKENIZER_ENCODE_ALLOW_SPECIAL) == COLI_OK);
    assert(token_count == 3);
    assert(token_ids[0] == byte_token('h'));
    assert(token_ids[1] == byte_token('i'));
    assert(token_ids[2] == COLI_TOKENIZER_EOS_ID_OLMOE);
    assert(coli_tokenizer_decode(&tokenizer, token_ids, token_count, decoded,
                                 sizeof(decoded), &decoded_bytes) == COLI_OK);
    assert_text(decoded, decoded_bytes, special_text);

    assert(coli_tokenizer_encode(&tokenizer, (const uint8_t *)"abcdef", 6,
                                 token_ids, 2, &token_count,
                                 COLI_TOKENIZER_ENCODE_DEFAULT) ==
           COLI_ERR_RANGE);
    const uint32_t long_token[] = {TEST_TOKEN_ABC};
    assert(coli_tokenizer_decode(&tokenizer, long_token, 1, decoded, 2,
                                 &decoded_bytes) == COLI_ERR_RANGE);

    coli_tokenizer_close(&tokenizer);
    coli_store_close(store);

    write_fixture(path, COLI_TOKENIZER_PRETOKENIZER_O200K, true);
    store = NULL;
    assert(coli_store_open_file(path, &store) == COLI_OK);
    assert(coli_tokenizer_open(store, &tokenizer) == COLI_OK);
    assert(tokenizer.pretokenizer_family == COLI_TOKENIZER_PRETOKENIZER_O200K);
    assert(tokenizer.special_token_count == 3);

    assert(coli_tokenizer_encode(&tokenizer, (const uint8_t *)mixed_case,
                                 strlen(mixed_case), token_ids,
                                 sizeof(token_ids) / sizeof(token_ids[0]),
                                 &token_count,
                                 COLI_TOKENIZER_ENCODE_DEFAULT) == COLI_OK);
    assert(token_count == 2);
    assert(token_ids[0] == TEST_TOKEN_HELLO);
    assert(token_ids[1] == TEST_TOKEN_WORLD);

    const char number_text[] = "1234";
    assert(coli_tokenizer_encode(&tokenizer, (const uint8_t *)number_text,
                                 strlen(number_text), token_ids,
                                 sizeof(token_ids) / sizeof(token_ids[0]),
                                 &token_count,
                                 COLI_TOKENIZER_ENCODE_DEFAULT) == COLI_OK);
    assert(token_count == 2);
    assert(token_ids[0] == TEST_TOKEN_123);
    assert(token_ids[1] == byte_token('4'));

    const char contraction[] = "can't";
    assert(coli_tokenizer_encode(&tokenizer, (const uint8_t *)contraction,
                                 strlen(contraction), token_ids,
                                 sizeof(token_ids) / sizeof(token_ids[0]),
                                 &token_count,
                                 COLI_TOKENIZER_ENCODE_DEFAULT) == COLI_OK);
    assert(token_count == 1);
    assert(token_ids[0] == TEST_TOKEN_CANT);

    const char punctuation[] = "!//\n/";
    assert(coli_tokenizer_encode(&tokenizer, (const uint8_t *)punctuation,
                                 strlen(punctuation), token_ids,
                                 sizeof(token_ids) / sizeof(token_ids[0]),
                                 &token_count,
                                 COLI_TOKENIZER_ENCODE_DEFAULT) == COLI_OK);
    assert(token_count == 1);
    assert(token_ids[0] == TEST_TOKEN_BANG_SLASH_SLASH_NL_SLASH);

    assert(coli_tokenizer_decode(&tokenizer, token_ids, token_count, decoded,
                                 sizeof(decoded), &decoded_bytes) == COLI_OK);
    assert_text(decoded, decoded_bytes, punctuation);

    const char role_text[] = "x<|user|>y";
    assert(coli_tokenizer_encode(&tokenizer, (const uint8_t *)role_text,
                                 strlen(role_text), token_ids,
                                 sizeof(token_ids) / sizeof(token_ids[0]),
                                 &token_count,
                                 COLI_TOKENIZER_ENCODE_ALLOW_SPECIAL) ==
           COLI_OK);
    assert(token_count == 3);
    assert(token_ids[0] == byte_token('x'));
    assert(token_ids[1] == TEST_TOKEN_ROLE_USER);
    assert(token_ids[2] == byte_token('y'));

    coli_tokenizer_close(&tokenizer);
    coli_store_close(store);

    write_fixture(path, COLI_TOKENIZER_PRETOKENIZER_KIMI_K3, true);
    store = NULL;
    assert(coli_store_open_file(path, &store) == COLI_OK);
    assert(coli_tokenizer_open(store, &tokenizer) == COLI_OK);
    assert(tokenizer.pretokenizer_family ==
           COLI_TOKENIZER_PRETOKENIZER_KIMI_K3);

    assert(coli_tokenizer_encode(&tokenizer, (const uint8_t *)mixed_case,
                                 strlen(mixed_case), token_ids,
                                 sizeof(token_ids) / sizeof(token_ids[0]),
                                 &token_count,
                                 COLI_TOKENIZER_ENCODE_DEFAULT) == COLI_OK);
    assert(token_count == 2);
    assert(token_ids[0] == TEST_TOKEN_HELLO);
    assert(token_ids[1] == TEST_TOKEN_WORLD);

    assert(coli_tokenizer_encode(&tokenizer, (const uint8_t *)punctuation,
                                 strlen(punctuation), token_ids,
                                 sizeof(token_ids) / sizeof(token_ids[0]),
                                 &token_count,
                                 COLI_TOKENIZER_ENCODE_DEFAULT) == COLI_OK);
    assert(token_count == 2);
    assert(token_ids[0] == TEST_TOKEN_BANG_SLASH_SLASH_NL);
    assert(token_ids[1] == byte_token('/'));

    const char han_text[] = "\xE4\xBD\xA0\xE5\xA5\xBD";
    assert(coli_tokenizer_encode(&tokenizer, (const uint8_t *)han_text,
                                 strlen(han_text), token_ids,
                                 sizeof(token_ids) / sizeof(token_ids[0]),
                                 &token_count,
                                 COLI_TOKENIZER_ENCODE_DEFAULT) == COLI_OK);
    assert(token_count == 1);
    assert(token_ids[0] == TEST_TOKEN_HAN_NIHAO);

    const char han_mixed[] = "\xE4\xBD\xA0Hello";
    assert(coli_tokenizer_encode(&tokenizer, (const uint8_t *)han_mixed,
                                 strlen(han_mixed), token_ids,
                                 sizeof(token_ids) / sizeof(token_ids[0]),
                                 &token_count,
                                 COLI_TOKENIZER_ENCODE_DEFAULT) == COLI_OK);
    assert(token_count == 2);
    assert(token_ids[0] == TEST_TOKEN_HAN_NI);
    assert(token_ids[1] == TEST_TOKEN_HELLO);

    coli_tokenizer_close(&tokenizer);
    coli_store_close(store);
    unlink(path);
    puts("CTOK tokenizer byte fallback, merges, specials, bounds: PASS");
    return 0;
}
