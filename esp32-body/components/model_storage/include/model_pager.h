#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include "esp_err.h"
#include "model_storage.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef struct model_pager model_pager_t;

typedef struct {
    const uint8_t *data;
    size_t len;
    uint64_t offset;
    void *internal;
} model_page_view_t;

typedef struct {
    size_t page_size;
    size_t page_count;
    bool prefer_psram;
} model_pager_config_t;

typedef struct {
    uint64_t hits;
    uint64_t misses;
    uint64_t evictions;
    uint64_t bytes_read;
} model_pager_stats_t;

esp_err_t model_pager_create(model_storage_t *storage, const model_pager_config_t *config, model_pager_t **out);
void model_pager_destroy(model_pager_t *pager);
esp_err_t model_pager_pin(model_pager_t *pager, uint64_t offset, size_t len, model_page_view_t *out);
void model_pager_unpin(model_pager_t *pager, model_page_view_t *view);
esp_err_t model_pager_read(model_pager_t *pager, uint64_t offset, void *dst, size_t len);
void model_pager_get_stats(model_pager_t *pager, model_pager_stats_t *out);

#ifdef __cplusplus
}
#endif
