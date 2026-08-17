#pragma once

#include <stdint.h>

#include "coli_generate.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    const char *model_path;
    const char *tokenizer_path;
    coli_generate_config_t generation;
} coli_runtime_request_t;

typedef struct {
    uint32_t architecture;
    coli_generate_result_t generation;
} coli_runtime_result_t;

/**
 * Inspect the model architecture and run its prompt-to-text engine.
 *
 * Model and tokenizer assets remain file-backed. Decoded bytes are delivered
 * incrementally through request->generation.log_chunk.
 */
coli_status_t coli_runtime_generate(const coli_runtime_request_t *request,
                                    coli_runtime_result_t *result);

#ifdef __cplusplus
}
#endif
