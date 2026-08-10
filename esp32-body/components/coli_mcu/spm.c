#include "coli_spm.h"

#include <float.h>
#include <math.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#define COLI_SPM_TYPE_NORMAL 1u
#define COLI_SPM_TYPE_CONTROL 3u
#define COLI_SPM_TYPE_USER_DEFINED 4u
#define COLI_SPM_TYPE_BYTE 6u
#define COLI_SPM_FLAG_SPECIAL 0x0001u
#define COLI_SPM_FLAG_BYTE 0x0002u

typedef struct {
    uint64_t data_offset;
    uint16_t byte_length;
    uint16_t token_type;
    float score;
    uint16_t flags;
    uint16_t byte_value;
} coli_spm_entry_t;

typedef struct {
    uint32_t first_edge;
    uint16_t edge_count;
    uint16_t reserved;
    uint32_t token_id;
    float score;
} coli_spm_node_t;

typedef struct {
    uint8_t byte_value;
    uint8_t reserved[3];
    uint32_t child_node;
} coli_spm_edge_t;

typedef struct {
    float score;
    uint32_t previous;
    uint32_t token_id;
    bool reachable;
} coli_spm_dp_t;

static uint16_t get_u16(const uint8_t *p)
{
    return (uint16_t)p[0] | ((uint16_t)p[1] << 8);
}

static uint32_t get_u32(const uint8_t *p)
{
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) |
           ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

static uint64_t get_u64(const uint8_t *p)
{
    return (uint64_t)get_u32(p) | ((uint64_t)get_u32(p + 4) << 32);
}

static float get_f32(const uint8_t *p)
{
    uint32_t raw = get_u32(p);
    float value;
    memcpy(&value, &raw, sizeof(value));
    return value;
}

static coli_status_t read_entry(const coli_spm_t *spm, uint32_t token_id,
                                coli_spm_entry_t *entry)
{
    if (spm == NULL || entry == NULL || token_id >= spm->vocab_size) {
        return COLI_ERR_ARGUMENT;
    }
    uint8_t raw[COLI_SPM_ENTRY_BYTES];
    coli_status_t status = coli_store_read_at(
        spm->store, spm->entry_offset + (uint64_t)token_id * COLI_SPM_ENTRY_BYTES,
        raw, sizeof(raw));
    if (status != COLI_OK) {
        return status;
    }
    entry->data_offset = get_u64(raw);
    entry->byte_length = get_u16(raw + 8);
    entry->token_type = get_u16(raw + 10);
    entry->score = get_f32(raw + 12);
    entry->flags = get_u16(raw + 16);
    entry->byte_value = get_u16(raw + 18);
    return COLI_OK;
}

static coli_status_t read_node(const coli_spm_t *spm, uint32_t node_id,
                               coli_spm_node_t *node)
{
    if (spm == NULL || node == NULL || node_id >= spm->node_count) {
        return COLI_ERR_ARGUMENT;
    }
    uint8_t raw[COLI_SPM_NODE_BYTES];
    coli_status_t status = coli_store_read_at(
        spm->store, spm->node_offset + (uint64_t)node_id * COLI_SPM_NODE_BYTES,
        raw, sizeof(raw));
    if (status != COLI_OK) {
        return status;
    }
    node->first_edge = get_u32(raw);
    node->edge_count = get_u16(raw + 4);
    node->reserved = get_u16(raw + 6);
    node->token_id = get_u32(raw + 8);
    node->score = get_f32(raw + 12);
    return COLI_OK;
}

static coli_status_t read_edge(const coli_spm_t *spm, uint32_t edge_id,
                               coli_spm_edge_t *edge)
{
    if (spm == NULL || edge == NULL || edge_id >= spm->edge_count) {
        return COLI_ERR_ARGUMENT;
    }
    uint8_t raw[COLI_SPM_EDGE_BYTES];
    coli_status_t status = coli_store_read_at(
        spm->store, spm->edge_offset + (uint64_t)edge_id * COLI_SPM_EDGE_BYTES,
        raw, sizeof(raw));
    if (status != COLI_OK) {
        return status;
    }
    edge->byte_value = raw[0];
    edge->child_node = get_u32(raw + 4);
    return COLI_OK;
}

static coli_status_t find_child(const coli_spm_t *spm, const coli_spm_node_t *node,
                                uint8_t byte_value, uint32_t *child_node,
                                bool *found)
{
    uint32_t low = 0;
    uint32_t high = node->edge_count;
    *found = false;
    while (low < high) {
        uint32_t mid = low + (high - low) / 2u;
        coli_spm_edge_t edge;
        coli_status_t status = read_edge(spm, node->first_edge + mid, &edge);
        if (status != COLI_OK) {
            return status;
        }
        if (edge.byte_value == byte_value) {
            *child_node = edge.child_node;
            *found = true;
            return COLI_OK;
        }
        if (edge.byte_value < byte_value) {
            low = mid + 1u;
        } else {
            high = mid;
        }
    }
    return COLI_OK;
}

static coli_status_t read_piece_bytes(const coli_spm_t *spm,
                                      const coli_spm_entry_t *entry,
                                      uint8_t *destination,
                                      size_t destination_capacity)
{
    if (entry->byte_length > destination_capacity) {
        return COLI_ERR_RANGE;
    }
    if (entry->byte_length == 0) {
        return COLI_OK;
    }
    if (entry->data_offset > coli_store_size(spm->store) ||
        entry->byte_length > coli_store_size(spm->store) - entry->data_offset) {
        return COLI_ERR_FORMAT;
    }
    return coli_store_read_at(spm->store, entry->data_offset, destination,
                              entry->byte_length);
}

static coli_status_t validate_entry(const coli_spm_t *spm, uint32_t token_id,
                                    coli_spm_entry_t *entry)
{
    coli_status_t status = read_entry(spm, token_id, entry);
    if (status != COLI_OK) {
        return status;
    }
    if (entry->byte_length > spm->max_piece_bytes ||
        entry->data_offset > coli_store_size(spm->store) ||
        entry->byte_length > coli_store_size(spm->store) - entry->data_offset) {
        return COLI_ERR_FORMAT;
    }
    if ((entry->flags & COLI_SPM_FLAG_BYTE) != 0 &&
        (entry->byte_value > 255u || entry->token_type != COLI_SPM_TYPE_BYTE)) {
        return COLI_ERR_FORMAT;
    }
    (void)COLI_SPM_TYPE_NORMAL;
    (void)COLI_SPM_TYPE_CONTROL;
    (void)COLI_SPM_TYPE_USER_DEFINED;
    return COLI_OK;
}

static coli_status_t append_normalized_byte(uint8_t byte_value, uint8_t *out,
                                            size_t capacity, size_t *count)
{
    if (*count >= capacity) {
        return COLI_ERR_RANGE;
    }
    out[*count] = byte_value;
    ++*count;
    return COLI_OK;
}

static coli_status_t normalize_text(const coli_spm_t *spm, const uint8_t *text,
                                    size_t text_bytes, uint8_t *out,
                                    size_t capacity, size_t *out_bytes)
{
    size_t count = 0;
    if (spm->add_space_prefix) {
        const uint8_t marker[] = {0xE2u, 0x96u, 0x81u};
        for (size_t i = 0; i < sizeof(marker); ++i) {
            coli_status_t status =
                append_normalized_byte(marker[i], out, capacity, &count);
            if (status != COLI_OK) {
                return status;
            }
        }
    }
    for (size_t i = 0; i < text_bytes; ++i) {
        if (text[i] == ' ') {
            const uint8_t marker[] = {0xE2u, 0x96u, 0x81u};
            for (size_t j = 0; j < sizeof(marker); ++j) {
                coli_status_t status =
                    append_normalized_byte(marker[j], out, capacity, &count);
                if (status != COLI_OK) {
                    return status;
                }
            }
        } else {
            coli_status_t status =
                append_normalized_byte(text[i], out, capacity, &count);
            if (status != COLI_OK) {
                return status;
            }
        }
    }
    *out_bytes = count;
    return COLI_OK;
}

static coli_status_t push_token(uint32_t token_id, uint32_t *token_ids,
                                size_t token_capacity, size_t *token_count)
{
    if (*token_count >= token_capacity) {
        return COLI_ERR_RANGE;
    }
    token_ids[*token_count] = token_id;
    ++*token_count;
    return COLI_OK;
}

static bool token_is_special(const coli_spm_entry_t *entry)
{
    return (entry->flags & COLI_SPM_FLAG_SPECIAL) != 0;
}

static coli_status_t match_special(const coli_spm_t *spm, uint32_t token_id,
                                   const uint8_t *text, size_t text_bytes,
                                   size_t position, bool *matched,
                                   size_t *matched_bytes)
{
    *matched = false;
    *matched_bytes = 0;
    if (token_id >= spm->vocab_size) {
        return COLI_OK;
    }
    coli_spm_entry_t entry;
    coli_status_t status = read_entry(spm, token_id, &entry);
    if (status != COLI_OK) {
        return status;
    }
    if (!token_is_special(&entry) || entry.byte_length == 0 ||
        position + entry.byte_length > text_bytes ||
        entry.byte_length > COLI_SPM_MAX_PIECE_BYTES) {
        return COLI_OK;
    }
    uint8_t bytes[COLI_SPM_MAX_PIECE_BYTES];
    status = read_piece_bytes(spm, &entry, bytes, sizeof(bytes));
    if (status != COLI_OK) {
        return status;
    }
    if (memcmp(text + position, bytes, entry.byte_length) == 0) {
        *matched = true;
        *matched_bytes = entry.byte_length;
    }
    return COLI_OK;
}

static coli_status_t encode_plain_span(const coli_spm_t *spm,
                                       const uint8_t *text,
                                       size_t text_bytes,
                                       uint32_t *token_ids,
                                       size_t token_capacity,
                                       size_t *token_count)
{
    if (text_bytes > COLI_SPM_MAX_NORMALIZED_BYTES / 3u) {
        return COLI_ERR_RANGE;
    }

    uint8_t *normalized = (uint8_t *)malloc(COLI_SPM_MAX_NORMALIZED_BYTES);
    if (normalized == NULL) {
        return COLI_ERR_NO_MEMORY;
    }
    size_t normalized_bytes = 0;
    coli_status_t status = normalize_text(spm, text, text_bytes, normalized,
                                          COLI_SPM_MAX_NORMALIZED_BYTES,
                                          &normalized_bytes);
    if (status != COLI_OK) {
        free(normalized);
        return status;
    }

    coli_spm_dp_t *dp =
        (coli_spm_dp_t *)calloc(normalized_bytes + 1u, sizeof(*dp));
    if (dp == NULL) {
        free(normalized);
        return COLI_ERR_NO_MEMORY;
    }
    dp[0].score = 0.0f;
    dp[0].reachable = true;

    for (size_t start = 0; start < normalized_bytes; ++start) {
        if (!dp[start].reachable) {
            continue;
        }
        coli_spm_node_t node;
        status = read_node(spm, 0, &node);
        if (status != COLI_OK) {
            free(dp);
            free(normalized);
            return status;
        }
        for (size_t end = start; end < normalized_bytes; ++end) {
            uint32_t child = 0;
            bool found = false;
            status = find_child(spm, &node, normalized[end], &child, &found);
            if (status != COLI_OK) {
                free(dp);
                free(normalized);
                return status;
            }
            if (!found) {
                break;
            }
            status = read_node(spm, child, &node);
            if (status != COLI_OK) {
                free(dp);
                free(normalized);
                return status;
            }
            if (node.token_id != UINT32_MAX) {
                float candidate = dp[start].score + node.score;
                size_t next = end + 1u;
                if (!dp[next].reachable || candidate > dp[next].score ||
                    (candidate == dp[next].score &&
                     node.token_id < dp[next].token_id)) {
                    dp[next].score = candidate;
                    dp[next].previous = (uint32_t)start;
                    dp[next].token_id = node.token_id;
                    dp[next].reachable = true;
                }
            }
        }
        if (spm->has_byte_token[normalized[start]]) {
            size_t next = start + 1u;
            float candidate = dp[start].score - 100.0f;
            uint32_t token_id = spm->byte_token_ids[normalized[start]];
            if (!dp[next].reachable || candidate > dp[next].score) {
                dp[next].score = candidate;
                dp[next].previous = (uint32_t)start;
                dp[next].token_id = token_id;
                dp[next].reachable = true;
            }
        } else if (spm->unk_token_id < spm->vocab_size) {
            size_t next = start + 1u;
            float candidate = dp[start].score - 1000.0f;
            if (!dp[next].reachable || candidate > dp[next].score) {
                dp[next].score = candidate;
                dp[next].previous = (uint32_t)start;
                dp[next].token_id = spm->unk_token_id;
                dp[next].reachable = true;
            }
        }
    }

    if (!dp[normalized_bytes].reachable) {
        free(dp);
        free(normalized);
        return COLI_ERR_FORMAT;
    }

    uint32_t *reverse_ids =
        (uint32_t *)malloc((normalized_bytes + 1u) * sizeof(*reverse_ids));
    if (reverse_ids == NULL) {
        free(dp);
        free(normalized);
        return COLI_ERR_NO_MEMORY;
    }
    size_t reverse_count = 0;
    size_t cursor = normalized_bytes;
    while (cursor > 0) {
        reverse_ids[reverse_count++] = dp[cursor].token_id;
        cursor = dp[cursor].previous;
    }
    for (size_t i = 0; i < reverse_count; ++i) {
        status = push_token(reverse_ids[reverse_count - 1u - i], token_ids,
                            token_capacity, token_count);
        if (status != COLI_OK) {
            free(reverse_ids);
            free(dp);
            free(normalized);
            return status;
        }
    }

    free(reverse_ids);
    free(dp);
    free(normalized);
    return COLI_OK;
}

coli_status_t coli_spm_open(coli_store_t *store, coli_spm_t *spm)
{
    if (store == NULL || spm == NULL) {
        return COLI_ERR_ARGUMENT;
    }
    memset(spm, 0, sizeof(*spm));
    uint8_t header[COLI_SPM_HEADER_BYTES];
    coli_status_t status = coli_store_read_at(store, 0, header, sizeof(header));
    if (status != COLI_OK) {
        return status;
    }
    if (memcmp(header, COLI_SPM_MAGIC, 4) != 0 ||
        get_u16(header + 4) != COLI_SPM_VERSION ||
        get_u16(header + 6) != COLI_SPM_HEADER_BYTES ||
        get_u32(header + 20) != COLI_SPM_ENTRY_BYTES ||
        get_u32(header + 24) != COLI_SPM_NODE_BYTES ||
        get_u32(header + 28) != COLI_SPM_EDGE_BYTES) {
        return COLI_ERR_FORMAT;
    }

    spm->store = store;
    spm->vocab_size = get_u32(header + 8);
    spm->node_count = get_u32(header + 12);
    spm->edge_count = get_u32(header + 16);
    spm->entry_offset = get_u64(header + 32);
    spm->node_offset = get_u64(header + 40);
    spm->edge_offset = get_u64(header + 48);
    spm->piece_data_offset = get_u64(header + 56);
    spm->bos_token_id = get_u32(header + 64);
    spm->eos_token_id = get_u32(header + 68);
    spm->pad_token_id = get_u32(header + 72);
    spm->unk_token_id = get_u32(header + 76);
    uint32_t byte_token_count = get_u32(header + 80);
    spm->max_piece_bytes = get_u16(header + 84);
    spm->add_bos_token = header[86] != 0;
    spm->add_eos_token = header[87] != 0;
    spm->add_space_prefix = header[88] != 0;

    if (spm->vocab_size == 0 || spm->node_count == 0 ||
        spm->max_piece_bytes == 0 ||
        spm->max_piece_bytes > COLI_SPM_MAX_PIECE_BYTES ||
        byte_token_count != 256u ||
        spm->entry_offset < COLI_SPM_HEADER_BYTES ||
        spm->entry_offset > coli_store_size(store) ||
        spm->node_offset > coli_store_size(store) ||
        spm->edge_offset > coli_store_size(store) ||
        spm->piece_data_offset > coli_store_size(store)) {
        return COLI_ERR_FORMAT;
    }
    if ((uint64_t)spm->vocab_size >
            (UINT64_MAX - spm->entry_offset) / COLI_SPM_ENTRY_BYTES ||
        (uint64_t)spm->node_count >
            (UINT64_MAX - spm->node_offset) / COLI_SPM_NODE_BYTES ||
        (uint64_t)spm->edge_count >
            (UINT64_MAX - spm->edge_offset) / COLI_SPM_EDGE_BYTES) {
        return COLI_ERR_FORMAT;
    }
    if ((uint64_t)spm->vocab_size * COLI_SPM_ENTRY_BYTES >
            coli_store_size(store) - spm->entry_offset ||
        (uint64_t)spm->node_count * COLI_SPM_NODE_BYTES >
            coli_store_size(store) - spm->node_offset ||
        (uint64_t)spm->edge_count * COLI_SPM_EDGE_BYTES >
            coli_store_size(store) - spm->edge_offset) {
        return COLI_ERR_FORMAT;
    }

    for (uint32_t token_id = 0; token_id < spm->vocab_size; ++token_id) {
        coli_spm_entry_t entry;
        status = validate_entry(spm, token_id, &entry);
        if (status != COLI_OK) {
            coli_spm_close(spm);
            return status;
        }
        if ((entry.flags & COLI_SPM_FLAG_BYTE) != 0) {
            if (spm->has_byte_token[entry.byte_value]) {
                coli_spm_close(spm);
                return COLI_ERR_FORMAT;
            }
            spm->has_byte_token[entry.byte_value] = true;
            spm->byte_token_ids[entry.byte_value] = token_id;
        }
    }
    for (uint32_t byte_value = 0; byte_value < 256u; ++byte_value) {
        if (!spm->has_byte_token[byte_value]) {
            coli_spm_close(spm);
            return COLI_ERR_FORMAT;
        }
    }

    return COLI_OK;
}

coli_status_t coli_spm_encode(const coli_spm_t *spm, const uint8_t *text,
                              size_t text_bytes, uint32_t *token_ids,
                              size_t token_capacity, size_t *out_token_count,
                              uint32_t flags)
{
    if (spm == NULL || (text == NULL && text_bytes != 0) ||
        token_ids == NULL || out_token_count == NULL) {
        return COLI_ERR_ARGUMENT;
    }

    size_t token_count = 0;
    if (((flags & COLI_SPM_ENCODE_ADD_BOS) != 0 || spm->add_bos_token) &&
        spm->bos_token_id < spm->vocab_size) {
        coli_status_t status =
            push_token(spm->bos_token_id, token_ids, token_capacity, &token_count);
        if (status != COLI_OK) {
            return status;
        }
    }

    size_t span_start = 0;
    size_t position = 0;
    while (position < text_bytes) {
        bool matched_special = false;
        size_t matched_bytes = 0;
        uint32_t matched_id = UINT32_MAX;
        if ((flags & COLI_SPM_ENCODE_ALLOW_SPECIAL) != 0) {
            const uint32_t special_ids[] = {spm->eos_token_id, spm->bos_token_id,
                                            spm->pad_token_id, spm->unk_token_id};
            for (size_t i = 0; i < sizeof(special_ids) / sizeof(special_ids[0]);
                 ++i) {
                coli_status_t status =
                    match_special(spm, special_ids[i], text, text_bytes, position,
                                  &matched_special, &matched_bytes);
                if (status != COLI_OK) {
                    return status;
                }
                if (matched_special) {
                    matched_id = special_ids[i];
                    break;
                }
            }
        }
        if (matched_special) {
            if (position > span_start) {
                coli_status_t status = encode_plain_span(
                    spm, text + span_start, position - span_start, token_ids,
                    token_capacity, &token_count);
                if (status != COLI_OK) {
                    return status;
                }
            }
            coli_status_t status =
                push_token(matched_id, token_ids, token_capacity, &token_count);
            if (status != COLI_OK) {
                return status;
            }
            position += matched_bytes;
            span_start = position;
        } else {
            ++position;
        }
    }
    if (text_bytes > span_start) {
        coli_status_t status = encode_plain_span(
            spm, text + span_start, text_bytes - span_start, token_ids,
            token_capacity, &token_count);
        if (status != COLI_OK) {
            return status;
        }
    }

    if (((flags & COLI_SPM_ENCODE_ADD_EOS) != 0 || spm->add_eos_token) &&
        spm->eos_token_id < spm->vocab_size) {
        coli_status_t status =
            push_token(spm->eos_token_id, token_ids, token_capacity, &token_count);
        if (status != COLI_OK) {
            return status;
        }
    }
    *out_token_count = token_count;
    return COLI_OK;
}

coli_status_t coli_spm_decode(const coli_spm_t *spm, const uint32_t *token_ids,
                              size_t token_count, uint8_t *text,
                              size_t text_capacity, size_t *out_text_bytes)
{
    if (spm == NULL || (token_ids == NULL && token_count != 0) ||
        text == NULL || out_text_bytes == NULL) {
        return COLI_ERR_ARGUMENT;
    }

    size_t written = 0;
    for (size_t i = 0; i < token_count; ++i) {
        coli_spm_entry_t entry;
        coli_status_t status = read_entry(spm, token_ids[i], &entry);
        if (status != COLI_OK) {
            return status;
        }
        if ((entry.flags & COLI_SPM_FLAG_BYTE) != 0) {
            if (written >= text_capacity) {
                return COLI_ERR_RANGE;
            }
            text[written++] = (uint8_t)entry.byte_value;
            continue;
        }
        if (entry.byte_length > COLI_SPM_MAX_PIECE_BYTES) {
            return COLI_ERR_FORMAT;
        }
        uint8_t piece[COLI_SPM_MAX_PIECE_BYTES];
        status = read_piece_bytes(spm, &entry, piece, sizeof(piece));
        if (status != COLI_OK) {
            return status;
        }
        for (uint16_t j = 0; j < entry.byte_length; ++j) {
            if (j + 2u < entry.byte_length && piece[j] == 0xE2u &&
                piece[j + 1u] == 0x96u && piece[j + 2u] == 0x81u) {
                if (written >= text_capacity) {
                    return COLI_ERR_RANGE;
                }
                text[written++] = ' ';
                j += 2u;
            } else {
                if (written >= text_capacity) {
                    return COLI_ERR_RANGE;
                }
                text[written++] = piece[j];
            }
        }
    }
    *out_text_bytes = written;
    return COLI_OK;
}

void coli_spm_close(coli_spm_t *spm)
{
    if (spm != NULL) {
        memset(spm, 0, sizeof(*spm));
    }
}
