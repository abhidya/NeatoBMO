/* Host-only mmap-backed coli_store. Drop-in replacement for store_file.c.
 *
 * Byte-for-byte identical semantics to coli_store_read_at: same bytes are
 * copied into the caller's bounded workspace, so every kernel above it
 * (unpacking, scale reads, accumulation order) is unchanged. Only the
 * mechanism for fetching those bytes differs: one memcpy from a demand-paged
 * mapping instead of an fseeko+fread syscall pair.
 *
 * This does NOT load the model into RAM and does NOT enlarge any workspace;
 * the tiling and peak-workspace accounting in q4_matvec are untouched.
 * The ESP32 build keeps its own bounded SSD-paging store.
 */
#include "coli_store.h"

#include <fcntl.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>

struct coli_store {
    const uint8_t *base;
    uint64_t size;
};

coli_status_t coli_store_open_file(const char *path, coli_store_t **out_store)
{
    if (!path || !out_store) return COLI_ERR_ARGUMENT;
    *out_store = NULL;
    int fd = open(path, O_RDONLY);
    if (fd < 0) return COLI_ERR_IO;
    struct stat info;
    if (fstat(fd, &info) != 0 || info.st_size < 0) {
        close(fd);
        return COLI_ERR_IO;
    }
    void *base = MAP_FAILED;
    if (info.st_size > 0) {
        base = mmap(NULL, (size_t)info.st_size, PROT_READ, MAP_SHARED, fd, 0);
        if (base == MAP_FAILED) {
            close(fd);
            return COLI_ERR_IO;
        }
    }
    close(fd);
    coli_store_t *store = calloc(1, sizeof(*store));
    if (!store) {
        if (base != MAP_FAILED) munmap(base, (size_t)info.st_size);
        return COLI_ERR_NO_MEMORY;
    }
    store->base = (base == MAP_FAILED) ? NULL : base;
    store->size = (uint64_t)info.st_size;
    *out_store = store;
    return COLI_OK;
}

coli_status_t coli_store_read_at(coli_store_t *store, uint64_t offset,
                                 void *destination, size_t length)
{
    if (!store || (!destination && length)) return COLI_ERR_ARGUMENT;
    if (offset > store->size || length > store->size - offset)
        return COLI_ERR_RANGE;
    if (length) memcpy(destination, store->base + offset, length);
    return COLI_OK;
}

uint64_t coli_store_size(const coli_store_t *store)
{
    return store ? store->size : 0;
}

void coli_store_close(coli_store_t *store)
{
    if (!store) return;
    if (store->base) munmap((void *)store->base, (size_t)store->size);
    free(store);
}
