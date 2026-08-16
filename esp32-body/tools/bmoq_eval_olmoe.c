#include <errno.h>
#include <math.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "coli_kv_cache.h"
#include "coli_model.h"
#include "coli_olmoe.h"

#define MAX_LINE_BYTES (1024u * 1024u)
#define MAX_TOKENS 8192u
#define DEFAULT_WORKSPACE_BYTES (8u * 1024u * 1024u)
#define DEFAULT_KV_PAGE_BYTES (16u * 1024u * 1024u)

/* Emitting the full vocabulary for every target token costs roughly 27 GB of
 * JSON at benchmark scale, so full distributions are sampled on a stride while
 * every record still carries the target logit and a sparse top-k. Both runners
 * must use the same stride for the paired records to stay aligned. */
#define DEFAULT_LOGIT_TOP_K 32u

typedef struct {
    const char *model_path;
    const char *tokenizer_path;
    const char *input_path;
    const char *output_path;
    const char *kv_cache_path;
    const char *corpus_sha256;
    const char *tool_commit;
    const char *variant_name;
    size_t workspace_bytes;
    size_t kv_page_bytes;
    uint32_t logit_top_k;
    uint32_t full_logit_stride;
    bool dump_logits;
    bool dump_routing;
} args_t;

typedef struct {
    uint32_t token_id;
    float logit;
} logit_slot_t;

typedef struct {
    char sample_id[128];
    char category[128];
    uint32_t tokens[MAX_TOKENS];
    size_t token_count;
} sample_t;

typedef struct {
    uint32_t layer;
    uint32_t experts[COLI_MOE_MAX_TOP_K];
    float weights[COLI_MOE_MAX_TOP_K];
    size_t count;
} route_layer_t;

typedef struct {
    route_layer_t layers[COLI_OLMOE_EXPECTED_LAYERS];
    size_t count;
} route_capture_t;

static void usage(const char *argv0)
{
    fprintf(stderr,
            "usage: %s --model model.bmoq --tokenizer tokenizer.ctok "
            "--input benchmark.jsonl --output bmoq-results.jsonl "
            "--teacher-force --dump-logits --dump-routing\n",
            argv0);
}

static bool parse_size(const char *text, size_t *out)
{
    char *end = NULL;
    errno = 0;
    unsigned long long value = strtoull(text, &end, 10);
    if (errno || !end || *end) return false;
    *out = (size_t)value;
    return true;
}

static bool parse_args(int argc, char **argv, args_t *args)
{
    memset(args, 0, sizeof(*args));
    args->workspace_bytes = DEFAULT_WORKSPACE_BYTES;
    args->kv_page_bytes = DEFAULT_KV_PAGE_BYTES;
    args->logit_top_k = DEFAULT_LOGIT_TOP_K;
    args->variant_name = "bmoq-host";
    for (int i = 1; i < argc; ++i) {
        if (strcmp(argv[i], "--model") == 0 && i + 1 < argc)
            args->model_path = argv[++i];
        else if (strcmp(argv[i], "--tokenizer") == 0 && i + 1 < argc)
            args->tokenizer_path = argv[++i];
        else if (strcmp(argv[i], "--input") == 0 && i + 1 < argc)
            args->input_path = argv[++i];
        else if (strcmp(argv[i], "--output") == 0 && i + 1 < argc)
            args->output_path = argv[++i];
        else if (strcmp(argv[i], "--kv-cache") == 0 && i + 1 < argc)
            args->kv_cache_path = argv[++i];
        else if (strcmp(argv[i], "--corpus-sha256") == 0 && i + 1 < argc)
            args->corpus_sha256 = argv[++i];
        else if (strcmp(argv[i], "--tool-commit") == 0 && i + 1 < argc)
            args->tool_commit = argv[++i];
        else if (strcmp(argv[i], "--variant-name") == 0 && i + 1 < argc)
            args->variant_name = argv[++i];
        else if (strcmp(argv[i], "--workspace-bytes") == 0 && i + 1 < argc) {
            if (!parse_size(argv[++i], &args->workspace_bytes)) return false;
        } else if (strcmp(argv[i], "--kv-page-bytes") == 0 && i + 1 < argc) {
            if (!parse_size(argv[++i], &args->kv_page_bytes)) return false;
        } else if (strcmp(argv[i], "--logit-top-k") == 0 && i + 1 < argc) {
            size_t value = 0;
            if (!parse_size(argv[++i], &value) || value > UINT32_MAX) return false;
            args->logit_top_k = (uint32_t)value;
        } else if (strcmp(argv[i], "--full-logit-stride") == 0 && i + 1 < argc) {
            size_t value = 0;
            if (!parse_size(argv[++i], &value) || value > UINT32_MAX) return false;
            args->full_logit_stride = (uint32_t)value;
        } else if (strcmp(argv[i], "--teacher-force") == 0) {
        } else if (strcmp(argv[i], "--dump-logits") == 0)
            args->dump_logits = true;
        else if (strcmp(argv[i], "--dump-routing") == 0)
            args->dump_routing = true;
        else
            return false;
    }
    return args->model_path && args->tokenizer_path && args->input_path &&
           args->output_path;
}

static bool json_string(const char *line, const char *key, char *out,
                        size_t out_bytes)
{
    char pattern[64];
    snprintf(pattern, sizeof(pattern), "\"%s\"", key);
    const char *cursor = strstr(line, pattern);
    if (!cursor) return false;
    cursor = strchr(cursor + strlen(pattern), ':');
    if (!cursor) return false;
    cursor = strchr(cursor, '"');
    if (!cursor) return false;
    ++cursor;
    size_t count = 0;
    while (*cursor && *cursor != '"') {
        if (count + 1 >= out_bytes) return false;
        if (*cursor == '\\') {
            ++cursor;
            switch (*cursor) {
            case '"':
            case '\\':
            case '/':
                out[count++] = *cursor++;
                break;
            case 'b':
                out[count++] = '\b';
                ++cursor;
                break;
            case 'f':
                out[count++] = '\f';
                ++cursor;
                break;
            case 'n':
                out[count++] = '\n';
                ++cursor;
                break;
            case 'r':
                out[count++] = '\r';
                ++cursor;
                break;
            case 't':
                out[count++] = '\t';
                ++cursor;
                break;
            default:
                return false;
            }
        } else {
            out[count++] = *cursor++;
        }
    }
    if (*cursor != '"') return false;
    out[count] = '\0';
    return count > 0;
}

static bool json_tokens(const char *line, uint32_t *tokens, size_t *out_count)
{
    const char *cursor = strstr(line, "\"tokens\"");
    if (!cursor) return false;
    cursor = strchr(cursor, '[');
    if (!cursor) return false;
    ++cursor;
    size_t count = 0;
    while (*cursor && *cursor != ']') {
        while (*cursor == ' ' || *cursor == ',') ++cursor;
        char *end = NULL;
        errno = 0;
        unsigned long value = strtoul(cursor, &end, 10);
        if (errno || end == cursor || value > UINT32_MAX || count >= MAX_TOKENS)
            return false;
        tokens[count++] = (uint32_t)value;
        cursor = end;
    }
    *out_count = count;
    return count > 1;
}

static bool parse_sample(const char *line, sample_t *sample)
{
    memset(sample, 0, sizeof(*sample));
    if (!json_string(line, "sample_id", sample->sample_id,
                     sizeof(sample->sample_id)))
        snprintf(sample->sample_id, sizeof(sample->sample_id), "sample");
    if (!json_string(line, "category", sample->category,
                     sizeof(sample->category)))
        snprintf(sample->category, sizeof(sample->category), "uncategorized");
    return json_tokens(line, sample->tokens, &sample->token_count);
}

static float nll_for_token(const float *logits, size_t count, uint32_t token)
{
    float peak = logits[0];
    for (size_t i = 1; i < count; ++i)
        if (logits[i] > peak) peak = logits[i];
    double total = 0.0;
    for (size_t i = 0; i < count; ++i)
        total += exp((double)logits[i] - peak);
    return (float)(log(total) + peak - logits[token]);
}

static bool validate_sample_tokens(const sample_t *sample, uint32_t vocab_size)
{
    for (size_t i = 0; i < sample->token_count; ++i)
        if (sample->tokens[i] >= vocab_size) return false;
    return true;
}

static void write_json_string(FILE *output, const char *text)
{
    fputc('"', output);
    for (const unsigned char *cursor = (const unsigned char *)text; *cursor;
         ++cursor) {
        switch (*cursor) {
        case '"':
            fputs("\\\"", output);
            break;
        case '\\':
            fputs("\\\\", output);
            break;
        case '\b':
            fputs("\\b", output);
            break;
        case '\f':
            fputs("\\f", output);
            break;
        case '\n':
            fputs("\\n", output);
            break;
        case '\r':
            fputs("\\r", output);
            break;
        case '\t':
            fputs("\\t", output);
            break;
        default:
            if (*cursor < 0x20)
                fprintf(output, "\\u%04x", *cursor);
            else
                fputc(*cursor, output);
        }
    }
    fputc('"', output);
}

static coli_status_t observe_layer(void *context, uint32_t layer,
                                   const coli_moe_stats_t *moe_stats)
{
    route_capture_t *capture = context;
    if (capture->count >= COLI_OLMOE_EXPECTED_LAYERS)
        return COLI_ERR_RANGE;
    route_layer_t *row = &capture->layers[capture->count++];
    row->layer = layer;
    row->count = moe_stats->selected_count;
    for (size_t i = 0; i < row->count && i < COLI_MOE_MAX_TOP_K; ++i) {
        row->experts[i] = moe_stats->selected_experts[i];
        row->weights[i] = moe_stats->routing_weights[i];
    }
    return COLI_OK;
}

/* Descending by logit, ties broken by the lower token id. Mirrors the
 * reference runner's sorted(range(V), key=(-logit, index)) exactly so the two
 * files order identical distributions identically. */
static bool logit_precedes(const logit_slot_t *candidate, const logit_slot_t *held)
{
    if (candidate->logit != held->logit) return candidate->logit > held->logit;
    return candidate->token_id < held->token_id;
}

static void select_top_logits(const float *logits, size_t count, uint32_t top_k,
                              logit_slot_t *out, size_t *out_count)
{
    size_t filled = 0;
    for (size_t index = 0; index < count; ++index) {
        logit_slot_t candidate = {.token_id = (uint32_t)index,
                                  .logit = logits[index]};
        if (filled == top_k && !logit_precedes(&candidate, &out[filled - 1u]))
            continue;
        size_t slot = filled < top_k ? filled : top_k - 1u;
        while (slot > 0 && logit_precedes(&candidate, &out[slot - 1u])) {
            out[slot] = out[slot - 1u];
            --slot;
        }
        out[slot] = candidate;
        if (filled < top_k) ++filled;
    }
    *out_count = filled;
}

static void write_logit_top_k(FILE *output, const logit_slot_t *slots,
                              size_t count)
{
    fputc('[', output);
    for (size_t i = 0; i < count; ++i) {
        if (i) fputc(',', output);
        fprintf(output, "{\"token_id\":%u,\"logit\":%.9g}", slots[i].token_id,
                slots[i].logit);
    }
    fputc(']', output);
}

static void write_float_array(FILE *output, const float *values, size_t count)
{
    fputc('[', output);
    for (size_t i = 0; i < count; ++i) {
        if (i) fputc(',', output);
        fprintf(output, "%.9g", values[i]);
    }
    fputc(']', output);
}

static void write_router(FILE *output, const route_capture_t *capture)
{
    fputc('[', output);
    for (size_t i = 0; i < capture->count; ++i) {
        if (i) fputc(',', output);
        const route_layer_t *layer = &capture->layers[i];
        fprintf(output, "{\"layer\":%u,\"top_experts\":[", layer->layer);
        for (size_t slot = 0; slot < layer->count; ++slot) {
            if (slot) fputc(',', output);
            fprintf(output, "%u", layer->experts[slot]);
        }
        fputs("],\"weights\":[", output);
        for (size_t slot = 0; slot < layer->count; ++slot) {
            if (slot) fputc(',', output);
            fprintf(output, "%.9g", layer->weights[slot]);
        }
        fputs("]}", output);
    }
    fputc(']', output);
}

static coli_status_t open_kv_cache(const coli_model_t *model,
                                   const args_t *args,
                                   const char *fallback_path,
                                   coli_kv_cache_t **out_cache)
{
    coli_kv_cache_layout_t layout;
    uint32_t head_dim =
        model->config.hidden_size / model->config.num_attention_heads;
    coli_status_t status = coli_kv_cache_layout(
        model->config.num_hidden_layers, model->config.num_attention_heads,
        head_dim, model->config.max_position_embeddings, sizeof(float),
        &layout);
    if (status != COLI_OK) return status;
    return coli_kv_cache_open_file(&layout,
                                   args->kv_cache_path ? args->kv_cache_path
                                                       : fallback_path,
                                   args->kv_page_bytes, out_cache);
}

static int run(const args_t *args)
{
    coli_store_t *store = NULL;
    coli_model_t model;
    memset(&model, 0, sizeof(model));
    coli_status_t status = coli_store_open_file(args->model_path, &store);
    if (status != COLI_OK) return status;
    status = coli_model_open(store, &model);
    if (status != COLI_OK) return status;
    if (model.config.arch != BMOQ_MODEL_ARCH_OLMOE) return COLI_ERR_UNSUPPORTED;

    void *workspace = malloc(args->workspace_bytes);
    float *logits = malloc((size_t)model.config.vocab_size * sizeof(*logits));
    logit_slot_t *top_logits =
        args->logit_top_k ? malloc((size_t)args->logit_top_k * sizeof(*top_logits))
                          : NULL;
    if (!workspace || !logits || (args->logit_top_k && !top_logits))
        return COLI_ERR_NO_MEMORY;

    FILE *input = fopen(args->input_path, "r");
    FILE *output = fopen(args->output_path, "w");
    if (!input || !output) return COLI_ERR_IO;
    char *line = malloc(MAX_LINE_BYTES);
    if (!line) return COLI_ERR_NO_MEMORY;

    fputs("{\"schema_version\":1,\"record_type\":\"run_meta\",\"checkpoint_id\":",
          output);
    write_json_string(output, args->model_path);
    fputs(",\"tokenizer_id\":", output);
    write_json_string(output, args->tokenizer_path);
    fputs(",\"corpus_sha256\":", output);
    write_json_string(output, args->corpus_sha256 ? args->corpus_sha256 : "");
    fputs(",\"tool_commit\":", output);
    write_json_string(output, args->tool_commit ? args->tool_commit : "");
    fprintf(output,
            ",\"logit_top_k\":%u,\"full_logit_stride\":%u,"
            "\"workspace_bytes\":%zu,\"kv_page_bytes\":%zu,"
            "\"variant\":{\"name\":",
            args->logit_top_k, args->full_logit_stride, args->workspace_bytes,
            args->kv_page_bytes);
    write_json_string(output, args->variant_name);
    fputs(",\"model\":", output);
    write_json_string(output, args->model_path);
    fprintf(output,
            ",\"tensor_precision\":\"BMOQ\",\"quant_group_size\":%u,"
            "\"runtime\":\"coli_mcu\"}}\n",
            model.config.quant_group);

    while (fgets(line, MAX_LINE_BYTES, input)) {
        sample_t sample;
        if (!parse_sample(line, &sample)) return COLI_ERR_FORMAT;
        if (!validate_sample_tokens(&sample, model.config.vocab_size))
            return COLI_ERR_RANGE;
        char tmp_template[] = "/tmp/bmoq-eval-kv-XXXXXX";
        int fd = mkstemp(tmp_template);
        if (fd < 0) return COLI_ERR_IO;
        close(fd);
        coli_kv_cache_t *kv_cache = NULL;
        status = open_kv_cache(&model, args, tmp_template, &kv_cache);
        if (status != COLI_OK) return status;
        for (size_t position = 0; position + 1 < sample.token_count; ++position) {
            route_capture_t routes;
            memset(&routes, 0, sizeof(routes));
            uint32_t predicted = UINT32_MAX;
            coli_olmoe_decode_stats_t stats;
            status = coli_olmoe_decode_eval_token(
                &model, sample.tokens[position], (uint32_t)position, kv_cache,
                workspace, args->workspace_bytes, logits, model.config.vocab_size,
                &predicted, args->dump_routing ? observe_layer : NULL, &routes,
                &stats);
            if (status != COLI_OK) return status;
            uint32_t target = sample.tokens[position + 1u];
            /* Keyed on the record's own position, not a running counter, so
             * the same records carry full logits however the corpus is split
             * across parallel workers. */
            bool emit_dense =
                args->dump_logits ||
                (args->full_logit_stride &&
                 (position + 1u) % args->full_logit_stride == 0);
            fputs("{\"schema_version\":1,\"record_type\":\"token_eval\","
                  "\"sample_id\":",
                  output);
            write_json_string(output, sample.sample_id);
            fprintf(output, ",\"position\":%zu,\"token_id\":%u,"
                            "\"nll\":%.9g,\"target_logit\":%.9g,\"logits\":",
                    position + 1u, target,
                    nll_for_token(logits, model.config.vocab_size, target),
                    logits[target]);
            if (emit_dense)
                write_float_array(output, logits, model.config.vocab_size);
            else
                fputs("[]", output);
            fputs(",\"logit_top_k\":", output);
            if (args->logit_top_k) {
                size_t selected = 0;
                select_top_logits(logits, model.config.vocab_size,
                                  args->logit_top_k, top_logits, &selected);
                write_logit_top_k(output, top_logits, selected);
            } else {
                fputs("[]", output);
            }
            fputs(",\"category\":", output);
            write_json_string(output, sample.category);
            fprintf(output, ",\"sequence_length\":%zu,\"variant\":{\"name\":",
                    sample.token_count);
            write_json_string(output, args->variant_name);
            fputs(",\"model\":", output);
            write_json_string(output, args->model_path);
            fputs(",\"tokenizer\":", output);
            write_json_string(output, args->tokenizer_path);
            fprintf(output,
                    ",\"tensor_precision\":\"BMOQ\","
                    "\"quant_group_size\":%u,\"runtime\":\"coli_mcu\"},"
                    "\"router\":",
                    model.config.quant_group);
            if (args->dump_routing)
                write_router(output, &routes);
            else
                fputs("[]", output);
            fputs("}\n", output);
        }
        coli_kv_cache_close(kv_cache);
        if (!args->kv_cache_path) unlink(tmp_template);
    }

    free(line);
    fclose(output);
    fclose(input);
    free(top_logits);
    free(logits);
    free(workspace);
    coli_model_close(&model);
    coli_store_close(store);
    return COLI_OK;
}

#ifndef BMOQ_EVAL_OLMOE_NO_MAIN
int main(int argc, char **argv)
{
    args_t args;
    if (!parse_args(argc, argv, &args)) {
        usage(argv[0]);
        return 2;
    }
    int status = run(&args);
    if (status != COLI_OK) {
        fprintf(stderr, "bmoq-eval-olmoe failed: %d\n", status);
        return 1;
    }
    return 0;
}
#endif
