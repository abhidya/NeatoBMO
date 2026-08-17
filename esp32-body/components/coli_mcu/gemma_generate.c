#include "coli_gemma_generate.h"

#include <stdlib.h>
#include <string.h>

#include "coli_gemma.h"

#ifdef ESP_PLATFORM
#include "esp_heap_caps.h"
#endif

static void *gemma_generate_alloc(size_t bytes)
{
#ifdef ESP_PLATFORM
    return heap_caps_malloc(bytes, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
#else
    return malloc(bytes);
#endif
}

static void gemma_generate_free(void *ptr)
{
    free(ptr);
}

static bool cancelled(const coli_gemma_generate_config_t *config)
{
    return config->should_cancel &&
           config->should_cancel(config->callback_context);
}

static void maybe_yield(const coli_gemma_generate_config_t *config,
                        coli_gemma_generate_result_t *result)
{
    if (config->yield) {
        config->yield(config->callback_context);
        ++result->yield_calls;
    }
}

static coli_status_t fail(coli_gemma_generate_result_t *result,
                          coli_generate_stage_t stage,
                          coli_status_t status)
{
    result->stage = stage;
    result->status = status;
    return status;
}

static bool mul_size(size_t left, size_t right, size_t *out)
{
    if (!out) return false;
    if (left != 0 && right > SIZE_MAX / left) return false;
    *out = left * right;
    return true;
}

static coli_status_t infer_head_dim(const coli_model_t *model,
                                    uint32_t *out_head_dim)
{
    if (!model || !out_head_dim || model->config.num_key_value_heads == 0)
        return COLI_ERR_ARGUMENT;
    const bmoq_tensor_t *k_proj =
        coli_model_find(model, coli_gemma_k_proj_id(0));
    if (!k_proj || k_proj->layout != BMOQ_LAYOUT_Q4_ROW_MAJOR ||
        k_proj->dimensions[0] == 0 ||
        k_proj->dimensions[0] % model->config.num_key_value_heads != 0)
        return COLI_ERR_FORMAT;
    *out_head_dim = k_proj->dimensions[0] / model->config.num_key_value_heads;
    return *out_head_dim == 0 ? COLI_ERR_FORMAT : COLI_OK;
}

coli_status_t coli_gemma_generate_with_tokenizer(
    coli_store_t *model_store,
    coli_store_t *tokenizer_store,
    const coli_gemma_tokenizer_t *tokenizer,
    const coli_gemma_generate_config_t *config,
    coli_gemma_generate_result_t *result)
{
    if (!model_store || !tokenizer || !tokenizer->encode ||
        !tokenizer->decode || !config || !result || !config->prompt ||
        config->prompt_bytes == 0 || config->context_tokens == 0 ||
        config->max_prompt_tokens == 0 || config->max_new_tokens == 0 ||
        config->context_tokens < config->max_prompt_tokens ||
        config->context_tokens <
            config->max_prompt_tokens + config->max_new_tokens ||
        config->workspace_bytes == 0 || config->decoded_chunk_bytes == 0)
        return COLI_ERR_ARGUMENT;

    memset(result, 0, sizeof(*result));
    result->stage = COLI_GENERATE_STAGE_OPEN_MODEL;
    coli_model_t model;
    coli_status_t status = coli_model_open(model_store, &model);
    if (status != COLI_OK) return fail(result, result->stage, status);
    if (model.config.arch != COLI_GEMMA3_ARCH_ID ||
        model.config.hidden_size == 0 ||
        model.config.num_hidden_layers == 0 ||
        model.config.num_attention_heads == 0 ||
        model.config.num_key_value_heads == 0 ||
        model.config.num_attention_heads % model.config.num_key_value_heads !=
            0) {
        coli_model_close(&model);
        return fail(result, COLI_GENERATE_STAGE_OPEN_MODEL, COLI_ERR_FORMAT);
    }

    uint32_t head_dim = 0;
    status = infer_head_dim(&model, &head_dim);
    if (status != COLI_OK) {
        coli_model_close(&model);
        return fail(result, COLI_GENERATE_STAGE_OPEN_MODEL, status);
    }

    result->stage = COLI_GENERATE_STAGE_ALLOCATE;
    size_t prompt_bytes = 0;
    size_t output_bytes = 0;
    if (!mul_size(config->max_prompt_tokens, sizeof(uint32_t),
                  &prompt_bytes) ||
        !mul_size(config->context_tokens, sizeof(uint32_t), &output_bytes)) {
        coli_model_close(&model);
        return fail(result, result->stage, COLI_ERR_RANGE);
    }
    uint32_t *prompt_ids = gemma_generate_alloc(prompt_bytes);
    uint32_t *output_ids = gemma_generate_alloc(output_bytes);
    uint8_t *decoded = gemma_generate_alloc(config->decoded_chunk_bytes);
    void *workspace = gemma_generate_alloc(config->workspace_bytes);
    uint8_t *kv_cache = NULL;
    coli_kv_cache_layout_t kv_layout;
    status = coli_ops_kv_cache_layout(
        model.config.num_hidden_layers, model.config.num_key_value_heads,
        head_dim, (uint32_t)config->context_tokens, sizeof(float), &kv_layout);
    if (status == COLI_OK && kv_layout.total_bytes <= (uint64_t)SIZE_MAX)
        kv_cache = gemma_generate_alloc((size_t)kv_layout.total_bytes);
    if (status != COLI_OK || !prompt_ids || !output_ids || !decoded ||
        !workspace || !kv_cache) {
        if (status == COLI_OK) status = COLI_ERR_NO_MEMORY;
        goto cleanup;
    }
    memset(kv_cache, 0, (size_t)kv_layout.total_bytes);
    result->kv_cache_bytes = (size_t)kv_layout.total_bytes;
    result->workspace_bytes = config->workspace_bytes;
    maybe_yield(config, result);
    if (cancelled(config)) {
        status = COLI_ERR_REMOVED;
        result->stage = COLI_GENERATE_STAGE_CANCELLED;
        goto cleanup;
    }

    result->stage = COLI_GENERATE_STAGE_ENCODE_PROMPT;
    size_t prompt_count = 0;
    status = tokenizer->encode(tokenizer->context, tokenizer_store,
                               config->prompt, config->prompt_bytes,
                               prompt_ids, config->max_prompt_tokens,
                               &prompt_count);
    if (status != COLI_OK) goto cleanup;
    if (prompt_count == 0 || prompt_count > config->max_prompt_tokens) {
        status = COLI_ERR_RANGE;
        goto cleanup;
    }
    result->prompt_tokens = prompt_count;
    maybe_yield(config, result);
    if (cancelled(config)) {
        status = COLI_ERR_REMOVED;
        result->stage = COLI_GENERATE_STAGE_CANCELLED;
        goto cleanup;
    }

    result->stage = COLI_GENERATE_STAGE_GENERATE;
    size_t output_count = 0;
    coli_gemma_generate_stats_t generate_stats;
    status = coli_gemma_generate_greedy(
        &model, prompt_ids, prompt_count, output_ids, config->context_tokens,
        config->max_new_tokens, &output_count, kv_cache,
        (size_t)kv_layout.total_bytes, &kv_layout, workspace,
        config->workspace_bytes, &generate_stats);
    if (status != COLI_OK) goto cleanup;
    result->generated_tokens = generate_stats.generated_tokens;
    if (output_count > 0) result->last_token_id = output_ids[output_count - 1u];
    maybe_yield(config, result);
    if (cancelled(config)) {
        status = COLI_ERR_REMOVED;
        result->stage = COLI_GENERATE_STAGE_CANCELLED;
        goto cleanup;
    }

    result->stage = COLI_GENERATE_STAGE_DECODE;
    for (size_t i = prompt_count; i < output_count; ++i) {
        size_t decoded_bytes = 0;
        status = tokenizer->decode(tokenizer->context, tokenizer_store,
                                   &output_ids[i], 1, decoded,
                                   config->decoded_chunk_bytes,
                                   &decoded_bytes);
        if (status != COLI_OK) goto cleanup;
        result->decoded_bytes += decoded_bytes;
        if (config->log_chunk && decoded_bytes)
            config->log_chunk(config->callback_context, decoded,
                              decoded_bytes);
        maybe_yield(config, result);
        if (cancelled(config)) {
            status = COLI_ERR_REMOVED;
            result->stage = COLI_GENERATE_STAGE_CANCELLED;
            goto cleanup;
        }
    }

    result->stage = COLI_GENERATE_STAGE_DONE;
    result->status = COLI_OK;

cleanup:
    gemma_generate_free(kv_cache);
    gemma_generate_free(workspace);
    gemma_generate_free(decoded);
    gemma_generate_free(output_ids);
    gemma_generate_free(prompt_ids);
    coli_model_close(&model);
    if (status != COLI_OK && result->stage != COLI_GENERATE_STAGE_CANCELLED)
        fail(result, COLI_GENERATE_STAGE_ERROR, status);
    else
        result->status = status;
    return status;
}
