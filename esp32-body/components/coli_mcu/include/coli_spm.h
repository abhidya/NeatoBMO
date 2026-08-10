#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "coli_store.h"

#ifdef __cplusplus
extern "C" {
#endif

#define COLI_SPM_MAGIC "CSPM"
#define COLI_SPM_VERSION 1u
#define COLI_SPM_HEADER_BYTES 128u
#define COLI_SPM_ENTRY_BYTES 24u
#define COLI_SPM_NODE_BYTES 16u
#define COLI_SPM_EDGE_BYTES 8u
#define COLI_SPM_MAX_PIECE_BYTES 128u
#define COLI_SPM_MAX_NORMALIZED_BYTES 4096u

typedef enum {
    COLI_SPM_ENCODE_DEFAULT = 0,
    COLI_SPM_ENCODE_ALLOW_SPECIAL = 1u << 0,
    COLI_SPM_ENCODE_ADD_BOS = 1u << 1,
    COLI_SPM_ENCODE_ADD_EOS = 1u << 2,
} coli_spm_encode_flags_t;

typedef struct {
    coli_store_t *store;
    uint32_t vocab_size;
    uint32_t node_count;
    uint32_t edge_count;
    uint64_t entry_offset;
    uint64_t node_offset;
    uint64_t edge_offset;
    uint64_t piece_data_offset;
    uint32_t bos_token_id;
    uint32_t eos_token_id;
    uint32_t pad_token_id;
    uint32_t unk_token_id;
    uint32_t byte_token_ids[256];
    bool has_byte_token[256];
    uint16_t max_piece_bytes;
    bool add_bos_token;
    bool add_eos_token;
    bool add_space_prefix;
} coli_spm_t;

coli_status_t coli_spm_open(coli_store_t *store, coli_spm_t *spm);
coli_status_t coli_spm_encode(const coli_spm_t *spm,
                              const uint8_t *text,
                              size_t text_bytes,
                              uint32_t *token_ids,
                              size_t token_capacity,
                              size_t *out_token_count,
                              uint32_t flags);
coli_status_t coli_spm_decode(const coli_spm_t *spm,
                              const uint32_t *token_ids,
                              size_t token_count,
                              uint8_t *text,
                              size_t text_capacity,
                              size_t *out_text_bytes);
void coli_spm_close(coli_spm_t *spm);

#ifdef __cplusplus
}
#endif
