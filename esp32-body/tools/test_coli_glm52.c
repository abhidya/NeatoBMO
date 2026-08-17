#include <assert.h>
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "coli_glm52.h"
#include "coli_runtime.h"
#include "coli_store.h"

#define CONFIG_OFFSET 8192u
#define FIXTURE_TENSOR_COUNT 26u
#define QUANT_GROUP 2u
#define CTOK_HEADER 128u
#define CTOK_ENTRY 24u

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
    for (unsigned i = 0; i < 8; ++i) p[i] = (uint8_t)(value >> (i * 8));
}

static size_t add_value(uint8_t *buffer, size_t cursor, uint32_t key,
                        uint16_t type, const void *value, uint16_t count)
{
    const uint32_t bytes = (uint32_t)count * sizeof(uint32_t);
    put_u32(buffer + cursor, key);
    put_u16(buffer + cursor + 4, type);
    put_u16(buffer + cursor + 6, count);
    put_u32(buffer + cursor + 8, bytes);
    memcpy(buffer + cursor + BMOQ_CONFIG_ENTRY_BYTES, value, bytes);
    return cursor + BMOQ_CONFIG_ENTRY_BYTES + bytes;
}

static size_t add_u32(uint8_t *buffer, size_t cursor, uint32_t key,
                      uint32_t value)
{
    return add_value(buffer, cursor, key, BMOQ_CONFIG_U32, &value, 1);
}

static uint64_t align_up(uint64_t value)
{
    return (value + BMOQ_DATA_ALIGNMENT - 1u) / BMOQ_DATA_ALIGNMENT *
           BMOQ_DATA_ALIGNMENT;
}

static void write_tensor_entry(FILE *file, uint32_t id, uint16_t dtype,
                               uint16_t quant_group, uint32_t rows,
                               uint32_t columns, uint64_t offset,
                               uint64_t length, uint32_t layout,
                               const char *name)
{
    uint8_t tensor[64] = {0};
    put_u32(tensor, id);
    put_u16(tensor + 4, dtype);
    put_u16(tensor + 6, quant_group);
    put_u32(tensor + 8, rows);
    put_u32(tensor + 12, columns);
    put_u32(tensor + 16, 1);
    put_u32(tensor + 20, 1);
    put_u64(tensor + 24, offset);
    put_u64(tensor + 32, length);
    put_u32(tensor + 40, layout);
    strncpy((char *)tensor + 48, name, BMOQ_TENSOR_NAME_BYTES);
    assert(fwrite(tensor, 1, sizeof(tensor), file) == sizeof(tensor));
}

typedef struct {
    uint32_t id;
    uint32_t rows;
    uint32_t columns;
    const char *name;
} q4_fixture_t;

static void write_fixture(const char *path)
{
    uint8_t config[248] = {0};
    memcpy(config, "BCFG", 4);
    put_u16(config + 4, 1);
    put_u16(config + 6, BMOQ_CONFIG_HEADER_BYTES);
    put_u32(config + 8, 14);
    put_u32(config + 12, sizeof(config));
    size_t cursor = BMOQ_CONFIG_HEADER_BYTES;
    cursor = add_u32(config, cursor, BMOQ_CONFIG_MOE_INTERMEDIATE_SIZE, 256);
    cursor = add_u32(config, cursor, BMOQ_CONFIG_FIRST_DENSE_LAYERS, 1);
    cursor = add_u32(config, cursor, BMOQ_CONFIG_Q_LORA_RANK, 32);
    cursor = add_u32(config, cursor, BMOQ_CONFIG_KV_LORA_RANK, 12);
    cursor = add_u32(config, cursor, BMOQ_CONFIG_QK_NOPE_HEAD_DIM, 6);
    cursor = add_u32(config, cursor, BMOQ_CONFIG_QK_ROPE_HEAD_DIM, 2);
    cursor = add_u32(config, cursor, BMOQ_CONFIG_V_HEAD_DIM, 8);
    cursor = add_u32(config, cursor, BMOQ_CONFIG_SHARED_EXPERTS, 1);
    cursor = add_u32(config, cursor, BMOQ_CONFIG_EXPERT_GROUPS, 1);
    cursor = add_u32(config, cursor, BMOQ_CONFIG_TOPK_GROUPS, 1);
    float epsilon = 1.0e-6f;
    cursor = add_value(config, cursor, BMOQ_CONFIG_RMS_NORM_EPS,
                       BMOQ_CONFIG_F32, &epsilon, 1);
    float routed_scale = 2.5f;
    cursor = add_value(config, cursor, BMOQ_CONFIG_ROUTED_SCALE,
                       BMOQ_CONFIG_F32, &routed_scale, 1);
    uint32_t normalize = 1;
    cursor = add_value(config, cursor, BMOQ_CONFIG_NORMALIZE_TOPK,
                       BMOQ_CONFIG_BOOL, &normalize, 1);
    uint32_t stops[] = {151329, 151336, 151338};
    cursor = add_value(config, cursor, BMOQ_CONFIG_STOP_TOKEN_IDS,
                       BMOQ_CONFIG_U32_ARRAY, stops, 3);
    assert(cursor == sizeof(config));

    FILE *file = fopen(path, "wb");
    assert(file);
    uint8_t header[BMOQ_HEADER_BYTES] = {0};
    memcpy(header, "BMOQ", 4);
    put_u16(header + 4, BMOQ_VERSION_EXTENDED_CONFIG);
    put_u32(header + 8, 0x01020304u);
    put_u32(header + 12, BMOQ_HEADER_BYTES);
    put_u32(header + 16, FIXTURE_TENSOR_COUNT);
    put_u32(header + 20, 64);
    put_u64(header + 24, BMOQ_HEADER_BYTES);
    uint8_t *fixed = header + BMOQ_MODEL_CONFIG_OFFSET;
    put_u32(fixed, BMOQ_MODEL_ARCH_GLM52);
    put_u32(fixed + 8, 64);
    put_u32(fixed + 12, 16);
    put_u32(fixed + 16, 1);
    put_u32(fixed + 20, 4);
    put_u32(fixed + 28, 8);
    put_u32(fixed + 32, 2);
    put_u32(fixed + 36, 1024);
    put_u32(fixed + 40, 4096);
    put_u32(fixed + 44, 10000);
    assert(fwrite(header, 1, sizeof(header), file) == sizeof(header));

    write_tensor_entry(file, BMOQ_CONFIG_TENSOR_ID, BMOQ_DTYPE_OPAQUE, 0,
                       sizeof(config), 1, CONFIG_OFFSET, sizeof(config),
                       BMOQ_LAYOUT_OPAQUE, "config.v2");
    const q4_fixture_t matrices[] = {
        {COLI_GLM52_TENSOR_EMBED_TOKENS, 1024, 64, "tok_emb"},
        {COLI_GLM52_TENSOR_LM_HEAD, 1024, 64, "lm_head"},
        {coli_glm52_q_a_id(0), 32, 64, "q_a"},
        {coli_glm52_kv_a_id(0), 14, 64, "kv_a"},
        {coli_glm52_q_b_id(0), 32, 32, "q_b"},
        {coli_glm52_kv_b_id(0), 56, 12, "kv_b"},
        {coli_glm52_o_id(0), 64, 32, "o"},
        {coli_glm52_dense_gate_id(0), 16, 64, "mlp_gate"},
        {coli_glm52_dense_up_id(0), 16, 64, "mlp_up"},
        {coli_glm52_dense_down_id(0), 64, 16, "mlp_down"},
    };
    uint64_t offsets[sizeof(matrices) / sizeof(matrices[0])][2];
    uint64_t data_offset = align_up(CONFIG_OFFSET + sizeof(config));
    for (size_t i = 0; i < sizeof(matrices) / sizeof(matrices[0]); ++i) {
        const uint64_t weight_bytes =
            (uint64_t)matrices[i].rows * matrices[i].columns / 2u;
        const uint64_t scale_bytes =
            (uint64_t)matrices[i].rows *
            (matrices[i].columns / QUANT_GROUP) * sizeof(float);
        offsets[i][0] = data_offset;
        write_tensor_entry(file, matrices[i].id, BMOQ_DTYPE_Q4_SYM,
                           QUANT_GROUP, matrices[i].rows, matrices[i].columns,
                           data_offset, weight_bytes,
                           BMOQ_LAYOUT_Q4_ROW_MAJOR, matrices[i].name);
        data_offset = align_up(data_offset + weight_bytes);
        offsets[i][1] = data_offset;
        write_tensor_entry(file, coli_glm52_scale_id(matrices[i].id),
                           BMOQ_DTYPE_F32, QUANT_GROUP, matrices[i].rows,
                           matrices[i].columns / QUANT_GROUP, data_offset,
                           scale_bytes, BMOQ_LAYOUT_GROUP_SCALES_F32, "scale");
        data_offset = align_up(data_offset + scale_bytes);
    }
    const uint32_t norm_ids[] = {COLI_GLM52_TENSOR_FINAL_NORM,
                                 coli_glm52_input_norm_id(0),
                                 coli_glm52_post_attention_norm_id(0),
                                 coli_glm52_q_a_norm_id(0),
                                 coli_glm52_kv_a_norm_id(0)};
    const uint32_t norm_counts[] = {64, 64, 64, 32, 12};
    uint64_t norm_offsets[5];
    for (size_t i = 0; i < 5; ++i) {
        norm_offsets[i] = data_offset;
        write_tensor_entry(file, norm_ids[i], BMOQ_DTYPE_F32, 0,
                           norm_counts[i], 1, data_offset,
                           (uint64_t)norm_counts[i] * sizeof(float),
                           BMOQ_LAYOUT_DENSE_F32, "norm");
        data_offset =
            align_up(data_offset + (uint64_t)norm_counts[i] * sizeof(float));
    }
    assert(fseeko(file, CONFIG_OFFSET, SEEK_SET) == 0);
    assert(fwrite(config, 1, sizeof(config), file) == sizeof(config));
    for (size_t i = 0; i < sizeof(matrices) / sizeof(matrices[0]); ++i) {
        const size_t weight_bytes =
            (size_t)matrices[i].rows * matrices[i].columns / 2u;
        uint8_t *zeros = calloc(weight_bytes, 1);
        assert(zeros);
        if (matrices[i].id == COLI_GLM52_TENSOR_EMBED_TOKENS ||
            matrices[i].id == COLI_GLM52_TENSOR_LM_HEAD) {
            const uint32_t diagonal = matrices[i].rows < matrices[i].columns
                                          ? matrices[i].rows
                                          : matrices[i].columns;
            for (uint32_t element = 0; element < diagonal; ++element) {
                size_t packed_index =
                    (size_t)element * (matrices[i].columns / 2u) +
                    element / 2u;
                if ((element & 1u) == 0)
                    zeros[packed_index] |= 7u;
                else
                    zeros[packed_index] |= 7u << 4;
            }
        }
        assert(fseeko(file, (off_t)offsets[i][0], SEEK_SET) == 0);
        assert(fwrite(zeros, 1, weight_bytes, file) == weight_bytes);
        free(zeros);
        assert(fseeko(file, (off_t)offsets[i][1], SEEK_SET) == 0);
        const size_t scale_count =
            (size_t)matrices[i].rows * matrices[i].columns / QUANT_GROUP;
        for (size_t scale = 0; scale < scale_count; ++scale) {
            const float one = 1.0f;
            assert(fwrite(&one, 1, sizeof(one), file) == sizeof(one));
        }
    }
    for (size_t i = 0; i < 5; ++i) {
        assert(fseeko(file, (off_t)norm_offsets[i], SEEK_SET) == 0);
        for (uint32_t element = 0; element < norm_counts[i]; ++element) {
            const float one = 1.0f;
            assert(fwrite(&one, 1, sizeof(one), file) == sizeof(one));
        }
    }
    assert(fclose(file) == 0);
}

static void write_ctok(const char *path)
{
    FILE *file = fopen(path, "wb");
    assert(file);
    uint8_t header[CTOK_HEADER] = {0};
    memcpy(header, "CTOK", 4);
    put_u16(header + 4, COLI_TOKENIZER_VERSION);
    put_u16(header + 6, CTOK_HEADER);
    put_u32(header + 8, 256);
    put_u32(header + 16, CTOK_ENTRY);
    put_u32(header + 20, COLI_TOKENIZER_MERGE_BYTES);
    put_u64(header + 24, CTOK_HEADER);
    put_u64(header + 32, CTOK_HEADER + 256u * CTOK_ENTRY);
    put_u64(header + 40, CTOK_HEADER + 256u * CTOK_ENTRY + 256u);
    put_u32(header + 48, 256);
    put_u32(header + 52, 1);
    put_u32(header + 56, 255);
    put_u16(header + 60, COLI_TOKENIZER_MAX_TOKEN_BYTES);
    assert(fwrite(header, 1, sizeof(header), file) == sizeof(header));
    for (uint32_t token = 0; token < 256; ++token) {
        uint8_t entry[CTOK_ENTRY] = {0};
        put_u64(entry, CTOK_HEADER + 256u * CTOK_ENTRY + token);
        put_u16(entry + 8, 1);
        put_u16(entry + 10, 0x0002u);
        put_u32(entry + 12, token);
        assert(fwrite(entry, 1, sizeof(entry), file) == sizeof(entry));
    }
    for (uint32_t token = 0; token < 256; ++token) {
        uint8_t byte = (uint8_t)token;
        assert(fwrite(&byte, 1, 1, file) == 1);
    }
    assert(fclose(file) == 0);
}

typedef struct {
    uint32_t tokens[4];
    size_t count;
    bool cancel_after_first;
} token_capture_t;

static void capture_bytes(void *context, const uint8_t *bytes, size_t count)
{
    token_capture_t *capture = context;
    assert(capture->count + count <= 4);
    for (size_t i = 0; i < count; ++i)
        capture->tokens[capture->count++] = bytes[i];
}

static bool capture_cancel(void *context)
{
    token_capture_t *capture = context;
    return capture->cancel_after_first && capture->count > 0;
}

static coli_status_t capture_token(void *context, uint32_t token_id,
                                   size_t generated_index)
{
    token_capture_t *capture = context;
    assert(generated_index == capture->count);
    assert(capture->count < 4);
    capture->tokens[capture->count++] = token_id;
    return COLI_OK;
}

int main(void)
{
    char path[] = "/tmp/coli-glm52-XXXXXX";
    char tokenizer_path[] = "/tmp/coli-glm52-ctok-XXXXXX";
    int fd = mkstemp(path);
    assert(fd >= 0);
    close(fd);
    fd = mkstemp(tokenizer_path);
    assert(fd >= 0);
    close(fd);
    write_fixture(path);
    write_ctok(tokenizer_path);

    coli_store_t *store = NULL;
    assert(coli_store_open_file(path, &store) == COLI_OK);
    coli_model_t model;
    assert(coli_model_open(store, &model) == COLI_OK);
    coli_glm52_config_t config;
    assert(coli_glm52_config_load(&model, &config) == COLI_OK);
    assert(config.hidden_size == 64);
    assert(config.qk_head_dim == 8);
    assert(config.stop_token_count == 3);
    assert(config.stop_token_ids[2] == 151338);
    assert(config.normalize_topk);
    assert(fabsf(config.rms_norm_epsilon - 1.0e-6f) < 1.0e-9f);
    assert(fabsf(config.attention_scale - 1.0f / sqrtf(8.0f)) < 1.0e-6f);

    coli_kv_cache_layout_t layout;
    assert(coli_glm52_state_layout(&config, &layout) == COLI_OK);
    assert(layout.layers == 1);
    assert(layout.max_tokens == 4096);
    assert(layout.key_token_bytes == 12u * sizeof(float));
    assert(layout.value_token_bytes == 2u * sizeof(float));
    assert(layout.total_bytes ==
           4096u * (12u + 2u) * sizeof(float));

    float rope[] = {1.0f, 2.0f, 3.0f, 4.0f};
    float rope_scratch[4];
    assert(coli_glm52_rope(rope, 4, 0, 10000.0f, rope_scratch, 4) ==
           COLI_OK);
    assert(rope[0] == 1.0f && rope[1] == 3.0f);
    assert(rope[2] == 2.0f && rope[3] == 4.0f);

    coli_kv_cache_layout_t tiny_layout;
    assert(coli_kv_cache_layout_custom(1, 3, 2 * sizeof(float),
                                       sizeof(float), &tiny_layout) == COLI_OK);
    uint8_t state_bytes[3u * 3u * sizeof(float)] = {0};
    coli_kv_cache_t *state = NULL;
    assert(coli_kv_cache_open_ram(&tiny_layout, state_bytes,
                                  sizeof(state_bytes), &state) == COLI_OK);
    const float latent[3][2] = {{1.0f, 0.0f}, {0.0f, 1.0f}, {1.0f, 1.0f}};
    const float rotated[3] = {0.0f, 1.0f, -1.0f};
    for (uint32_t token = 0; token < 3; ++token)
        assert(coli_kv_cache_write_token(state, 0, token, latent[token],
                                         &rotated[token]) == COLI_OK);
    const float query_absorbed[2] = {1.0f, 0.0f};
    const float query_rope[1] = {1.0f};
    float output_latent[2];
    float scores[3];
    float latent_scratch[2];
    float one_rope_scratch[1];
    assert(coli_glm52_attention_absorb_head(
               state, 0, query_absorbed, 2, query_rope, 1, 3, 1.0f,
               output_latent, 2, scores, 3, latent_scratch, 2,
               one_rope_scratch, 1) == COLI_OK);
    const float denom = expf(1.0f) + expf(1.0f) + expf(0.0f);
    const float expected0 = (expf(1.0f) + expf(0.0f)) / denom;
    const float expected1 = (expf(1.0f) + expf(0.0f)) / denom;
    assert(fabsf(output_latent[0] - expected0) < 1.0e-6f);
    assert(fabsf(output_latent[1] - expected1) < 1.0e-6f);
    coli_kv_cache_close(state);

    uint8_t *full_state_bytes = calloc(1, (size_t)layout.total_bytes);
    assert(full_state_bytes);
    assert(coli_kv_cache_open_ram(&layout, full_state_bytes,
                                  (size_t)layout.total_bytes, &state) ==
           COLI_OK);
    const size_t attention_workspace_bytes =
        coli_glm52_attention_required_workspace(&config, 1, 64);
    assert(attention_workspace_bytes > 64);
    void *attention_workspace = malloc(attention_workspace_bytes);
    assert(attention_workspace);
    float input[64];
    float output[64];
    for (size_t i = 0; i < 64; ++i) input[i] = (float)i / 64.0f;
    coli_glm52_attention_stats_t attention_stats;
    assert(coli_glm52_attention_decode(
               &model, &config, 0, 0, input, 64, output, 64, state,
               attention_workspace, attention_workspace_bytes,
               &attention_stats) == COLI_OK);
    for (size_t i = 0; i < 64; ++i) assert(output[i] == 0.0f);
    assert(attention_stats.projections.weight_bytes_read > 0);
    assert(attention_stats.peak_workspace_bytes <= attention_workspace_bytes);
    const size_t mlp_workspace_bytes =
        coli_glm52_dense_mlp_required_workspace(&config, 64);
    void *mlp_workspace = malloc(mlp_workspace_bytes);
    assert(mlp_workspace);
    coli_q4_stats_t mlp_stats;
    assert(coli_glm52_dense_mlp_decode(
               &model, &config, 0, input, 64, output, 64, mlp_workspace,
               mlp_workspace_bytes, &mlp_stats) == COLI_OK);
    for (size_t i = 0; i < 64; ++i) assert(output[i] == 0.0f);
    assert(mlp_stats.weight_bytes_read > 0);
    free(mlp_workspace);

    const size_t layer_workspace_bytes =
        coli_glm52_dense_layer_required_workspace(&config, 1, 64);
    void *layer_workspace = malloc(layer_workspace_bytes);
    assert(layer_workspace);
    coli_glm52_layer_stats_t layer_stats;
    assert(coli_glm52_dense_layer_decode(
               &model, &config, 0, 0, input, 64, output, 64, state,
               layer_workspace, layer_workspace_bytes, &layer_stats) ==
           COLI_OK);
    for (size_t i = 0; i < 64; ++i) assert(output[i] == input[i]);
    assert(layer_stats.peak_workspace_bytes <= layer_workspace_bytes);
    free(layer_workspace);

    free(attention_workspace);
    coli_kv_cache_close(state);
    free(full_state_bytes);

    coli_glm52_config_t generation_config = config;
    generation_config.num_layers = 1;
    generation_config.max_context_tokens = 4;
    coli_kv_cache_layout_t generation_layout;
    assert(coli_glm52_state_layout(&generation_config, &generation_layout) ==
           COLI_OK);
    uint8_t *generation_state = calloc(1, (size_t)generation_layout.total_bytes);
    assert(generation_state);
    assert(coli_kv_cache_open_ram(&generation_layout, generation_state,
                                  (size_t)generation_layout.total_bytes,
                                  &state) == COLI_OK);
    const size_t decode_workspace_bytes =
        coli_glm52_decode_required_workspace(&generation_config, 4, 64);
    assert(decode_workspace_bytes > 0);
    void *decode_workspace = malloc(decode_workspace_bytes);
    assert(decode_workspace);
    const uint32_t prompt[] = {7, 9};
    uint32_t generated[4] = {0};
    size_t generated_count = 0;
    token_capture_t capture = {0};
    coli_glm52_generate_stats_t generate_stats;
    assert(coli_glm52_generate_greedy_stream(
               &model, &generation_config, prompt, 2, generated, 4, 2,
               &generated_count, state, decode_workspace,
               decode_workspace_bytes, capture_token, &capture,
               &generate_stats) == COLI_OK);
    assert(generated_count == 4 && generated[0] == 7 && generated[1] == 9);
    assert(generated[2] == 9 && generated[3] == 9);
    assert(capture.count == 2 && capture.tokens[0] == 9 &&
           capture.tokens[1] == 9);
    assert(generate_stats.prompt_tokens_consumed == 2);
    assert(generate_stats.generated_tokens == 2);
    assert(!generate_stats.stopped_on_eos);
    assert(generate_stats.last_decode.layers_executed == 1);
    coli_glm52_config_t stopping_config = generation_config;
    stopping_config.stop_token_ids[0] = 9;
    stopping_config.stop_token_count = 1;
    memset(&capture, 0, sizeof(capture));
    generated_count = 0;
    assert(coli_glm52_generate_greedy_stream(
               &model, &stopping_config, prompt, 2, generated, 4, 2,
               &generated_count, state, decode_workspace,
               decode_workspace_bytes, capture_token, &capture,
               &generate_stats) == COLI_OK);
    assert(generated_count == 2 && capture.count == 0);
    assert(generate_stats.stopped_on_eos);
    coli_kv_cache_close(state);
    free(generation_state);

    char generation_state_path[] = "/tmp/coli-glm52-state-XXXXXX";
    fd = mkstemp(generation_state_path);
    assert(fd >= 0);
    close(fd);
    assert(coli_kv_cache_open_file(&generation_layout, generation_state_path,
                                   17, &state) == COLI_OK);
    memset(generated, 0xff, sizeof(generated));
    generated_count = 0;
    assert(coli_glm52_generate_greedy(
               &model, &generation_config, prompt, 2, generated, 4, 2,
               &generated_count, state, decode_workspace,
               decode_workspace_bytes, &generate_stats) == COLI_OK);
    assert(generated_count == 4 && generated[2] == 9 && generated[3] == 9);
    coli_kv_cache_stats_t state_stats;
    coli_kv_cache_stats(state, &state_stats);
    assert(state_stats.resident_bytes == 17);
    assert(state_stats.resident_bytes < generation_layout.total_bytes);
    coli_kv_cache_close(state);
    unlink(generation_state_path);

    char runtime_state_path[] = "/tmp/coli-glm52-runtime-state-XXXXXX";
    fd = mkstemp(runtime_state_path);
    assert(fd >= 0);
    close(fd);
    const uint8_t prompt_bytes[] = {7, 9};
    memset(&capture, 0, sizeof(capture));
    coli_runtime_request_t request = {
        .model_path = path,
        .tokenizer_path = tokenizer_path,
        .generation =
            {
                .prompt = prompt_bytes,
                .prompt_bytes = sizeof(prompt_bytes),
                .context_tokens = 4,
                .max_prompt_tokens = 2,
                .max_new_tokens = 2,
                .workspace_bytes = decode_workspace_bytes,
                .decoded_chunk_bytes = 4,
                .kv_cache_path = runtime_state_path,
                .kv_page_bytes = 17,
                .log_chunk = capture_bytes,
                .callback_context = &capture,
            },
    };
    coli_runtime_result_t runtime_result;
    assert(coli_runtime_generate(&request, &runtime_result) == COLI_OK);
    assert(runtime_result.architecture == BMOQ_MODEL_ARCH_GLM52);
    assert(runtime_result.generation.stage == COLI_GENERATE_STAGE_DONE);
    assert(runtime_result.generation.prompt_tokens == 2);
    assert(runtime_result.generation.generated_tokens == 2);
    assert(runtime_result.generation.decoded_bytes == 2);
    assert(runtime_result.generation.kv_cache_resident_bytes == 17);
    assert(capture.count == 2 && capture.tokens[0] == 9 &&
           capture.tokens[1] == 9);
    memset(&capture, 0, sizeof(capture));
    capture.cancel_after_first = true;
    request.generation.should_cancel = capture_cancel;
    assert(coli_runtime_generate(&request, &runtime_result) ==
           COLI_ERR_REMOVED);
    assert(runtime_result.generation.stage == COLI_GENERATE_STAGE_CANCELLED);
    assert(runtime_result.generation.generated_tokens == 1);
    assert(runtime_result.generation.decoded_bytes == 1);
    assert(capture.count == 1 && capture.tokens[0] == 9);
    unlink(runtime_state_path);
    free(decode_workspace);

    coli_glm52_config_t production_shape = config;
    production_shape.hidden_size = 6144;
    production_shape.num_heads = 64;
    production_shape.q_lora_rank = 2048;
    production_shape.kv_lora_rank = 512;
    production_shape.qk_nope_head_dim = 192;
    production_shape.qk_rope_head_dim = 64;
    production_shape.qk_head_dim = 256;
    production_shape.v_head_dim = 256;
    production_shape.dense_intermediate_size = 12288;
    production_shape.max_context_tokens = 4096;
    const size_t production_workspace =
        coli_glm52_attention_required_workspace(&production_shape, 4096,
                                                64u * 1024u);
    assert(production_workspace > 0);
    assert(production_workspace < 224u * 1024u);
    const size_t production_layer_workspace =
        coli_glm52_dense_layer_required_workspace(&production_shape, 4096,
                                                  1024);
    assert(production_layer_workspace > 0);
    assert(production_layer_workspace < 152u * 1024u);

    coli_model_close(&model);
    coli_store_close(store);
    unlink(tokenizer_path);
    unlink(path);
    puts("GLM-5.2 BMOQ config and compressed state layout: PASS");
    return 0;
}
