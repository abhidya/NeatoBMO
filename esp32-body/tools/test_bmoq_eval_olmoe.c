#define BMOQ_EVAL_OLMOE_NO_MAIN
#include "bmoq_eval_olmoe.c"

#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

int main(void)
{
    sample_t sample = {0};
    sample.token_count = 3;
    sample.tokens[0] = 0;
    sample.tokens[1] = 7;
    sample.tokens[2] = 8;
    assert(validate_sample_tokens(&sample, 9));
    assert(!validate_sample_tokens(&sample, 8));

    const char *line =
        "{\"sample_id\":\"quote\\\"backslash\\\\\","
        "\"category\":\"line\\nfeed\",\"tokens\":[0,1]}";
    sample_t parsed = {0};
    assert(parse_sample(line, &parsed));
    assert(strcmp(parsed.sample_id, "quote\"backslash\\") == 0);
    assert(strcmp(parsed.category, "line\nfeed") == 0);
    assert(parsed.token_count == 2);

    char *buffer = NULL;
    size_t bytes = 0;
    FILE *stream = open_memstream(&buffer, &bytes);
    assert(stream);
    write_json_string(stream, "quote\" slash\\ newline\n tab\t");
    assert(fclose(stream) == 0);
    assert(strcmp(buffer, "\"quote\\\" slash\\\\ newline\\n tab\\t\"") == 0);
    free(buffer);

    puts("BMOQ OLMoE eval helpers: PASS");
    return 0;
}
