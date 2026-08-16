#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "coli_model.h"
#include "coli_tokenizer.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
    COLI_GENERATE_STAGE_IDLE = 0,
    COLI_GENERATE_STAGE_OPEN_MODEL,
    COLI_GENERATE_STAGE_OPEN_TOKENIZER,
    COLI_GENERATE_STAGE_ENCODE_PROMPT,
    COLI_GENERATE_STAGE_ALLOCATE,
    COLI_GENERATE_STAGE_GENERATE,
    COLI_GENERATE_STAGE_DECODE,
    COLI_GENERATE_STAGE_DONE,
    COLI_GENERATE_STAGE_CANCELLED,
    COLI_GENERATE_STAGE_ERROR,
} coli_generate_stage_t;

typedef bool (*coli_generate_cancel_fn)(void *context);
typedef void (*coli_generate_yield_fn)(void *context);
typedef void (*coli_generate_log_fn)(void *context, const uint8_t *bytes,
                                     size_t byte_count);

typedef struct {
    const uint8_t *prompt;
    size_t prompt_bytes;
    size_t context_tokens;
    size_t max_prompt_tokens;
    size_t max_new_tokens;
    size_t workspace_bytes;
    size_t decoded_chunk_bytes;
    /** Optional writable KV backing file. NULL keeps the legacy RAM backend. */
    const char *kv_cache_path;
    /** Resident file-cache page size; required when kv_cache_path is set. */
    size_t kv_page_bytes;
    coli_generate_cancel_fn should_cancel;
    coli_generate_yield_fn yield;
    coli_generate_log_fn log_chunk;
    void *callback_context;
} coli_generate_config_t;

typedef struct {
    coli_generate_stage_t stage;
    coli_status_t status;
    size_t prompt_tokens;
    size_t generated_tokens;
    size_t decoded_bytes;
    size_t kv_cache_bytes;
    size_t kv_cache_resident_bytes;
    size_t workspace_bytes;
    uint32_t last_token_id;
} coli_generate_result_t;

coli_status_t coli_generate_olmoe_greedy(coli_store_t *model_store,
                                         coli_store_t *tokenizer_store,
                                         const coli_generate_config_t *config,
                                         coli_generate_result_t *result);

#ifdef __cplusplus
}
#endif
