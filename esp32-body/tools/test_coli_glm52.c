#include <assert.h>
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "coli_glm52.h"
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
    put_u32(header + 16, 1);
    put_u32(header + 20, 64);
    put_u64(header + 24, BMOQ_HEADER_BYTES);
    uint8_t *fixed = header + BMOQ_MODEL_CONFIG_OFFSET;
    put_u32(fixed, BMOQ_MODEL_ARCH_GLM52);
    put_u32(fixed + 8, 64);
    put_u32(fixed + 12, 512);
    put_u32(fixed + 16, 2);
    put_u32(fixed + 20, 4);
    put_u32(fixed + 28, 8);
    put_u32(fixed + 32, 2);
    put_u32(fixed + 36, 1024);
    put_u32(fixed + 40, 4096);
    put_u32(fixed + 44, 10000);
    assert(fwrite(header, 1, sizeof(header), file) == sizeof(header));

    uint8_t tensor[64] = {0};
    put_u32(tensor, BMOQ_CONFIG_TENSOR_ID);
    put_u32(tensor + 8, sizeof(config));
    put_u32(tensor + 12, 1);
    put_u32(tensor + 16, 1);
    put_u32(tensor + 20, 1);
    put_u64(tensor + 24, CONFIG_OFFSET);
    put_u64(tensor + 32, sizeof(config));
    memcpy(tensor + 48, "config.v2", 9);
    assert(fwrite(tensor, 1, sizeof(tensor), file) == sizeof(tensor));
    assert(fseeko(file, CONFIG_OFFSET, SEEK_SET) == 0);
    assert(fwrite(config, 1, sizeof(config), file) == sizeof(config));
    assert(fclose(file) == 0);
}

int main(void)
{
    char path[] = "/tmp/coli-glm52-XXXXXX";
    int fd = mkstemp(path);
    assert(fd >= 0);
    close(fd);
    write_fixture(path);

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
    assert(layout.layers == 2);
    assert(layout.max_tokens == 4096);
    assert(layout.key_token_bytes == 12u * sizeof(float));
    assert(layout.value_token_bytes == 2u * sizeof(float));
    assert(layout.total_bytes ==
           2u * 4096u * (12u + 2u) * sizeof(float));

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

    coli_model_close(&model);
    coli_store_close(store);
    unlink(path);
    puts("GLM-5.2 BMOQ config and compressed state layout: PASS");
    return 0;
}
