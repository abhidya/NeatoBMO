/* Host driver for the exact firmware inference path.
 *
 * Runs coli_runtime_generate — the same entry point web.c calls on the
 * ESP32 — against a real model.bmoq and tokenizer, streaming decoded text to
 * stdout. This is the offline prompt-to-text proof for a model file before it
 * ships to the SSD: same runtime, same kernels, same bounded workspace; only
 * the storage backend differs (host file instead of USB-MSC).
 *
 *   tools/build/coli-run model.bmoq tokenizer "prompt" [max_new_tokens]
 */
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "coli_runtime.h"

static void log_chunk(void *context, const uint8_t *bytes, size_t byte_count)
{
    (void)context;
    fwrite(bytes, 1, byte_count, stdout);
    fflush(stdout);
}

int main(int argc, char **argv)
{
    if (argc < 4) {
        fprintf(stderr,
                "usage: %s model.bmoq tokenizer \"prompt\" [max_new_tokens]\n",
                argv[0]);
        return 2;
    }
    const char *prompt = argv[3];
    size_t max_new_tokens = argc > 4 ? (size_t)strtoul(argv[4], NULL, 10) : 32;

    /* Mirror the firmware's bounded budgets so a host pass predicts device
     * behaviour: fixed workspace, bounded context, streamed decode chunks. */
    size_t context_tokens = 128 + max_new_tokens;
    coli_runtime_request_t request = {
        .model_path = argv[1],
        .tokenizer_path = argv[2],
        .generation = {
            .prompt = (const uint8_t *)prompt,
            .prompt_bytes = strlen(prompt),
            .context_tokens = context_tokens,
            .max_prompt_tokens = 128,
            .max_new_tokens = max_new_tokens,
            .workspace_bytes = 8u * 1024u * 1024u,
            .decoded_chunk_bytes = 256,
            .log_chunk = log_chunk,
        },
    };
    coli_runtime_result_t result;
    coli_status_t status = coli_runtime_generate(&request, &result);
    fputc('\n', stdout);
    fprintf(stderr,
            "arch=%" PRIu32 " status=%d stage=%d prompt_tokens=%zu "
            "generated=%zu workspace=%zu bytes\n",
            result.architecture, status, result.generation.stage,
            result.generation.prompt_tokens, result.generation.generated_tokens,
            result.generation.workspace_bytes);
    return status == COLI_OK ? 0 : 1;
}
