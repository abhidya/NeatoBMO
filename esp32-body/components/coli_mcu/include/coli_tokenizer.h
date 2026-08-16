#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "coli_store.h"

#ifdef __cplusplus
extern "C" {
#endif

#define COLI_TOKENIZER_MAGIC "CTOK"
#define COLI_TOKENIZER_VERSION 1u
#define COLI_TOKENIZER_HEADER_BYTES 128u
#define COLI_TOKENIZER_ENTRY_BYTES 24u
#define COLI_TOKENIZER_MERGE_BYTES 16u
#define COLI_TOKENIZER_VOCAB_SIZE_OLMOE 50304u
#define COLI_TOKENIZER_PAD_ID_OLMOE 1u
#define COLI_TOKENIZER_EOS_ID_OLMOE 50279u
#define COLI_TOKENIZER_MAX_TOKEN_BYTES 256u
#define COLI_TOKENIZER_MAX_SPECIAL_TOKENS 512u

typedef enum {
    COLI_TOKENIZER_PRETOKENIZER_BYTE_BPE = 0u,
    COLI_TOKENIZER_PRETOKENIZER_O200K = 1u,
} coli_tokenizer_pretokenizer_family_t;

typedef enum {
    COLI_TOKENIZER_ENCODE_DEFAULT = 0,
    COLI_TOKENIZER_ENCODE_ALLOW_SPECIAL = 1u << 0,
} coli_tokenizer_encode_flags_t;

typedef struct {
    coli_store_t *store;
    uint32_t vocab_size;
    uint32_t merge_count;
    uint64_t vocab_offset;
    uint64_t token_data_offset;
    uint64_t merge_offset;
    uint64_t special_token_offset;
    uint32_t pad_token_id;
    uint32_t eos_token_id;
    uint32_t special_token_count;
    uint16_t max_token_bytes;
    uint16_t pretokenizer_family;
    uint32_t special_token_ids[COLI_TOKENIZER_MAX_SPECIAL_TOKENS];
    uint32_t byte_token_ids[256];
    bool has_byte_token[256];
} coli_tokenizer_t;

coli_status_t coli_tokenizer_open(coli_store_t *store,
                                  coli_tokenizer_t *tokenizer);
coli_status_t coli_tokenizer_encode(const coli_tokenizer_t *tokenizer,
                                    const uint8_t *text, size_t text_bytes,
                                    uint32_t *token_ids,
                                    size_t token_capacity,
                                    size_t *out_token_count,
                                    uint32_t flags);
coli_status_t coli_tokenizer_decode(const coli_tokenizer_t *tokenizer,
                                    const uint32_t *token_ids,
                                    size_t token_count,
                                    uint8_t *text,
                                    size_t text_capacity,
                                    size_t *out_text_bytes);
void coli_tokenizer_close(coli_tokenizer_t *tokenizer);

#ifdef __cplusplus
}
#endif
