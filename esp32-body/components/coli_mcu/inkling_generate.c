#include "coli_inkling.h"

#include <string.h>

coli_status_t coli_inkling_generate_greedy_stream(
    const coli_inkling_config_t *config, uint32_t seed_token,
    size_t seed_position, size_t max_new_tokens, uint32_t *output_token_ids,
    size_t output_token_capacity, size_t *out_output_token_count,
    coli_inkling_next_token_fn next_token, void *next_token_context,
    coli_inkling_token_fn on_token, void *token_context)
{
    if (!config || !output_token_ids || !out_output_token_count ||
        !next_token || output_token_capacity < max_new_tokens ||
        seed_position + max_new_tokens > config->max_context_tokens)
        return COLI_ERR_ARGUMENT;
    *out_output_token_count = 0;
    uint32_t token = seed_token;
    for (size_t generated = 0; generated < max_new_tokens; ++generated) {
        coli_status_t status =
            next_token(next_token_context, token, seed_position + generated,
                       &token);
        if (status != COLI_OK) return status;
        if (coli_inkling_is_stop_token(config, token)) return COLI_OK;
        output_token_ids[generated] = token;
        *out_output_token_count = generated + 1u;
        if (on_token) {
            status = on_token(token_context, token, generated);
            if (status != COLI_OK) return status;
        }
    }
    return COLI_OK;
}
