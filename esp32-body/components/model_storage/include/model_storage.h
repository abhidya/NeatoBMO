#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef struct model_storage model_storage_t;
typedef esp_err_t (*model_storage_read_at_fn)(model_storage_t *storage, uint64_t offset, void *dst, size_t len);
typedef void (*model_storage_close_fn)(model_storage_t *storage);

struct model_storage {
    void *ctx;
    uint64_t size_bytes;
    model_storage_read_at_fn read_at;
    model_storage_close_fn close;
};

esp_err_t model_storage_open_file(const char *path, model_storage_t *out);
esp_err_t model_storage_read(model_storage_t *storage, uint64_t offset, void *dst, size_t len);
void model_storage_close(model_storage_t *storage);

#ifdef __cplusplus
}
#endif
