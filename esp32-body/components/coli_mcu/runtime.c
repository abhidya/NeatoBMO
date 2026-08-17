#include "coli_runtime.h"

#include <string.h>

#include "coli_gemma_generate.h"
#include "coli_model.h"
#include "coli_spm.h"
#include "coli_store.h"

/* Gemma ships a SentencePiece tokenizer rather than CTOK; adapt it to the
 * engine's tokenizer callbacks so the one runtime entry point serves every
 * architecture the firmware can open. */
/* Gemma instruction-tuned checkpoints answer a *conversation*, not a text
 * completion. Given a bare prompt they continue it: "Hello BMO!" returns
 * "The word 'beloved'". Wrapped in the turn markers below the same weights
 * answer "Hello! I'm BMO. How can I help you today?".
 *
 * The SPM tokenizer only treats BOS/EOS/PAD as special, so "<start_of_turn>"
 * as text would byte-fallback into garbage; the marker ids are injected
 * directly instead. They are Gemma-specific, so they are verified against the
 * vocabulary before use and templating is skipped if they do not match. */
#define GEMMA_START_OF_TURN 105u
#define GEMMA_END_OF_TURN 106u
#define GEMMA_NEWLINE 107u

static bool gemma_markers_present(const coli_spm_t *spm)
{
    uint8_t text[32];
    size_t bytes = 0;
    const uint32_t start = GEMMA_START_OF_TURN;
    if (coli_spm_decode(spm, &start, 1, text, sizeof(text), &bytes) != COLI_OK)
        return false;
    return bytes == 15u && memcmp(text, "<start_of_turn>", 15u) == 0;
}

static coli_status_t push_id(uint32_t id, uint32_t *ids, size_t capacity,
                             size_t *count)
{
    if (*count >= capacity) return COLI_ERR_RANGE;
    ids[(*count)++] = id;
    return COLI_OK;
}

static coli_status_t runtime_spm_encode(
    void *context, coli_store_t *store, const uint8_t *prompt,
    size_t prompt_bytes, uint32_t *token_ids, size_t token_capacity,
    size_t *out_token_count)
{
    (void)store;
    const coli_spm_t *spm = (const coli_spm_t *)context;
    if (!gemma_markers_present(spm))
        return coli_spm_encode(spm, prompt, prompt_bytes, token_ids,
                               token_capacity, out_token_count,
                               COLI_SPM_ENCODE_ADD_BOS);

    size_t count = 0;
    coli_status_t status = COLI_OK;
    /* <bos><start_of_turn>user\n */
    if ((status = push_id(spm->bos_token_id, token_ids, token_capacity, &count)) != COLI_OK ||
        (status = push_id(GEMMA_START_OF_TURN, token_ids, token_capacity, &count)) != COLI_OK)
        return status;
    size_t role_count = 0;
    status = coli_spm_encode(spm, (const uint8_t *)"user\n", 5u,
                             token_ids + count, token_capacity - count,
                             &role_count, COLI_SPM_ENCODE_DEFAULT);
    if (status != COLI_OK) return status;
    count += role_count;

    size_t body_count = 0;
    status = coli_spm_encode(spm, prompt, prompt_bytes, token_ids + count,
                             token_capacity - count, &body_count,
                             COLI_SPM_ENCODE_DEFAULT);
    if (status != COLI_OK) return status;
    count += body_count;

    /* <end_of_turn>\n<start_of_turn>model\n */
    if ((status = push_id(GEMMA_END_OF_TURN, token_ids, token_capacity, &count)) != COLI_OK ||
        (status = push_id(GEMMA_NEWLINE, token_ids, token_capacity, &count)) != COLI_OK ||
        (status = push_id(GEMMA_START_OF_TURN, token_ids, token_capacity, &count)) != COLI_OK)
        return status;
    size_t tail_count = 0;
    status = coli_spm_encode(spm, (const uint8_t *)"model\n", 6u,
                             token_ids + count, token_capacity - count,
                             &tail_count, COLI_SPM_ENCODE_DEFAULT);
    if (status != COLI_OK) return status;
    count += tail_count;

    *out_token_count = count;
    return COLI_OK;
}

static coli_status_t runtime_spm_decode(
    void *context, coli_store_t *store, const uint32_t *token_ids,
    size_t token_count, uint8_t *text, size_t text_capacity,
    size_t *out_text_bytes)
{
    (void)store;
    return coli_spm_decode((const coli_spm_t *)context, token_ids, token_count,
                           text, text_capacity, out_text_bytes);
}

static coli_status_t runtime_generate_gemma(
    coli_store_t *model_store, coli_store_t *tokenizer_store,
    const coli_generate_config_t *generation,
    coli_generate_result_t *out_result)
{
    coli_spm_t spm;
    coli_status_t status = coli_spm_open(tokenizer_store, &spm);
    if (status != COLI_OK) return status;

    const coli_gemma_tokenizer_t tokenizer = {
        .encode = runtime_spm_encode,
        .decode = runtime_spm_decode,
        .context = &spm,
    };
    const coli_gemma_generate_config_t config = {
        .prompt = generation->prompt,
        .prompt_bytes = generation->prompt_bytes,
        .context_tokens = generation->context_tokens,
        .max_prompt_tokens = generation->max_prompt_tokens,
        .max_new_tokens = generation->max_new_tokens,
        .workspace_bytes = generation->workspace_bytes,
        .decoded_chunk_bytes = generation->decoded_chunk_bytes,
        .should_cancel = generation->should_cancel,
        .yield = generation->yield,
        .log_chunk = generation->log_chunk,
        .callback_context = generation->callback_context,
    };
    coli_gemma_generate_result_t result = {0};
    status = coli_gemma_generate_with_tokenizer(model_store, tokenizer_store,
                                                &tokenizer, &config, &result);
    coli_spm_close(&spm);
    out_result->stage = result.stage;
    out_result->status = result.status;
    out_result->prompt_tokens = result.prompt_tokens;
    out_result->generated_tokens = result.generated_tokens;
    out_result->decoded_bytes = result.decoded_bytes;
    out_result->kv_cache_bytes = result.kv_cache_bytes;
    out_result->workspace_bytes = result.workspace_bytes;
    out_result->last_token_id = result.last_token_id;
    return status;
}

coli_status_t coli_runtime_generate(const coli_runtime_request_t *request,
                                    coli_runtime_result_t *result)
{
    if (!request || !result || !request->model_path ||
        !request->tokenizer_path)
        return COLI_ERR_ARGUMENT;

    memset(result, 0, sizeof(*result));
    coli_store_t *model_store = NULL;
    coli_store_t *tokenizer_store = NULL;
    coli_status_t status =
        coli_store_open_file(request->model_path, &model_store);
    if (status != COLI_OK) return status;

    coli_model_t model;
    status = coli_model_open(model_store, &model);
    if (status != COLI_OK) goto cleanup;
    result->architecture = model.config.arch;
    coli_model_close(&model);

    if (result->architecture != BMOQ_MODEL_ARCH_OLMOE &&
        result->architecture != BMOQ_MODEL_ARCH_GLM52 &&
        result->architecture != BMOQ_MODEL_ARCH_GEMMA3) {
        status = COLI_ERR_UNSUPPORTED;
        goto cleanup;
    }

    status = coli_store_open_file(request->tokenizer_path, &tokenizer_store);
    if (status != COLI_OK) goto cleanup;
    if (result->architecture == BMOQ_MODEL_ARCH_GEMMA3)
        status = runtime_generate_gemma(model_store, tokenizer_store,
                                        &request->generation,
                                        &result->generation);
    else if (result->architecture == BMOQ_MODEL_ARCH_GLM52)
        status = coli_generate_glm52_greedy(
            model_store, tokenizer_store, &request->generation,
            &result->generation);
    else
        status = coli_generate_olmoe_greedy(
            model_store, tokenizer_store, &request->generation,
            &result->generation);

cleanup:
    coli_store_close(tokenizer_store);
    coli_store_close(model_store);
    return status;
}
