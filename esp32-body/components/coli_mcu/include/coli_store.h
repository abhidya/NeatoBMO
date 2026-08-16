#pragma once

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
    COLI_OK = 0,
    COLI_ERR_ARGUMENT = -1,
    COLI_ERR_IO = -2,
    COLI_ERR_FORMAT = -3,
    COLI_ERR_RANGE = -4,
    COLI_ERR_NO_MEMORY = -5,
    COLI_ERR_REMOVED = -6,
    COLI_ERR_UNSUPPORTED = -7,
    COLI_ERR_NOT_FOUND = -8,
} coli_status_t;

typedef struct coli_store coli_store_t;

/** Open a read-only model file. No tensor is loaded eagerly. */
coli_status_t coli_store_open_file(const char *path, coli_store_t **out_store);

/** Read exactly length bytes at a 64-bit file offset. */
coli_status_t coli_store_read_at(coli_store_t *store, uint64_t offset,
                                 void *destination, size_t length);

uint64_t coli_store_size(const coli_store_t *store);
void coli_store_close(coli_store_t *store);

#ifdef __cplusplus
}
#endif
