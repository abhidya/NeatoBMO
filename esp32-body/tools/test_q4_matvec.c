#include <assert.h>
#include <inttypes.h>
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "coli_q4.h"

#define ROWS 40000u
#define COLUMNS 448u
#define GROUP_SIZE 32u
#define GROUPS (COLUMNS / GROUP_SIZE)
#define DIRECTORY_OFFSET BMOQ_HEADER_BYTES
#define WEIGHT_OFFSET (BMOQ_HEADER_BYTES * 2u)

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

static uint64_t align_up(uint64_t value, uint64_t alignment)
{
    return (value + alignment - 1) / alignment * alignment;
}

static int8_t fixture_q(uint32_t row, uint32_t column)
{
    uint32_t group = column / GROUP_SIZE;
    return (int8_t)((row * 3u + column * 5u + group * 7u) & 15u) - 8;
}

static float fixture_scale(uint32_t row, uint32_t group)
{
    return (float)(1u + (row + group) % 7u) * 0.01f;
}

static float fixture_input(uint32_t column)
{
    return (float)((int32_t)(column % 17u) - 8) * 0.03125f;
}

static void write_entry(uint8_t *entry, uint32_t id, uint16_t dtype,
                        uint16_t quant_group, uint32_t rows, uint32_t columns,
                        uint64_t offset, uint64_t length, uint32_t layout,
                        const char *name)
{
    put_u32(entry, id);
    put_u16(entry + 4, dtype);
    put_u16(entry + 6, quant_group);
    put_u32(entry + 8, rows);
    put_u32(entry + 12, columns);
    put_u32(entry + 16, 1);
    put_u32(entry + 20, 1);
    put_u64(entry + 24, offset);
    put_u64(entry + 32, length);
    put_u32(entry + 40, layout);
    strncpy((char *)entry + 48, name, BMOQ_TENSOR_NAME_BYTES);
}

static void write_fixture(const char *path, uint64_t *weight_bytes_out,
                          uint64_t *scale_bytes_out)
{
    const uint64_t weight_bytes = (uint64_t)ROWS * COLUMNS / 2;
    const uint64_t scale_bytes = (uint64_t)ROWS * GROUPS * sizeof(float);
    const uint64_t scale_offset = align_up(WEIGHT_OFFSET + weight_bytes,
                                           BMOQ_DATA_ALIGNMENT);
    FILE *file = fopen(path, "wb");
    assert(file);
    uint8_t header[BMOQ_HEADER_BYTES] = {0};
    memcpy(header, "BMOQ", 4);
    put_u16(header + 4, BMOQ_VERSION);
    put_u32(header + 8, 0x01020304u);
    put_u32(header + 12, BMOQ_HEADER_BYTES);
    put_u32(header + 16, 2);
    put_u32(header + 20, 64);
    put_u64(header + 24, DIRECTORY_OFFSET);
    assert(fwrite(header, 1, sizeof(header), file) == sizeof(header));

    uint8_t directory[128] = {0};
    write_entry(directory, 100, BMOQ_DTYPE_Q4_SYM, GROUP_SIZE, ROWS, COLUMNS,
                WEIGHT_OFFSET, weight_bytes, BMOQ_LAYOUT_Q4_ROW_MAJOR, "q4.weight");
    write_entry(directory + 64, 101, BMOQ_DTYPE_F32, GROUP_SIZE, ROWS, GROUPS,
                scale_offset, scale_bytes, BMOQ_LAYOUT_GROUP_SCALES_F32, "q4.scale");
    assert(fwrite(directory, 1, sizeof(directory), file) == sizeof(directory));
    assert(fseeko(file, WEIGHT_OFFSET, SEEK_SET) == 0);

    uint8_t packed[COLUMNS / 2];
    for (uint32_t row = 0; row < ROWS; ++row) {
        for (uint32_t column = 0; column < COLUMNS; column += 2) {
            uint8_t low = (uint8_t)fixture_q(row, column) & 0x0fu;
            uint8_t high = (uint8_t)fixture_q(row, column + 1) & 0x0fu;
            packed[column / 2] = low | (uint8_t)(high << 4);
        }
        assert(fwrite(packed, 1, sizeof(packed), file) == sizeof(packed));
    }
    assert(fseeko(file, scale_offset, SEEK_SET) == 0);
    for (uint32_t row = 0; row < ROWS; ++row) {
        for (uint32_t group = 0; group < GROUPS; ++group) {
            float scale = fixture_scale(row, group);
            assert(fwrite(&scale, 1, sizeof(scale), file) == sizeof(scale));
        }
    }
    assert(fclose(file) == 0);
    *weight_bytes_out = weight_bytes;
    *scale_bytes_out = scale_bytes;
}

static float reference_row(uint32_t row, const float *input)
{
    float sum = 0.0f;
    for (uint32_t column = 0; column < COLUMNS; ++column) {
        float scale = fixture_scale(row, column / GROUP_SIZE);
        sum += (float)fixture_q(row, column) * scale * input[column];
    }
    return sum;
}

static void overwrite_u64(const char *path, uint64_t file_offset, uint64_t value)
{
    FILE *file = fopen(path, "r+b");
    assert(file);
    uint8_t encoded[8];
    put_u64(encoded, value);
    assert(fseeko(file, file_offset, SEEK_SET) == 0);
    assert(fwrite(encoded, 1, sizeof(encoded), file) == sizeof(encoded));
    assert(fclose(file) == 0);
}

int main(void)
{
    char path[] = "/tmp/coli-q4-XXXXXX";
    int fd = mkstemp(path);
    assert(fd >= 0);
    close(fd);
    uint64_t weight_bytes;
    uint64_t scale_bytes;
    write_fixture(path, &weight_bytes, &scale_bytes);
    assert(weight_bytes > 8u * 1024u * 1024u);

    coli_store_t *store = NULL;
    assert(coli_store_open_file(path, &store) == COLI_OK);
    coli_model_t model;
    assert(coli_model_open(store, &model) == COLI_OK);
    const bmoq_tensor_t *weights = coli_model_find(&model, 100);
    const bmoq_tensor_t *scales = coli_model_find(&model, 101);
    assert(weights && scales);

    float input[COLUMNS];
    for (uint32_t column = 0; column < COLUMNS; ++column)
        input[column] = fixture_input(column);
    float *output = calloc(ROWS, sizeof(*output));
    assert(output);
    uint8_t workspace[257];
    coli_q4_stats_t stats;
    assert(coli_q4_matvec(&model, weights, scales, input, COLUMNS, output, ROWS,
                          workspace, sizeof(workspace), &stats) == COLI_OK);

    for (uint32_t row = 0; row < ROWS; ++row)
        assert(fabsf(output[row] - reference_row(row, input)) < 0.0005f);
    assert(stats.weight_bytes_read == weight_bytes);
    assert(stats.scale_bytes_read == scale_bytes);
    assert(stats.peak_workspace_bytes <= sizeof(workspace));
    assert(stats.peak_workspace_bytes == 240);
    assert(stats.page_boundary_crossings > 0);
    assert(stats.storage_reads == ROWS * 4u);

    bmoq_tensor_t invalid = *weights;
    invalid.quant_group = 31;
    assert(coli_q4_matvec(&model, &invalid, scales, input, COLUMNS, output, ROWS,
                          workspace, sizeof(workspace), &stats) ==
           COLI_ERR_ARGUMENT);
    invalid = *weights;
    --invalid.byte_length;
    assert(coli_q4_matvec(&model, &invalid, scales, input, COLUMNS, output, ROWS,
                          workspace, sizeof(workspace), &stats) ==
           COLI_ERR_ARGUMENT);

    free(output);
    coli_model_close(&model);
    coli_store_close(store);

    overwrite_u64(path, DIRECTORY_OFFSET + 24, WEIGHT_OFFSET + 1);
    assert(coli_store_open_file(path, &store) == COLI_OK);
    assert(coli_model_open(store, &model) == COLI_ERR_FORMAT);
    coli_store_close(store);

    overwrite_u64(path, DIRECTORY_OFFSET + 24, WEIGHT_OFFSET);
    overwrite_u64(path, DIRECTORY_OFFSET + 32, UINT64_MAX);
    assert(coli_store_open_file(path, &store) == COLI_OK);
    assert(coli_model_open(store, &model) == COLI_ERR_FORMAT);
    coli_store_close(store);
    unlink(path);
    printf("Q4 streamed matvec: PASS (%" PRIu64 " weight bytes, %zu-byte workspace)\n",
           weight_bytes, sizeof(workspace));
    return 0;
}
