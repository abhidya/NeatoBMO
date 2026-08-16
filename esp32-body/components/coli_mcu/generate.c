#include "coli_generate.h"

#include <stdlib.h>
#include <string.h>

#include "coli_olmoe.h"
#include "coli_kv_cache.h"

#ifdef ESP_PLATFORM
#include "esp_heap_caps.h"
#endif

static void *generate_alloc(size_t bytes)
{
#ifdef ESP_PLATFORM
    return heap_caps_malloc(bytes, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
#else
    return malloc(bytes);
#endif
}

static void generate_free(void *ptr)
{
    free(ptr);
}

static bool cancelled(const coli_generate_config_t *config)
{
    return config->should_cancel &&
           config->should_cancel(config->callback_context);
}

static void maybe_yield(const coli_generate_config_t *config)
{
    if (config->yield) config->yield(config->callback_context);
}

static coli_status_t fail(coli_generate_result_t *result,
                          coli_generate_stage_t stage,
                          coli_status_t status)
{
    result->stage = stage;
    result->status = status;
    return status;
}

typedef struct {
    const coli_generate_config_t *config;
    coli_generate_result_t *result;
    const coli_tokenizer_t *tokenizer;
    uint8_t *decoded;
} generate_stream_t;

static coli_status_t stream_token(void *context, uint32_t token_id,
                                  size_t generated_index)
{
    generate_stream_t *stream = context;
    size_t decoded_bytes = 0;
    coli_status_t status = coli_tokenizer_decode(
        stream->tokenizer, &token_id, 1, stream->decoded,
        stream->config->decoded_chunk_bytes, &decoded_bytes);
    if (status != COLI_OK) return status;

    stream->result->generated_tokens = generated_index + 1u;
    stream->result->last_token_id = token_id;
    stream->result->decoded_bytes += decoded_bytes;
    if (stream->config->log_chunk && decoded_bytes)
        stream->config->log_chunk(stream->config->callback_context,
                                  stream->decoded, decoded_bytes);
    maybe_yield(stream->config);
    if (cancelled(stream->config)) {
        stream->result->stage = COLI_GENERATE_STAGE_CANCELLED;
        return COLI_ERR_REMOVED;
    }
    return COLI_OK;
}

coli_status_t coli_generate_olmoe_greedy(coli_store_t *model_store,
                                         coli_store_t *tokenizer_store,
                                         const coli_generate_config_t *config,
                                         coli_generate_result_t *result)
{
    if (!model_store || !tokenizer_store || !config || !result ||
        !config->prompt || config->prompt_bytes == 0 ||
        config->context_tokens == 0 || config->max_prompt_tokens == 0 ||
        config->max_new_tokens == 0 ||
        config->context_tokens < config->max_prompt_tokens ||
        config->context_tokens < config->max_prompt_tokens + config->max_new_tokens ||
        config->workspace_bytes == 0 || config->decoded_chunk_bytes == 0 ||
        (config->kv_cache_path && config->kv_page_bytes == 0))
        return COLI_ERR_ARGUMENT;

    memset(result, 0, sizeof(*result));
    result->stage = COLI_GENERATE_STAGE_OPEN_MODEL;
    coli_model_t model;
    coli_status_t status = coli_model_open(model_store, &model);
    if (status != COLI_OK) return fail(result, result->stage, status);
    if (model.config.arch != BMOQ_MODEL_ARCH_OLMOE ||
        model.config.hidden_size == 0 ||
        model.config.num_attention_heads == 0 ||
        model.config.hidden_size % model.config.num_attention_heads != 0) {
        coli_model_close(&model);
        return fail(result, COLI_GENERATE_STAGE_OPEN_MODEL, COLI_ERR_FORMAT);
    }

    result->stage = COLI_GENERATE_STAGE_OPEN_TOKENIZER;
    coli_tokenizer_t tokenizer;
    status = coli_tokenizer_open(tokenizer_store, &tokenizer);
    if (status != COLI_OK) {
        coli_model_close(&model);
        return fail(result, result->stage, status);
    }

    result->stage = COLI_GENERATE_STAGE_ALLOCATE;
    uint32_t *prompt_ids =
        generate_alloc(config->max_prompt_tokens * sizeof(*prompt_ids));
    uint32_t *output_ids =
        generate_alloc(config->context_tokens * sizeof(*output_ids));
    uint8_t *decoded =
        generate_alloc(config->decoded_chunk_bytes);
    void *workspace = generate_alloc(config->workspace_bytes);
    uint8_t *kv_memory = NULL;
    coli_kv_cache_t *kv_cache = NULL;
    coli_kv_cache_layout_t kv_layout;
    status = coli_ops_kv_cache_layout(
        model.config.num_hidden_layers, model.config.num_attention_heads,
        model.config.hidden_size / model.config.num_attention_heads,
        (uint32_t)config->context_tokens, sizeof(float), &kv_layout);
    if (status != COLI_OK) goto cleanup;
    if (config->kv_cache_path) {
        status = coli_kv_cache_open_file(&kv_layout, config->kv_cache_path,
                                         config->kv_page_bytes, &kv_cache);
    } else if (kv_layout.total_bytes <= SIZE_MAX) {
        kv_memory = generate_alloc((size_t)kv_layout.total_bytes);
        if (kv_memory)
            status = coli_kv_cache_open_ram(&kv_layout, kv_memory,
                                            (size_t)kv_layout.total_bytes,
                                            &kv_cache);
        else
            status = COLI_ERR_NO_MEMORY;
    } else {
        status = COLI_ERR_RANGE;
    }
    if (!prompt_ids || !output_ids || !decoded || !workspace || !kv_cache) {
        if (status == COLI_OK) status = COLI_ERR_NO_MEMORY;
        goto cleanup;
    }
    result->kv_cache_bytes = (size_t)kv_layout.total_bytes;
    coli_kv_cache_stats_t kv_stats;
    coli_kv_cache_stats(kv_cache, &kv_stats);
    result->kv_cache_resident_bytes = kv_stats.resident_bytes;
    result->workspace_bytes = config->workspace_bytes;
    maybe_yield(config);
    if (cancelled(config)) {
        status = COLI_ERR_REMOVED;
        result->stage = COLI_GENERATE_STAGE_CANCELLED;
        goto cleanup;
    }

    result->stage = COLI_GENERATE_STAGE_ENCODE_PROMPT;
    size_t prompt_count = 0;
    status = coli_tokenizer_encode(&tokenizer, config->prompt,
                                   config->prompt_bytes, prompt_ids,
                                   config->max_prompt_tokens, &prompt_count,
                                   COLI_TOKENIZER_ENCODE_DEFAULT);
    if (status != COLI_OK) goto cleanup;
    if (prompt_count == 0) {
        status = COLI_ERR_RANGE;
        goto cleanup;
    }
    result->prompt_tokens = prompt_count;
    maybe_yield(config);

    result->stage = COLI_GENERATE_STAGE_GENERATE;
    size_t output_count = 0;
    coli_olmoe_generate_stats_t generate_stats;
    generate_stream_t stream = {
        .config = config,
        .result = result,
        .tokenizer = &tokenizer,
        .decoded = decoded,
    };
    status = coli_olmoe_generate_greedy_stream(
        &model, prompt_ids, prompt_count, output_ids, config->context_tokens,
        config->max_new_tokens, &output_count, kv_cache, workspace,
        config->workspace_bytes, stream_token, &stream, &generate_stats);
    if (status != COLI_OK) goto cleanup;
    result->generated_tokens = generate_stats.generated_tokens;
    if (output_count > 0) result->last_token_id = output_ids[output_count - 1u];
    maybe_yield(config);
    if (cancelled(config)) {
        status = COLI_ERR_REMOVED;
        result->stage = COLI_GENERATE_STAGE_CANCELLED;
        goto cleanup;
    }

    result->stage = COLI_GENERATE_STAGE_DONE;
    result->status = COLI_OK;

cleanup:
    generate_free(workspace);
    generate_free(decoded);
    generate_free(output_ids);
    generate_free(prompt_ids);
    coli_kv_cache_close(kv_cache);
    generate_free(kv_memory);
    coli_tokenizer_close(&tokenizer);
    coli_model_close(&model);
    if (status != COLI_OK && result->stage != COLI_GENERATE_STAGE_CANCELLED)
        fail(result, COLI_GENERATE_STAGE_ERROR, status);
    else
        result->status = status;
    return status;
}
