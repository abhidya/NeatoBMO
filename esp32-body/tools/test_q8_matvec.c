/* Q8_0 row kernels against a scalar reference.
 *
 * The fixture mirrors the on-disk product of the GGUF Q8_0 passthrough:
 * one signed byte per weight (values use the full -128..127 range that Q4
 * can never produce) plus one float32 scale per 32-weight group. Exercises
 * matvec, row-range matvec, dequantize, and argmax through the same tiled
 * bounded-workspace path the firmware uses.
 */
#include <assert.h>
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "coli_q4.h"

#define ROWS 640u
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
    /* Full int8 range, including magnitudes beyond Q4's [-8, 7]. */
    return (int8_t)(uint8_t)((row * 37u + column * 101u + 13u) & 0xffu);
}

static float fixture_scale(uint32_t row, uint32_t group)
{
    return (float)(1u + (row + group) % 5u) * 0.0078125f;
}

static float fixture_input(uint32_t column)
{
    return (float)((int32_t)(column % 19u) - 9) * 0.0625f;
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

static void write_fixture(const char *path)
{
    const uint64_t weight_bytes = (uint64_t)ROWS * COLUMNS;
    const uint64_t scale_bytes = (uint64_t)ROWS * GROUPS * sizeof(float);
    const uint64_t scale_offset =
        align_up(WEIGHT_OFFSET + weight_bytes, BMOQ_DATA_ALIGNMENT);
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
    write_entry(directory, 100, BMOQ_DTYPE_Q8_0, GROUP_SIZE, ROWS, COLUMNS,
                WEIGHT_OFFSET, weight_bytes, BMOQ_LAYOUT_Q8_ROW_MAJOR,
                "q8.weight");
    write_entry(directory + 64, 101, BMOQ_DTYPE_F32, GROUP_SIZE, ROWS, GROUPS,
                scale_offset, scale_bytes, BMOQ_LAYOUT_GROUP_SCALES_F32,
                "q8.scale");
    assert(fwrite(directory, 1, sizeof(directory), file) == sizeof(directory));
    assert(fseeko(file, WEIGHT_OFFSET, SEEK_SET) == 0);

    uint8_t row_bytes[COLUMNS];
    for (uint32_t row = 0; row < ROWS; ++row) {
        for (uint32_t column = 0; column < COLUMNS; ++column)
            row_bytes[column] = (uint8_t)fixture_q(row, column);
        assert(fwrite(row_bytes, 1, sizeof(row_bytes), file) ==
               sizeof(row_bytes));
    }
    assert(fseeko(file, scale_offset, SEEK_SET) == 0);
    for (uint32_t row = 0; row < ROWS; ++row) {
        for (uint32_t group = 0; group < GROUPS; ++group) {
            float scale = fixture_scale(row, group);
            assert(fwrite(&scale, 1, sizeof(scale), file) == sizeof(scale));
        }
    }
    assert(fclose(file) == 0);
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

int main(void)
{
    char path[] = "/tmp/coli-q8-XXXXXX";
    int fd = mkstemp(path);
    assert(fd >= 0);
    close(fd);
    write_fixture(path);

    coli_store_t *store = NULL;
    assert(coli_store_open_file(path, &store) == COLI_OK);
    coli_model_t model;
    memset(&model, 0, sizeof(model));
    assert(coli_model_open(store, &model) == COLI_OK);
    const bmoq_tensor_t *weights = coli_model_find(&model, 100);
    const bmoq_tensor_t *scales = coli_model_find(&model, 101);
    assert(weights && scales);
    assert(weights->dtype == BMOQ_DTYPE_Q8_0);
    assert(weights->layout == BMOQ_LAYOUT_Q8_ROW_MAJOR);

    float input[COLUMNS];
    for (uint32_t column = 0; column < COLUMNS; ++column)
        input[column] = fixture_input(column);

    static float output[ROWS];
    coli_q4_stats_t stats;

    /* Tiny workspace forces multi-tile rows; large workspace covers the
     * single-tile path. Both must agree with the scalar reference. */
    static uint8_t small_workspace[(GROUP_SIZE + 4u) * 2u];
    static uint8_t large_workspace[1u << 16];

    assert(coli_q4_matvec(&model, weights, scales, input, COLUMNS, output,
                          ROWS, small_workspace, sizeof(small_workspace),
                          &stats) == COLI_OK);
    for (uint32_t row = 0; row < ROWS; ++row) {
        float expected = reference_row(row, input);
        assert(fabsf(output[row] - expected) <= 1e-4f * (1.0f + fabsf(expected)));
    }
    assert(stats.weight_bytes_read == (uint64_t)ROWS * COLUMNS);

    memset(output, 0, sizeof(output));
    assert(coli_q4_matvec(&model, weights, scales, input, COLUMNS, output,
                          ROWS, large_workspace, sizeof(large_workspace),
                          &stats) == COLI_OK);
    for (uint32_t row = 0; row < ROWS; ++row) {
        float expected = reference_row(row, input);
        assert(fabsf(output[row] - expected) <= 1e-4f * (1.0f + fabsf(expected)));
    }

    float range_out[3];
    assert(coli_q4_matvec_rows(&model, weights, scales, 5, 3, input, COLUMNS,
                               range_out, 3, large_workspace,
                               sizeof(large_workspace), &stats) == COLI_OK);
    for (uint32_t i = 0; i < 3; ++i) {
        float expected = reference_row(5 + i, input);
        assert(fabsf(range_out[i] - expected) <=
               1e-4f * (1.0f + fabsf(expected)));
    }

    static float dequantized[COLUMNS];
    assert(coli_q4_dequantize_row(&model, weights, scales, 7, dequantized,
                                  COLUMNS, large_workspace,
                                  sizeof(large_workspace), &stats) == COLI_OK);
    for (uint32_t column = 0; column < COLUMNS; ++column) {
        float expected =
            (float)fixture_q(7, column) * fixture_scale(7, column / GROUP_SIZE);
        assert(fabsf(dequantized[column] - expected) <= 1e-6f);
    }

    uint32_t best_row = 0;
    float best_value = 0.0f;
    assert(coli_q4_argmax(&model, weights, scales, input, COLUMNS,
                          large_workspace, sizeof(large_workspace), &best_row,
                          &best_value, &stats) == COLI_OK);
    uint32_t expected_row = 0;
    float expected_best = reference_row(0, input);
    for (uint32_t row = 1; row < ROWS; ++row) {
        float value = reference_row(row, input);
        if (value > expected_best) {
            expected_best = value;
            expected_row = row;
        }
    }
    assert(best_row == expected_row);
    assert(fabsf(best_value - expected_best) <=
           1e-4f * (1.0f + fabsf(expected_best)));

    coli_model_close(&model);
    coli_store_close(store);
    unlink(path);
    printf("coli_q8 matvec/dequantize/argmax ok\n");
    return 0;
}
