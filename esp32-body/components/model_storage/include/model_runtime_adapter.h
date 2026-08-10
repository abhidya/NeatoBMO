#pragma once

#include <stddef.h>
#include <stdint.h>
#include "esp_err.h"
#include "model_pager.h"

#ifdef __cplusplus
extern "C" {
#endif

/*
 * Model-agnostic boundary for existing runtimes (llama2.c, cardputer-ai,
 * gemma.cpp-derived evaluators, etc.). Runtime code asks for byte ranges;
 * this layer decides whether they come from PSRAM cache or the SSD.
 */
typedef struct {
    model_pager_t *pager;
} model_runtime_io_t;

static inline esp_err_t model_runtime_pin(model_runtime_io_t *io, uint64_t offset, size_t len, model_page_view_t *view)
{
    return model_pager_pin(io->pager, offset, len, view);
}

static inline void model_runtime_unpin(model_runtime_io_t *io, model_page_view_t *view)
{
    model_pager_unpin(io->pager, view);
}

static inline esp_err_t model_runtime_read(model_runtime_io_t *io, uint64_t offset, void *dst, size_t len)
{
    return model_pager_read(io->pager, offset, dst, len);
}

#ifdef __cplusplus
}
#endif
