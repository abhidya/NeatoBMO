#include "coli_runtime.h"

#include <string.h>

#include "coli_model.h"
#include "coli_store.h"

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
        result->architecture != BMOQ_MODEL_ARCH_GLM52) {
        status = COLI_ERR_UNSUPPORTED;
        goto cleanup;
    }

    status = coli_store_open_file(request->tokenizer_path, &tokenizer_store);
    if (status != COLI_OK) goto cleanup;
    if (result->architecture == BMOQ_MODEL_ARCH_GLM52)
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
