#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "coli_model.h"

#define TEST_TENSOR_BYTES (16u * 1024u * 1024u)
#define TEST_DIRECTORY_OFFSET BMOQ_HEADER_BYTES
#define TEST_DATA_OFFSET (BMOQ_HEADER_BYTES * 2u)
#define TEST_DENSE_BYTES (4u * sizeof(float))

static void put_u16(uint8_t *p, uint16_t value)
{
    p[0] = value;
    p[1] = value >> 8;
}

static void put_u32(uint8_t *p, uint32_t value)
{
    for (unsigned i = 0; i < 4; ++i) p[i] = value >> (i * 8);
}

static void put_u64(uint8_t *p, uint64_t value)
{
    put_u32(p, (uint32_t)value);
    put_u32(p + 4, (uint32_t)(value >> 32));
}

static uint8_t expected_byte(uint64_t tensor_offset)
{
    return (uint8_t)((tensor_offset * 131u + 17u) & 0xffu);
}

static uint64_t align_up(uint64_t value, uint64_t alignment)
{
    return (value + alignment - 1) / alignment * alignment;
}

static void write_fixture(const char *path)
{
    FILE *file = fopen(path, "wb");
    assert(file);
    uint8_t *header = calloc(1, BMOQ_HEADER_BYTES);
    assert(header);
    memcpy(header, "BMOQ", 4);
    put_u16(header + 4, BMOQ_VERSION);
    put_u32(header + 8, 0x01020304u);
    put_u32(header + 12, BMOQ_HEADER_BYTES);
    put_u32(header + 16, 2);
    put_u32(header + 20, 64);
    put_u64(header + 24, TEST_DIRECTORY_OFFSET);
    assert(fwrite(header, 1, BMOQ_HEADER_BYTES, file) == BMOQ_HEADER_BYTES);
    free(header);

    uint8_t entry[64] = {0};
    put_u32(entry, 42);
    put_u16(entry + 4, 4);
    put_u16(entry + 6, 32);
    put_u32(entry + 8, 4096);
    put_u32(entry + 12, 8192);
    put_u64(entry + 24, TEST_DATA_OFFSET);
    put_u64(entry + 32, TEST_TENSOR_BYTES);
    memcpy(entry + 48, "virtual.weight", 14);
    assert(fwrite(entry, 1, sizeof(entry), file) == sizeof(entry));

    uint64_t dense_offset = align_up(TEST_DATA_OFFSET + TEST_TENSOR_BYTES,
                                     BMOQ_DATA_ALIGNMENT);
    memset(entry, 0, sizeof(entry));
    put_u32(entry, 43);
    put_u16(entry + 4, BMOQ_DTYPE_F32);
    put_u32(entry + 8, 4);
    put_u32(entry + 12, 1);
    put_u32(entry + 16, 1);
    put_u32(entry + 20, 1);
    put_u64(entry + 24, dense_offset);
    put_u64(entry + 32, TEST_DENSE_BYTES);
    put_u32(entry + 40, BMOQ_LAYOUT_DENSE_F32);
    memcpy(entry + 48, "dense.f32", 9);
    assert(fwrite(entry, 1, sizeof(entry), file) == sizeof(entry));
    assert(fseeko(file, TEST_DATA_OFFSET, SEEK_SET) == 0);

    uint8_t tile[4096];
    for (uint64_t offset = 0; offset < TEST_TENSOR_BYTES; offset += sizeof(tile)) {
        for (size_t i = 0; i < sizeof(tile); ++i)
            tile[i] = expected_byte(offset + i);
        assert(fwrite(tile, 1, sizeof(tile), file) == sizeof(tile));
    }
    assert(fseeko(file, dense_offset, SEEK_SET) == 0);
    const float dense[4] = {1.0f, -2.0f, 3.5f, 0.25f};
    assert(fwrite(dense, 1, sizeof(dense), file) == sizeof(dense));
    assert(fclose(file) == 0);
}

int main(void)
{
    char path[] = "/tmp/coli-bmoq-XXXXXX";
    int fd = mkstemp(path);
    assert(fd >= 0);
    close(fd);
    write_fixture(path);

    coli_store_t *store = NULL;
    assert(coli_store_open_file(path, &store) == COLI_OK);
    assert(coli_store_size(store) ==
           align_up(TEST_DATA_OFFSET + TEST_TENSOR_BYTES, BMOQ_DATA_ALIGNMENT) +
               TEST_DENSE_BYTES);
    coli_model_t model;
    assert(coli_model_open(store, &model) == COLI_OK);
    const bmoq_tensor_t *tensor = coli_model_find(&model, 42);
    assert(tensor && tensor->byte_length == TEST_TENSOR_BYTES);
    const bmoq_tensor_t *dense = coli_model_find(&model, 43);
    assert(dense && dense->layout == BMOQ_LAYOUT_DENSE_F32);

    uint8_t tile[257];
    const uint64_t offsets[] = {0, 4093, 8u * 1024u * 1024u + 19,
                                TEST_TENSOR_BYTES - sizeof(tile)};
    for (size_t test = 0; test < sizeof(offsets) / sizeof(offsets[0]); ++test) {
        assert(coli_tensor_read(&model, tensor, offsets[test], tile,
                                sizeof(tile)) == COLI_OK);
        for (size_t i = 0; i < sizeof(tile); ++i)
            assert(tile[i] == expected_byte(offsets[test] + i));
    }
    assert(coli_tensor_read(&model, tensor, TEST_TENSOR_BYTES - 1, tile, 2) ==
           COLI_ERR_RANGE);

    coli_model_close(&model);
    coli_store_close(store);
    unlink(path);
    puts("BMOQ parser and deterministic tiled reads: PASS");
    return 0;
}
