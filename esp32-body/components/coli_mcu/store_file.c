#include "coli_store.h"

#include <stdio.h>
#include <stdlib.h>

struct coli_store {
    FILE *file;
    uint64_t size;
};

coli_status_t coli_store_open_file(const char *path, coli_store_t **out_store)
{
    if (!path || !out_store) return COLI_ERR_ARGUMENT;
    *out_store = NULL;
    FILE *file = fopen(path, "rb");
    if (!file) return COLI_ERR_IO;
    if (fseeko(file, 0, SEEK_END) != 0) {
        fclose(file);
        return COLI_ERR_IO;
    }
    off_t end = ftello(file);
    if (end < 0 || fseeko(file, 0, SEEK_SET) != 0) {
        fclose(file);
        return COLI_ERR_IO;
    }
    coli_store_t *store = calloc(1, sizeof(*store));
    if (!store) {
        fclose(file);
        return COLI_ERR_NO_MEMORY;
    }
    store->file = file;
    store->size = (uint64_t)end;
    *out_store = store;
    return COLI_OK;
}

coli_status_t coli_store_read_at(coli_store_t *store, uint64_t offset,
                                 void *destination, size_t length)
{
    if (!store || !store->file || (!destination && length))
        return COLI_ERR_ARGUMENT;
    if (offset > store->size || length > store->size - offset)
        return COLI_ERR_RANGE;
    if (fseeko(store->file, (off_t)offset, SEEK_SET) != 0)
        return COLI_ERR_IO;
    if (length && fread(destination, 1, length, store->file) != length)
        return COLI_ERR_IO;
    return COLI_OK;
}

uint64_t coli_store_size(const coli_store_t *store)
{
    return store ? store->size : 0;
}

void coli_store_close(coli_store_t *store)
{
    if (!store) return;
    if (store->file) fclose(store->file);
    free(store);
}
