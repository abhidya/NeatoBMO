#include <assert.h>
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "coli_moe.h"

#define HIDDEN 32u
#define INTERMEDIATE 48u
#define EXPERTS 10u
#define TOP_K 8u
#define GROUP_SIZE 16u
#define DIRECTORY_OFFSET BMOQ_HEADER_BYTES

typedef enum {
    MATRIX_ROUTER,
    MATRIX_GATE,
    MATRIX_UP,
    MATRIX_DOWN,
} matrix_kind_t;

typedef struct {
    uint32_t weight_id;
    uint32_t scale_id;
    matrix_kind_t kind;
    uint32_t expert;
    uint32_t bundle_experts;
    uint32_t rows;
    uint32_t columns;
    uint64_t weight_offset;
    uint64_t scale_offset;
    uint64_t weight_bytes;
    uint64_t scale_bytes;
} matrix_spec_t;

static const float router_logits[EXPERTS] = {
    0.20f, 2.00f, 1.25f, -0.50f, 1.25f,
    3.00f, 0.10f, 2.00f, -1.00f, 0.75f,
};

static const uint32_t expected_order[TOP_K] = {5u, 1u, 7u, 2u,
                                              4u, 9u, 0u, 6u};

static void put_u16(uint8_t *p, uint16_t value)
{
    p[0] = (uint8_t)value;
    p[1] = (uint8_t)(value >> 8);
}

static void put_u32(uint8_t *p, uint32_t value)
{
    for (unsigned i = 0; i < 4; ++i) p[i] = (uint8_t)(value >> (i * 8));
}

static void put_u64(uint8_t *p, uint64_t value)
{
    put_u32(p, (uint32_t)value);
    put_u32(p + 4, (uint32_t)(value >> 32));
}

static uint64_t align_up(uint64_t value, uint64_t alignment)
{
    return (value + alignment - 1u) / alignment * alignment;
}

static float fixture_input(uint32_t column)
{
    if (column == 0) return 1.0f;
    return (float)((int32_t)(column % 11u) - 5) * 0.0625f;
}

static int8_t matrix_q(matrix_kind_t kind, uint32_t expert, uint32_t row,
                       uint32_t column)
{
    if (kind == MATRIX_ROUTER) return column == 0 ? 1 : 0;
    uint32_t seed = (uint32_t)kind * 19u + expert * 23u + row * 5u +
                    column * 3u + (column / GROUP_SIZE) * 7u;
    return (int8_t)(seed & 15u) - 8;
}

static float matrix_scale(matrix_kind_t kind, uint32_t expert, uint32_t row,
                          uint32_t group)
{
    if (kind == MATRIX_ROUTER) {
        if (group == 0) return router_logits[row];
        return 0.03125f;
    }
    uint32_t seed = (uint32_t)kind * 13u + expert * 11u + row * 3u + group;
    return (float)(1u + (seed % 9u)) * 0.00625f;
}

static float dense_matvec_value(const matrix_spec_t *spec, const float *input,
                                uint32_t row)
{
    float sum = 0.0f;
    for (uint32_t column = 0; column < spec->columns; ++column) {
        sum += (float)matrix_q(spec->kind, spec->expert, row, column) *
               matrix_scale(spec->kind, spec->expert, row,
                            column / GROUP_SIZE) *
               input[column];
    }
    return sum;
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

static void add_spec(matrix_spec_t *specs, size_t *count, uint32_t weight_id,
                     uint32_t scale_id, matrix_kind_t kind, uint32_t expert,
                     uint32_t rows, uint32_t columns)
{
    matrix_spec_t *spec = &specs[(*count)++];
    memset(spec, 0, sizeof(*spec));
    spec->weight_id = weight_id;
    spec->scale_id = scale_id;
    spec->kind = kind;
    spec->expert = expert;
    spec->bundle_experts = 1u;
    spec->rows = rows;
    spec->columns = columns;
    spec->weight_bytes = (uint64_t)rows * columns / 2u;
    spec->scale_bytes =
        (uint64_t)rows * (columns / GROUP_SIZE) * sizeof(float);
}

static void add_bundle_spec(matrix_spec_t *specs, size_t *count,
                            uint32_t weight_id, uint32_t scale_id,
                            matrix_kind_t kind, uint32_t rows,
                            uint32_t columns)
{
    add_spec(specs, count, weight_id, scale_id, kind, 0u, rows, columns);
    matrix_spec_t *spec = &specs[*count - 1u];
    spec->bundle_experts = EXPERTS;
    spec->weight_bytes *= EXPERTS;
    spec->scale_bytes *= EXPERTS;
}

static void assign_offsets(matrix_spec_t *specs, size_t count)
{
    uint64_t offset =
        align_up(DIRECTORY_OFFSET + count * 2u * 64u, BMOQ_DATA_ALIGNMENT);
    for (size_t i = 0; i < count; ++i) {
        specs[i].weight_offset = offset;
        offset = align_up(offset + specs[i].weight_bytes, BMOQ_DATA_ALIGNMENT);
        specs[i].scale_offset = offset;
        offset = align_up(offset + specs[i].scale_bytes, BMOQ_DATA_ALIGNMENT);
    }
}

static void write_matrix(FILE *file, const matrix_spec_t *spec)
{
    assert(fseeko(file, spec->weight_offset, SEEK_SET) == 0);
    uint8_t packed[INTERMEDIATE / 2u];
    assert(spec->columns / 2u <= sizeof(packed));
    for (uint32_t bundled = 0; bundled < spec->bundle_experts; ++bundled) {
        uint32_t expert = spec->bundle_experts > 1u ? bundled : spec->expert;
        for (uint32_t row = 0; row < spec->rows; ++row) {
            for (uint32_t column = 0; column < spec->columns; column += 2u) {
                uint8_t low =
                    (uint8_t)matrix_q(spec->kind, expert, row, column) & 0x0fu;
                uint8_t high =
                    (uint8_t)matrix_q(spec->kind, expert, row, column + 1u) &
                    0x0fu;
                packed[column / 2u] = low | (uint8_t)(high << 4);
            }
            assert(fwrite(packed, 1, spec->columns / 2u, file) ==
                   spec->columns / 2u);
        }
    }

    assert(fseeko(file, spec->scale_offset, SEEK_SET) == 0);
    for (uint32_t bundled = 0; bundled < spec->bundle_experts; ++bundled) {
        uint32_t expert = spec->bundle_experts > 1u ? bundled : spec->expert;
        for (uint32_t row = 0; row < spec->rows; ++row) {
            for (uint32_t group = 0; group < spec->columns / GROUP_SIZE;
                 ++group) {
                float scale = matrix_scale(spec->kind, expert, row, group);
                assert(fwrite(&scale, 1, sizeof(scale), file) ==
                       sizeof(scale));
            }
        }
    }
}

static size_t make_specs(matrix_spec_t *specs,
                         coli_moe_expert_t experts[EXPERTS])
{
    size_t count = 0;
    add_spec(specs, &count, 1000u, 1001u, MATRIX_ROUTER, 0u, EXPERTS, HIDDEN);
    for (uint32_t expert = 0; expert < EXPERTS; ++expert) {
        uint32_t base = 2000u + expert * 10u;
        experts[expert].gate.weight_id = base;
        experts[expert].gate.scale_id = base + 1u;
        experts[expert].up.weight_id = base + 2u;
        experts[expert].up.scale_id = base + 3u;
        experts[expert].down.weight_id = base + 4u;
        experts[expert].down.scale_id = base + 5u;
        add_spec(specs, &count, base, base + 1u, MATRIX_GATE, expert,
                 INTERMEDIATE, HIDDEN);
        add_spec(specs, &count, base + 2u, base + 3u, MATRIX_UP, expert,
                 INTERMEDIATE, HIDDEN);
        add_spec(specs, &count, base + 4u, base + 5u, MATRIX_DOWN, expert,
                 HIDDEN, INTERMEDIATE);
    }
    assign_offsets(specs, count);
    return count;
}

static size_t make_bundle_specs(matrix_spec_t specs[4])
{
    size_t count = 0;
    add_spec(specs, &count, 1000u, 1001u, MATRIX_ROUTER, 0u, EXPERTS,
             HIDDEN);
    add_bundle_spec(specs, &count, 3000u, 3001u, MATRIX_GATE,
                    INTERMEDIATE, HIDDEN);
    add_bundle_spec(specs, &count, 3002u, 3003u, MATRIX_UP, INTERMEDIATE,
                    HIDDEN);
    add_bundle_spec(specs, &count, 3004u, 3005u, MATRIX_DOWN, HIDDEN,
                    INTERMEDIATE);
    assign_offsets(specs, count);
    return count;
}

static const matrix_spec_t *find_spec(const matrix_spec_t *specs, size_t count,
                                      uint32_t weight_id)
{
    for (size_t i = 0; i < count; ++i)
        if (specs[i].weight_id == weight_id) return &specs[i];
    return NULL;
}

static void write_fixture(const char *path, const matrix_spec_t *specs,
                          size_t spec_count)
{
    FILE *file = fopen(path, "wb");
    assert(file);
    uint8_t header[BMOQ_HEADER_BYTES] = {0};
    memcpy(header, "BMOQ", 4);
    put_u16(header + 4, BMOQ_VERSION);
    put_u32(header + 8, 0x01020304u);
    put_u32(header + 12, BMOQ_HEADER_BYTES);
    put_u32(header + 16, (uint32_t)spec_count * 2u);
    put_u32(header + 20, 64);
    put_u64(header + 24, DIRECTORY_OFFSET);
    assert(fwrite(header, 1, sizeof(header), file) == sizeof(header));

    uint8_t entry[64];
    for (size_t i = 0; i < spec_count; ++i) {
        memset(entry, 0, sizeof(entry));
        if (specs[i].bundle_experts > 1u) {
            put_u32(entry, specs[i].weight_id);
            put_u16(entry + 4, BMOQ_DTYPE_Q4_SYM);
            put_u16(entry + 6, GROUP_SIZE);
            put_u32(entry + 8, specs[i].bundle_experts);
            put_u32(entry + 12, specs[i].rows);
            put_u32(entry + 16, specs[i].columns);
            put_u32(entry + 20, 1);
            put_u64(entry + 24, specs[i].weight_offset);
            put_u64(entry + 32, specs[i].weight_bytes);
            put_u32(entry + 40, BMOQ_LAYOUT_Q4_EXPERT_BUNDLE);
        } else {
            write_entry(entry, specs[i].weight_id, BMOQ_DTYPE_Q4_SYM,
                        GROUP_SIZE, specs[i].rows, specs[i].columns,
                        specs[i].weight_offset, specs[i].weight_bytes,
                        BMOQ_LAYOUT_Q4_ROW_MAJOR, "moe.w");
        }
        assert(fwrite(entry, 1, sizeof(entry), file) == sizeof(entry));
        memset(entry, 0, sizeof(entry));
        if (specs[i].bundle_experts > 1u) {
            put_u32(entry, specs[i].scale_id);
            put_u16(entry + 4, BMOQ_DTYPE_F32);
            put_u16(entry + 6, GROUP_SIZE);
            put_u32(entry + 8, specs[i].bundle_experts);
            put_u32(entry + 12, specs[i].rows);
            put_u32(entry + 16, specs[i].columns / GROUP_SIZE);
            put_u32(entry + 20, 1);
            put_u64(entry + 24, specs[i].scale_offset);
            put_u64(entry + 32, specs[i].scale_bytes);
            put_u32(entry + 40, BMOQ_LAYOUT_EXPERT_GROUP_SCALES_F32);
        } else {
            write_entry(entry, specs[i].scale_id, BMOQ_DTYPE_F32, GROUP_SIZE,
                        specs[i].rows, specs[i].columns / GROUP_SIZE,
                        specs[i].scale_offset, specs[i].scale_bytes,
                        BMOQ_LAYOUT_GROUP_SCALES_F32, "moe.s");
        }
        assert(fwrite(entry, 1, sizeof(entry), file) == sizeof(entry));
    }
    for (size_t i = 0; i < spec_count; ++i) write_matrix(file, &specs[i]);
    assert(fclose(file) == 0);
}

static float route_weight(uint32_t expert)
{
    float max_logit = router_logits[0];
    for (uint32_t i = 1; i < EXPERTS; ++i)
        if (router_logits[i] > max_logit) max_logit = router_logits[i];
    float denominator = 0.0f;
    for (uint32_t i = 0; i < EXPERTS; ++i)
        denominator += expf(router_logits[i] - max_logit);
    return expf(router_logits[expert] - max_logit) / denominator;
}

static float silu_reference(float x)
{
    return x / (1.0f + expf(-x));
}

static void reference_forward(const matrix_spec_t *specs, size_t spec_count,
                              const coli_moe_expert_t experts[EXPERTS],
                              const float *input, float output[HIDDEN])
{
    memset(output, 0, HIDDEN * sizeof(*output));
    for (uint32_t slot = 0; slot < TOP_K; ++slot) {
        uint32_t expert = expected_order[slot];
        const matrix_spec_t *gate =
            find_spec(specs, spec_count, experts[expert].gate.weight_id);
        const matrix_spec_t *up =
            find_spec(specs, spec_count, experts[expert].up.weight_id);
        const matrix_spec_t *down =
            find_spec(specs, spec_count, experts[expert].down.weight_id);
        assert(gate && up && down);
        float ffn[INTERMEDIATE];
        for (uint32_t row = 0; row < INTERMEDIATE; ++row) {
            float gate_value = dense_matvec_value(gate, input, row);
            float up_value = dense_matvec_value(up, input, row);
            ffn[row] = silu_reference(gate_value) * up_value;
        }
        float weight = route_weight(expert);
        for (uint32_t row = 0; row < HIDDEN; ++row)
            output[row] += weight * dense_matvec_value(down, ffn, row);
    }
}

int main(void)
{
    matrix_spec_t specs[1u + EXPERTS * 3u];
    coli_moe_expert_t experts[EXPERTS];
    size_t spec_count = make_specs(specs, experts);

    char path[] = "/tmp/coli-moe-XXXXXX";
    int fd = mkstemp(path);
    assert(fd >= 0);
    close(fd);
    write_fixture(path, specs, spec_count);

    coli_store_t *store = NULL;
    assert(coli_store_open_file(path, &store) == COLI_OK);
    coli_model_t model;
    assert(coli_model_open(store, &model) == COLI_OK);

    coli_moe_config_t config = {
        .router = {.weight_id = 1000u, .scale_id = 1001u},
        .experts = experts,
        .expert_count = EXPERTS,
        .top_k = TOP_K,
        .norm_topk_prob = false,
    };
    float input[HIDDEN];
    for (uint32_t i = 0; i < HIDDEN; ++i) input[i] = fixture_input(i);

    size_t workspace_bytes =
        coli_moe_required_workspace(HIDDEN, INTERMEDIATE, EXPERTS, TOP_K, 29u);
    assert(workspace_bytes > 0);
    uint8_t *workspace = malloc(workspace_bytes);
    assert(workspace);
    float output[HIDDEN];
    coli_moe_stats_t stats;
    assert(coli_moe_forward(&model, &config, input, HIDDEN, output, HIDDEN,
                            workspace, workspace_bytes, &stats) == COLI_OK);

    assert(stats.selected_count == TOP_K);
    assert(stats.q4_calls == 1u + TOP_K * 3u);
    assert(stats.q4.peak_workspace_bytes <= 29u);
    float top_sum = 0.0f;
    for (uint32_t slot = 0; slot < TOP_K; ++slot) {
        assert(stats.selected_experts[slot] == expected_order[slot]);
        float expected = route_weight(expected_order[slot]);
        assert(fabsf(stats.routing_weights[slot] - expected) < 0.000001f);
        top_sum += stats.routing_weights[slot];
    }
    assert(top_sum < 1.0f);

    float expected[HIDDEN];
    reference_forward(specs, spec_count, experts, input, expected);
    for (uint32_t i = 0; i < HIDDEN; ++i)
        assert(fabsf(output[i] - expected[i]) < 0.0005f);

    config.sigmoid_router = true;
    config.norm_topk_prob = true;
    config.routed_scale = 2.5f;
    assert(coli_moe_forward(&model, &config, input, HIDDEN, output, HIDDEN,
                            workspace, workspace_bytes, &stats) == COLI_OK);
    float selected_sigmoid_sum = 0.0f;
    for (uint32_t slot = 0; slot < TOP_K; ++slot)
        selected_sigmoid_sum +=
            1.0f / (1.0f + expf(-router_logits[expected_order[slot]]));
    float routed_sum = 0.0f;
    for (uint32_t slot = 0; slot < TOP_K; ++slot) {
        assert(stats.selected_experts[slot] == expected_order[slot]);
        float sigmoid =
            1.0f / (1.0f + expf(-router_logits[expected_order[slot]]));
        float expected_weight = sigmoid / selected_sigmoid_sum * 2.5f;
        assert(fabsf(stats.routing_weights[slot] - expected_weight) <
               0.000001f);
        routed_sum += stats.routing_weights[slot];
    }
    assert(fabsf(routed_sum - 2.5f) < 0.000001f);

    assert(coli_moe_forward(&model, &config, input, HIDDEN, output, HIDDEN,
                            workspace, workspace_bytes - 25u, &stats) ==
           COLI_ERR_RANGE);

    coli_model_close(&model);
    coli_store_close(store);
    unlink(path);

    matrix_spec_t bundle_specs[4];
    size_t bundle_spec_count = make_bundle_specs(bundle_specs);
    char bundle_path[] = "/tmp/coli-moe-bundle-XXXXXX";
    fd = mkstemp(bundle_path);
    assert(fd >= 0);
    close(fd);
    write_fixture(bundle_path, bundle_specs, bundle_spec_count);
    assert(coli_store_open_file(bundle_path, &store) == COLI_OK);
    assert(coli_model_open(store, &model) == COLI_OK);
    const coli_moe_config_t bundle_config = {
        .router = {.weight_id = 1000u, .scale_id = 1001u},
        .expert_bundles =
            {
                .gate = {.weight_id = 3000u, .scale_id = 3001u},
                .up = {.weight_id = 3002u, .scale_id = 3003u},
                .down = {.weight_id = 3004u, .scale_id = 3005u},
            },
        .expert_count = EXPERTS,
        .top_k = TOP_K,
        .experts_bundled = true,
    };
    assert(coli_moe_forward(&model, &bundle_config, input, HIDDEN, output,
                            HIDDEN, workspace, workspace_bytes, &stats) ==
           COLI_OK);
    for (uint32_t i = 0; i < HIDDEN; ++i)
        assert(fabsf(output[i] - expected[i]) < 0.0005f);

    free(workspace);
    coli_model_close(&model);
    coli_store_close(store);
    unlink(bundle_path);
    printf("MoE streamed router+experts: PASS (%u experts, top-%u)\n",
           EXPERTS, TOP_K);
    return 0;
}
