#include <assert.h>
#include <math.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "coli_inkling.h"
#include "coli_store.h"

#define CONFIG_OFFSET 8192u

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

static size_t add_entry(uint8_t *buffer, size_t cursor, uint32_t key,
                        uint16_t type, const void *value, uint16_t count)
{
    const uint32_t bytes = (uint32_t)count * sizeof(uint32_t);
    put_u32(buffer + cursor, key);
    put_u16(buffer + cursor + 4u, type);
    put_u16(buffer + cursor + 6u, count);
    put_u32(buffer + cursor + 8u, bytes);
    memcpy(buffer + cursor + BMOQ_CONFIG_ENTRY_BYTES, value, bytes);
    return cursor + BMOQ_CONFIG_ENTRY_BYTES + bytes;
}

static size_t add_u32(uint8_t *buffer, size_t cursor, uint32_t key,
                      uint32_t value)
{
    return add_entry(buffer, cursor, key, BMOQ_CONFIG_U32, &value, 1);
}

static size_t add_f32(uint8_t *buffer, size_t cursor, uint32_t key,
                      float value)
{
    return add_entry(buffer, cursor, key, BMOQ_CONFIG_F32, &value, 1);
}

static void write_config_tensor_entry(FILE *file, size_t config_bytes)
{
    uint8_t tensor[64] = {0};
    put_u32(tensor, BMOQ_CONFIG_TENSOR_ID);
    put_u16(tensor + 4, BMOQ_DTYPE_OPAQUE);
    put_u32(tensor + 8, (uint32_t)config_bytes);
    put_u32(tensor + 12, 1);
    put_u32(tensor + 16, 1);
    put_u32(tensor + 20, 1);
    put_u64(tensor + 24, CONFIG_OFFSET);
    put_u64(tensor + 32, config_bytes);
    put_u32(tensor + 40, BMOQ_LAYOUT_OPAQUE);
    memcpy(tensor + 48, "config.v2", 9);
    assert(fwrite(tensor, 1, sizeof(tensor), file) == sizeof(tensor));
}

static void write_fixture(const char *path)
{
    uint8_t config[356] = {0};
    memcpy(config, "BCFG", 4);
    put_u16(config + 4, 1);
    put_u16(config + 6, BMOQ_CONFIG_HEADER_BYTES);
    put_u32(config + 8, 21);
    put_u32(config + 12, sizeof(config));
    size_t cursor = BMOQ_CONFIG_HEADER_BYTES;
    cursor = add_u32(config, cursor, BMOQ_CONFIG_UNPADDED_VOCAB_SIZE, 8);
    cursor = add_u32(config, cursor, BMOQ_CONFIG_HEAD_DIM, 2);
    cursor = add_u32(config, cursor, BMOQ_CONFIG_SWA_NUM_ATTENTION_HEADS, 2);
    cursor = add_u32(config, cursor, BMOQ_CONFIG_SWA_NUM_KEY_VALUE_HEADS, 1);
    cursor = add_u32(config, cursor, BMOQ_CONFIG_SWA_HEAD_DIM, 2);
    cursor = add_u32(config, cursor, BMOQ_CONFIG_SLIDING_WINDOW, 2);
    cursor = add_u32(config, cursor, BMOQ_CONFIG_D_REL, 2);
    cursor = add_u32(config, cursor, BMOQ_CONFIG_REL_EXTENT, 4);
    cursor = add_u32(config, cursor, BMOQ_CONFIG_CONV_KERNEL, 3);
    cursor = add_u32(config, cursor, BMOQ_CONFIG_MOE_INTERMEDIATE_SIZE, 2);
    cursor = add_u32(config, cursor, BMOQ_CONFIG_DENSE_INTERMEDIATE_SIZE, 3);
    cursor = add_u32(config, cursor, BMOQ_CONFIG_DENSE_MLP_INDEX, 1);
    cursor = add_u32(config, cursor, BMOQ_CONFIG_SHARED_EXPERTS_INKLING, 1);
    cursor = add_f32(config, cursor, BMOQ_CONFIG_RMS_NORM_EPS, 1.0e-6f);
    cursor = add_f32(config, cursor, BMOQ_CONFIG_ROUTED_SCALE, 2.0f);
    cursor = add_f32(config, cursor,
                     BMOQ_CONFIG_LOGITS_MUP_WIDTH_MULTIPLIER, 4.0f);
    cursor = add_u32(config, cursor, BMOQ_CONFIG_LOG_SCALING_N_FLOOR, 2);
    cursor = add_f32(config, cursor, BMOQ_CONFIG_LOG_SCALING_ALPHA, 0.5f);
    uint32_t stops[] = {6};
    cursor = add_entry(config, cursor, BMOQ_CONFIG_STOP_TOKEN_IDS,
                       BMOQ_CONFIG_U32_ARRAY, stops, 1);
    uint32_t local_layers[] = {0, 1};
    cursor = add_entry(config, cursor, BMOQ_CONFIG_LOCAL_LAYER_IDS,
                       BMOQ_CONFIG_U32_ARRAY, local_layers, 2);
    uint32_t sparse_layers[] = {1};
    cursor = add_entry(config, cursor, BMOQ_CONFIG_SPARSE_LAYER_IDS,
                       BMOQ_CONFIG_U32_ARRAY, sparse_layers, 1);
    assert(cursor == sizeof(config));

    FILE *file = fopen(path, "wb");
    assert(file);
    uint8_t header[BMOQ_HEADER_BYTES] = {0};
    memcpy(header, "BMOQ", 4);
    put_u16(header + 4, BMOQ_VERSION_EXTENDED_CONFIG);
    put_u32(header + 8, 0x01020304u);
    put_u32(header + 12, BMOQ_HEADER_BYTES);
    put_u32(header + 16, 1);
    put_u32(header + 20, 64);
    put_u64(header + 24, BMOQ_HEADER_BYTES);
    uint8_t *fixed = header + BMOQ_MODEL_CONFIG_OFFSET;
    put_u32(fixed, BMOQ_MODEL_ARCH_INKLING);
    put_u32(fixed + 8, 4);
    put_u32(fixed + 12, 3);
    put_u32(fixed + 16, 2);
    put_u32(fixed + 20, 2);
    put_u32(fixed + 24, 1);
    put_u32(fixed + 28, 4);
    put_u32(fixed + 32, 2);
    put_u32(fixed + 36, 10);
    put_u32(fixed + 40, 512);
    assert(fwrite(header, 1, sizeof(header), file) == sizeof(header));
    write_config_tensor_entry(file, sizeof(config));
    assert(fseeko(file, CONFIG_OFFSET, SEEK_SET) == 0);
    assert(fwrite(config, 1, sizeof(config), file) == sizeof(config));
    assert(fclose(file) == 0);
}

static void open_fixture(const char *path, coli_store_t **store,
                         coli_model_t *model)
{
    assert(coli_store_open_file(path, store) == COLI_OK);
    assert(coli_model_open(*store, model) == COLI_OK);
}

static void assert_close(float actual, float expected)
{
    assert(fabsf(actual - expected) < 1.0e-5f);
}

static void reference_attention(const float *query, const float *keys,
                                const float *values, uint32_t heads,
                                uint32_t kv_heads, uint32_t head_dim,
                                uint32_t token_count, uint32_t start,
                                const float *bias, float tau, float *output)
{
    const float scale = 1.0f / (float)head_dim;
    for (uint32_t head = 0; head < heads; ++head) {
        const uint32_t kv_head = head / (heads / kv_heads);
        float scores[4];
        float max_score = -1.0e30f;
        uint32_t used = 0;
        for (uint32_t token = start; token < token_count; ++token) {
            float score = 0.0f;
            const float *q = query + (size_t)head * head_dim;
            const float *k = keys + (size_t)token * kv_heads * head_dim +
                             (size_t)kv_head * head_dim;
            for (uint32_t d = 0; d < head_dim; ++d) score += q[d] * k[d];
            score = (score * scale + bias[token_count - 1u - token]) * tau;
            scores[used++] = score;
            if (score > max_score) max_score = score;
        }
        float sum = 0.0f;
        for (uint32_t i = 0; i < used; ++i) {
            scores[i] = expf(scores[i] - max_score);
            sum += scores[i];
        }
        float *out = output + (size_t)head * head_dim;
        memset(out, 0, head_dim * sizeof(*out));
        used = 0;
        for (uint32_t token = start; token < token_count; ++token) {
            const float weight = scores[used++] / sum;
            const float *v = values + (size_t)token * kv_heads * head_dim +
                             (size_t)kv_head * head_dim;
            for (uint32_t d = 0; d < head_dim; ++d) out[d] += weight * v[d];
        }
    }
}

static void test_config_and_state(const coli_inkling_config_t *config)
{
    assert(config->hidden_size == 4);
    assert(config->sliding_window == 2);
    assert(config->max_context_tokens == 512);
    assert(config->shared_experts == 1);
    assert(config->moe_intermediate_size == 2);
    assert(config->dense_intermediate_size == 3);
    assert(config->local_layer_count == 2);
    assert(config->sparse_layer_count == 1);
    assert(coli_inkling_is_stop_token(config, 6));
    assert(!coli_inkling_is_stop_token(config, 5));
    assert_close(coli_inkling_global_tau(config, 2), 1.0f);
    assert(coli_inkling_global_tau(config, 4) > 1.0f);
    coli_kv_cache_layout_t layout;
    assert(coli_inkling_state_layout(config, &layout) == COLI_OK);
    assert(layout.total_bytes > 0);
    assert(layout.max_tokens == 512);
}

static void test_attention(const coli_inkling_config_t *config, bool file_backed)
{
    coli_kv_cache_layout_t layout;
    assert(coli_inkling_state_layout(config, &layout) == COLI_OK);
    coli_kv_cache_t *cache = NULL;
    uint8_t *memory = NULL;
    char path[] = "/tmp/coli_inkling_kv_XXXXXX";
    int fd = -1;
    if (file_backed) {
        fd = mkstemp(path);
        assert(fd >= 0);
        close(fd);
        assert(coli_kv_cache_open_file(&layout, path, 64, &cache) ==
               COLI_OK);
    } else {
        memory = calloc(1, (size_t)layout.total_bytes);
        assert(memory);
        assert(coli_kv_cache_open_ram(&layout, memory,
                                      (size_t)layout.total_bytes, &cache) ==
               COLI_OK);
    }

    const float query[] = {0.5f, -0.25f, 0.75f, 0.125f};
    const float keys[] = {1.0f, 0.0f, 0.0f, 1.0f, -0.5f, 0.75f};
    const float values[] = {0.25f, 1.0f, 2.0f, -1.0f, 0.5f, 0.5f};
    const float bias[] = {0.0f, 0.1f, -0.2f, 0.0f};
    float output[4] = {0};
    float expected[4] = {0};
    float scores[512];
    float key_scratch[2];
    float value_scratch[2];

    for (uint32_t pos = 0; pos < 3; ++pos) {
        assert(coli_inkling_attention_decode(
                   config, cache, 0, false, pos, query, keys + pos * 2,
                   values + pos * 2, bias, 4, output, 4, scores, 512,
                   key_scratch, 2, value_scratch, 2) == COLI_OK);
    }
    reference_attention(query, keys, values, 2, 1, 2, 3, 0, bias,
                        coli_inkling_global_tau(config, 3), expected);
    for (size_t i = 0; i < 4; ++i) assert_close(output[i], expected[i]);

    for (uint32_t pos = 0; pos < 3; ++pos) {
        assert(coli_inkling_attention_decode(
                   config, cache, 1, true, pos, query, keys + pos * 2,
                   values + pos * 2, bias, 4, output, 4, scores, 512,
                   key_scratch, 2, value_scratch, 2) == COLI_OK);
    }
    reference_attention(query, keys, values, 2, 1, 2, 3, 1, bias, 1.0f,
                        expected);
    for (size_t i = 0; i < 4; ++i) assert_close(output[i], expected[i]);

    coli_kv_cache_stats_t stats;
    coli_kv_cache_stats(cache, &stats);
    assert(stats.write_count > 0);
    if (file_backed) assert(stats.resident_bytes == 64);
    coli_kv_cache_close(cache);
    free(memory);
    if (file_backed) assert(remove(path) == 0);
}

static void test_conv_norm_mlp_and_logits(void)
{
    const float input[] = {1.0f, -2.0f};
    const float weights[] = {0.5f, 0.25f, -0.125f, -1.0f, 0.5f, 0.25f};
    float state[4] = {2.0f, -1.0f, 1.0f, 0.5f};
    float output[2] = {0};
    assert(coli_inkling_short_conv_step(input, weights, 2, 3, state, 4,
                                        output, 2) == COLI_OK);
    assert_close(output[0], 1.0f + 0.5f + 0.5f + 0.125f);
    assert_close(output[1], -2.0f + 2.0f + 0.5f + 0.125f);
    assert_close(state[0], 1.0f);
    assert_close(state[2], -2.0f);

    float heads[] = {3.0f, 4.0f, 1.0f, 2.0f};
    const float norm[] = {1.0f, 2.0f};
    assert(coli_inkling_qk_rmsnorm(heads, norm, 2, 2, 1.0e-6f) ==
           COLI_OK);
    assert(heads[1] > heads[0]);

    const float gate[] = {1.0f, -0.5f, 0.25f};
    const float up[] = {2.0f, 1.0f, -1.0f};
    const float down[] = {1.0f, 0.5f, -0.25f, -1.0f, 0.25f, 0.5f};
    assert(coli_inkling_dense_swiglu(gate, up, down, 2, 3, 2.0f, output,
                                     2) == COLI_OK);
    assert(output[0] != 0.0f);
    assert(output[1] != 0.0f);

    const float hidden[] = {1.0f, 2.0f};
    const float lm_head[] = {0.0f, 1.0f, 3.0f, 0.0f, -100.0f, 100.0f};
    uint32_t token = 99;
    float logit = 0.0f;
    assert(coli_inkling_logits_argmax(hidden, lm_head, 3, 2, 2, 2.0f,
                                      &token, &logit) == COLI_OK);
    assert(token == 1);
    assert_close(logit, 1.5f);
}

static coli_status_t scripted_next_token(void *context,
                                         uint32_t previous_token_id,
                                         size_t position,
                                         uint32_t *out_token_id)
{
    (void)context;
    (void)position;
    *out_token_id = previous_token_id + 1u;
    return COLI_OK;
}

static void test_moe_and_generation(const coli_inkling_config_t *base)
{
    coli_inkling_config_t config = *base;
    config.num_experts = 4;
    config.experts_per_token = 2;
    config.shared_experts = 1;
    config.hidden_size = 2;
    config.route_scale = 2.0f;
    const float logits[] = {-2.0f, 1.0f, 0.5f, 4.0f, 0.25f};
    const float bias[] = {0.0f, 0.0f, 1.0f, -1.0f};
    coli_inkling_moe_stats_t stats;
    assert(coli_inkling_moe_route(&config, logits, bias, 0.5f, &stats) ==
           COLI_OK);
    assert(stats.selected_count == 2);
    assert(stats.selected_experts[0] == 2);
    assert(stats.selected_experts[1] == 1);
    const float experts[] = {1.0f, 0.0f, 0.0f, 2.0f};
    const float shared[] = {0.5f, 0.5f};
    float output[2] = {0};
    assert(coli_inkling_moe_combine(&config, &stats, experts, 4, shared, 2,
                                    output, 2) == COLI_OK);
    assert(output[0] > 0.0f);
    assert(output[1] > 0.0f);

    uint32_t generated[8] = {0};
    size_t count = 0;
    assert(coli_inkling_generate_greedy_stream(base, 3, 0, 8, generated, 8,
                                              &count, scripted_next_token,
                                              NULL, NULL, NULL) ==
           COLI_OK);
    assert(count == 2);
    assert(generated[0] == 4);
    assert(generated[1] == 5);
}

int main(void)
{
    char path[] = "/tmp/coli_inkling_model_XXXXXX";
    int fd = mkstemp(path);
    assert(fd >= 0);
    close(fd);
    write_fixture(path);

    coli_store_t *store = NULL;
    coli_model_t model;
    open_fixture(path, &store, &model);
    coli_inkling_config_t config;
    assert(coli_inkling_config_load(&model, &config) == COLI_OK);
    test_config_and_state(&config);
    test_attention(&config, false);
    test_attention(&config, true);
    test_conv_norm_mlp_and_logits();
    test_moe_and_generation(&config);
    coli_model_close(&model);
    coli_store_close(store);
    assert(remove(path) == 0);
    puts("coli_inkling ok");
    return 0;
}
