#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

#include "coli_gemma_generate.h"
#include "coli_spm.h"

#define main coli_gemma_fixture_main
#include "test_coli_gemma.c"
#undef main

typedef struct {
    uint8_t bytes[8];
    size_t count;
    size_t cancel_checks;
    size_t encode_calls;
    size_t decode_calls;
} capture_t;

typedef struct {
    coli_spm_t *spm;
} spm_tokenizer_t;

static coli_status_t encode_prompt(void *context,
                                   coli_store_t *tokenizer_store,
                                   const uint8_t *prompt,
                                   size_t prompt_bytes,
                                   uint32_t *token_ids,
                                   size_t token_capacity,
                                   size_t *out_token_count)
{
    (void)tokenizer_store;
    capture_t *capture = context;
    ++capture->encode_calls;
    if (!prompt || prompt_bytes != 1 || prompt[0] != 'z' ||
        token_capacity < 1 || !token_ids || !out_token_count)
        return COLI_ERR_ARGUMENT;
    token_ids[0] = 0;
    *out_token_count = 1;
    return COLI_OK;
}

static coli_status_t decode_token(void *context,
                                  coli_store_t *tokenizer_store,
                                  const uint32_t *token_ids,
                                  size_t token_count,
                                  uint8_t *text,
                                  size_t text_capacity,
                                  size_t *out_text_bytes)
{
    (void)tokenizer_store;
    capture_t *capture = context;
    ++capture->decode_calls;
    if (!token_ids || token_count != 1 || !text || text_capacity < 1 ||
        !out_text_bytes)
        return COLI_ERR_ARGUMENT;
    text[0] = (uint8_t)('a' + token_ids[0]);
    *out_text_bytes = 1;
    return COLI_OK;
}

static bool should_cancel(void *context)
{
    capture_t *capture = context;
    ++capture->cancel_checks;
    return false;
}

static void yield_once(void *context)
{
    (void)context;
}

static void capture_chunk(void *context, const uint8_t *bytes, size_t count)
{
    capture_t *capture = context;
    assert(capture->count + count <= sizeof(capture->bytes));
    memcpy(capture->bytes + capture->count, bytes, count);
    capture->count += count;
}

static coli_status_t spm_encode_prompt(void *context,
                                       coli_store_t *tokenizer_store,
                                       const uint8_t *prompt,
                                       size_t prompt_bytes,
                                       uint32_t *token_ids,
                                       size_t token_capacity,
                                       size_t *out_token_count)
{
    (void)tokenizer_store;
    spm_tokenizer_t *tokenizer = context;
    return coli_spm_encode(tokenizer->spm, prompt, prompt_bytes, token_ids,
                           token_capacity, out_token_count,
                           COLI_SPM_ENCODE_DEFAULT);
}

static coli_status_t spm_decode_token(void *context,
                                      coli_store_t *tokenizer_store,
                                      const uint32_t *token_ids,
                                      size_t token_count,
                                      uint8_t *text,
                                      size_t text_capacity,
                                      size_t *out_text_bytes)
{
    (void)tokenizer_store;
    spm_tokenizer_t *tokenizer = context;
    return coli_spm_decode(tokenizer->spm, token_ids, token_count, text,
                           text_capacity, out_text_bytes);
}

static void stdout_chunk(void *context, const uint8_t *bytes, size_t count)
{
    (void)context;
    assert(fwrite(bytes, 1, count, stdout) == count);
}

static int run_real_smoke(const char *model_path,
                          const char *tokenizer_path,
                          const char *prompt)
{
    coli_store_t *model_store = NULL;
    coli_store_t *tokenizer_store = NULL;
    coli_spm_t spm;
    memset(&spm, 0, sizeof(spm));
    assert(coli_store_open_file(model_path, &model_store) == COLI_OK);
    assert(coli_store_open_file(tokenizer_path, &tokenizer_store) == COLI_OK);
    assert(coli_spm_open(tokenizer_store, &spm) == COLI_OK);

    spm_tokenizer_t spm_context = {.spm = &spm};
    coli_gemma_tokenizer_t tokenizer = {
        .encode = spm_encode_prompt,
        .decode = spm_decode_token,
        .context = &spm_context,
    };
    coli_gemma_generate_config_t config = {
        .prompt = (const uint8_t *)prompt,
        .prompt_bytes = strlen(prompt),
        .context_tokens = 33,
        .max_prompt_tokens = 32,
        .max_new_tokens = 1,
        .workspace_bytes = 131072,
        .decoded_chunk_bytes = 256,
        .log_chunk = stdout_chunk,
    };
    coli_gemma_generate_result_t result;
    coli_status_t status = coli_gemma_generate_with_tokenizer(
        model_store, tokenizer_store, &tokenizer, &config, &result);
    printf(
        "\nstatus=%d stage=%u prompt_tokens=%zu generated=%zu decoded=%zu "
        "kv=%zu workspace=%zu last=%u\n",
        (int)status, (unsigned)result.stage, result.prompt_tokens,
        result.generated_tokens, result.decoded_bytes, result.kv_cache_bytes,
        result.workspace_bytes, (unsigned)result.last_token_id);

    coli_spm_close(&spm);
    coli_store_close(tokenizer_store);
    coli_store_close(model_store);
    return status == COLI_OK && result.generated_tokens == 1 ? 0 : 1;
}

int main(int argc, char **argv)
{
    if (argc == 3 || argc == 4)
        return run_real_smoke(argv[1], argv[2], argc == 4 ? argv[3] : "hi");

    char model_path[] = "/tmp/coli-gemma-generate-model-XXXXXX";
    int fd = mkstemp(model_path);
    assert(fd >= 0);
    close(fd);
    write_fixture(model_path);

    coli_store_t *model_store = NULL;
    assert(coli_store_open_file(model_path, &model_store) == COLI_OK);
    const uint8_t prompt[] = {'z'};
    capture_t capture = {0};
    coli_gemma_tokenizer_t tokenizer = {
        .encode = encode_prompt,
        .decode = decode_token,
        .context = &capture,
    };
    coli_gemma_generate_config_t config = {
        .prompt = prompt,
        .prompt_bytes = sizeof(prompt),
        .context_tokens = 3,
        .max_prompt_tokens = 1,
        .max_new_tokens = 2,
        .workspace_bytes = 8192,
        .decoded_chunk_bytes = 4,
        .should_cancel = should_cancel,
        .yield = yield_once,
        .log_chunk = capture_chunk,
        .callback_context = &capture,
    };
    coli_gemma_generate_result_t result;
    assert(coli_gemma_generate_with_tokenizer(model_store, NULL, &tokenizer,
                                              &config, &result) == COLI_OK);
    assert(result.stage == COLI_GENERATE_STAGE_DONE);
    assert(result.prompt_tokens == 1);
    assert(result.generated_tokens == 2);
    assert(result.decoded_bytes == 2);
    assert(result.kv_cache_bytes > 0);
    assert(result.workspace_bytes == config.workspace_bytes);
    assert(result.yield_calls >= 4);
    assert(result.last_token_id == 0);
    assert(capture.cancel_checks >= 4);
    assert(capture.encode_calls == 1);
    assert(capture.decode_calls == 2);
    assert(capture.count == 2);
    assert(capture.bytes[0] == 'a');
    assert(capture.bytes[1] == 'a');

    coli_store_close(model_store);
    unlink(model_path);
    puts("coli_gemma_generate prompt-to-text callbacks: PASS");
    return 0;
}
