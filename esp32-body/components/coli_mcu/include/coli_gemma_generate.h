#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "coli_generate.h"
#include "coli_model.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef coli_status_t (*coli_gemma_tokenizer_encode_fn)(
    void *context,
    coli_store_t *tokenizer_store,
    const uint8_t *prompt,
    size_t prompt_bytes,
    uint32_t *token_ids,
    size_t token_capacity,
    size_t *out_token_count);

typedef coli_status_t (*coli_gemma_tokenizer_decode_fn)(
    void *context,
    coli_store_t *tokenizer_store,
    const uint32_t *token_ids,
    size_t token_count,
    uint8_t *text,
    size_t text_capacity,
    size_t *out_text_bytes);

typedef struct {
    coli_gemma_tokenizer_encode_fn encode;
    coli_gemma_tokenizer_decode_fn decode;
    void *context;
} coli_gemma_tokenizer_t;

typedef struct {
    const uint8_t *prompt;
    size_t prompt_bytes;
    size_t context_tokens;
    size_t max_prompt_tokens;
    size_t max_new_tokens;
    size_t workspace_bytes;
    size_t decoded_chunk_bytes;
    coli_generate_cancel_fn should_cancel;
    coli_generate_yield_fn yield;
    coli_generate_log_fn log_chunk;
    void *callback_context;
} coli_gemma_generate_config_t;

typedef struct {
    coli_generate_stage_t stage;
    coli_status_t status;
    size_t prompt_tokens;
    size_t generated_tokens;
    size_t decoded_bytes;
    size_t kv_cache_bytes;
    size_t workspace_bytes;
    size_t yield_calls;
    uint32_t last_token_id;
} coli_gemma_generate_result_t;

coli_status_t coli_gemma_generate_with_tokenizer(
    coli_store_t *model_store,
    coli_store_t *tokenizer_store,
    const coli_gemma_tokenizer_t *tokenizer,
    const coli_gemma_generate_config_t *config,
    coli_gemma_generate_result_t *result);

#ifdef __cplusplus
}
#endif
