#include "coli_tokenizer.h"

#include <limits.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>

#define COLI_TOKEN_FLAG_SPECIAL 0x0001u
#define COLI_TOKEN_FLAG_BYTE 0x0002u

typedef struct {
    uint64_t data_offset;
    uint16_t byte_length;
    uint16_t flags;
    uint32_t byte_value;
} coli_token_entry_t;

typedef struct {
    uint32_t left;
    uint32_t right;
    uint32_t result;
    uint32_t rank;
} coli_merge_t;

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

static coli_status_t read_token_entry(const coli_tokenizer_t *tokenizer,
                                      uint32_t token_id,
                                      coli_token_entry_t *entry)
{
    if (tokenizer == NULL || entry == NULL || token_id >= tokenizer->vocab_size) {
        return COLI_ERR_ARGUMENT;
    }

    uint8_t raw[COLI_TOKENIZER_ENTRY_BYTES];
    uint64_t offset = tokenizer->vocab_offset +
                      (uint64_t)token_id * COLI_TOKENIZER_ENTRY_BYTES;
    coli_status_t status = coli_store_read_at(tokenizer->store, offset, raw,
                                              sizeof(raw));
    if (status != COLI_OK) {
        return status;
    }

    entry->data_offset = get_u64(raw);
    entry->byte_length = get_u16(raw + 8);
    entry->flags = get_u16(raw + 10);
    entry->byte_value = get_u32(raw + 12);
    return COLI_OK;
}

static coli_status_t read_merge_at(const coli_tokenizer_t *tokenizer,
                                   uint32_t index,
                                   coli_merge_t *merge)
{
    if (tokenizer == NULL || merge == NULL || index >= tokenizer->merge_count) {
        return COLI_ERR_ARGUMENT;
    }

    uint8_t raw[COLI_TOKENIZER_MERGE_BYTES];
    uint64_t offset = tokenizer->merge_offset +
                      (uint64_t)index * COLI_TOKENIZER_MERGE_BYTES;
    coli_status_t status = coli_store_read_at(tokenizer->store, offset, raw,
                                              sizeof(raw));
    if (status != COLI_OK) {
        return status;
    }

    merge->left = get_u32(raw);
    merge->right = get_u32(raw + 4);
    merge->result = get_u32(raw + 8);
    merge->rank = get_u32(raw + 12);
    return COLI_OK;
}

static int compare_pair(uint32_t left_a, uint32_t right_a,
                        uint32_t left_b, uint32_t right_b)
{
    if (left_a != left_b) {
        return left_a < left_b ? -1 : 1;
    }
    if (right_a != right_b) {
        return right_a < right_b ? -1 : 1;
    }
    return 0;
}

static coli_status_t find_merge(const coli_tokenizer_t *tokenizer,
                                uint32_t left, uint32_t right,
                                coli_merge_t *out_merge, bool *found)
{
    uint32_t low = 0;
    uint32_t high = tokenizer->merge_count;
    *found = false;

    while (low < high) {
        uint32_t mid = low + (high - low) / 2u;
        coli_merge_t merge;
        coli_status_t status = read_merge_at(tokenizer, mid, &merge);
        if (status != COLI_OK) {
            return status;
        }
        int cmp = compare_pair(merge.left, merge.right, left, right);
        if (cmp == 0) {
            *out_merge = merge;
            *found = true;
            return COLI_OK;
        }
        if (cmp < 0) {
            low = mid + 1u;
        } else {
            high = mid;
        }
    }

    return COLI_OK;
}

static coli_status_t read_token_bytes(const coli_tokenizer_t *tokenizer,
                                      const coli_token_entry_t *entry,
                                      uint8_t *destination,
                                      size_t destination_capacity)
{
    if (entry->byte_length > destination_capacity) {
        return COLI_ERR_RANGE;
    }
    if (entry->byte_length == 0) {
        return COLI_OK;
    }
    if (entry->data_offset < tokenizer->token_data_offset ||
        entry->data_offset > coli_store_size(tokenizer->store) ||
        entry->byte_length > coli_store_size(tokenizer->store) - entry->data_offset) {
        return COLI_ERR_FORMAT;
    }
    return coli_store_read_at(tokenizer->store, entry->data_offset, destination,
                              entry->byte_length);
}

static coli_status_t match_special(const coli_tokenizer_t *tokenizer,
                                   uint32_t token_id,
                                   const uint8_t *text, size_t text_bytes,
                                   size_t position, size_t *matched_bytes,
                                   bool *matched)
{
    *matched = false;
    *matched_bytes = 0;
    if (token_id >= tokenizer->vocab_size) {
        return COLI_OK;
    }

    coli_token_entry_t entry;
    coli_status_t status = read_token_entry(tokenizer, token_id, &entry);
    if (status != COLI_OK) {
        return status;
    }
    if ((entry.flags & COLI_TOKEN_FLAG_SPECIAL) == 0 ||
        entry.byte_length == 0 ||
        position + entry.byte_length > text_bytes ||
        entry.byte_length > COLI_TOKENIZER_MAX_TOKEN_BYTES) {
        return COLI_OK;
    }

    uint8_t bytes[COLI_TOKENIZER_MAX_TOKEN_BYTES];
    status = read_token_bytes(tokenizer, &entry, bytes, sizeof(bytes));
    if (status != COLI_OK) {
        return status;
    }
    if (memcmp(text + position, bytes, entry.byte_length) == 0) {
        *matched = true;
        *matched_bytes = entry.byte_length;
    }
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

static coli_status_t apply_bpe_merges(const coli_tokenizer_t *tokenizer,
                                      uint32_t *token_ids,
                                      size_t *token_count)
{
    if (*token_count < 2) {
        return COLI_OK;
    }

    for (;;) {
        bool have_best = false;
        size_t best_index = 0;
        coli_merge_t best_merge = {0};

        for (size_t i = 0; i + 1u < *token_count; ++i) {
            coli_merge_t merge;
            bool found = false;
            coli_status_t status = find_merge(tokenizer, token_ids[i],
                                              token_ids[i + 1u], &merge,
                                              &found);
            if (status != COLI_OK) {
                return status;
            }
            if (found && (!have_best || merge.rank < best_merge.rank)) {
                have_best = true;
                best_index = i;
                best_merge = merge;
            }
        }

        if (!have_best) {
            return COLI_OK;
        }

        token_ids[best_index] = best_merge.result;
        for (size_t i = best_index + 1u; i + 1u < *token_count; ++i) {
            token_ids[i] = token_ids[i + 1u];
        }
        --*token_count;
        if (*token_count < 2) {
            return COLI_OK;
        }
    }
}

static bool is_special_token_id(const coli_tokenizer_t *tokenizer,
                                uint32_t token_id)
{
    return token_id == tokenizer->eos_token_id ||
           token_id == tokenizer->pad_token_id;
}

static coli_status_t apply_bpe_merges_to_plain_spans(
    const coli_tokenizer_t *tokenizer, uint32_t *token_ids,
    size_t *token_count)
{
    size_t start = 0;
    while (start < *token_count) {
        if (is_special_token_id(tokenizer, token_ids[start])) {
            ++start;
            continue;
        }

        size_t end = start;
        while (end < *token_count &&
               !is_special_token_id(tokenizer, token_ids[end])) {
            ++end;
        }

        size_t old_span_count = end - start;
        size_t new_span_count = old_span_count;
        coli_status_t status = apply_bpe_merges(tokenizer, token_ids + start,
                                                &new_span_count);
        if (status != COLI_OK) {
            return status;
        }
        if (new_span_count < old_span_count) {
            size_t removed = old_span_count - new_span_count;
            for (size_t i = start + new_span_count; i + removed < *token_count;
                 ++i) {
                token_ids[i] = token_ids[i + removed];
            }
            *token_count -= removed;
            end -= removed;
        }
        start = end;
    }
    return COLI_OK;
}

static coli_status_t validate_token_entry(const coli_tokenizer_t *tokenizer,
                                          uint32_t token_id,
                                          coli_token_entry_t *entry)
{
    coli_status_t status = read_token_entry(tokenizer, token_id, entry);
    if (status != COLI_OK) {
        return status;
    }
    if (entry->byte_length > tokenizer->max_token_bytes ||
        entry->data_offset > coli_store_size(tokenizer->store) ||
        entry->byte_length > coli_store_size(tokenizer->store) - entry->data_offset) {
        return COLI_ERR_FORMAT;
    }
    if ((entry->flags & COLI_TOKEN_FLAG_BYTE) != 0 &&
        (entry->byte_value > 255u || entry->byte_length != 1u)) {
        return COLI_ERR_FORMAT;
    }
    return COLI_OK;
}

coli_status_t coli_tokenizer_open(coli_store_t *store,
                                  coli_tokenizer_t *tokenizer)
{
    if (store == NULL || tokenizer == NULL) {
        return COLI_ERR_ARGUMENT;
    }

    memset(tokenizer, 0, sizeof(*tokenizer));
    uint8_t header[COLI_TOKENIZER_HEADER_BYTES];
    coli_status_t status = coli_store_read_at(store, 0, header, sizeof(header));
    if (status != COLI_OK) {
        return status;
    }
    if (memcmp(header, COLI_TOKENIZER_MAGIC, 4) != 0 ||
        get_u16(header + 4) != COLI_TOKENIZER_VERSION ||
        get_u16(header + 6) != COLI_TOKENIZER_HEADER_BYTES ||
        get_u32(header + 16) != COLI_TOKENIZER_ENTRY_BYTES ||
        get_u32(header + 20) != COLI_TOKENIZER_MERGE_BYTES) {
        return COLI_ERR_FORMAT;
    }

    tokenizer->store = store;
    tokenizer->vocab_size = get_u32(header + 8);
    tokenizer->merge_count = get_u32(header + 12);
    tokenizer->vocab_offset = get_u64(header + 24);
    tokenizer->token_data_offset = get_u64(header + 32);
    tokenizer->merge_offset = get_u64(header + 40);
    uint32_t byte_token_count = get_u32(header + 48);
    tokenizer->pad_token_id = get_u32(header + 52);
    tokenizer->eos_token_id = get_u32(header + 56);
    tokenizer->max_token_bytes = get_u16(header + 60);

    if (tokenizer->vocab_size == 0 ||
        tokenizer->max_token_bytes == 0 ||
        tokenizer->max_token_bytes > COLI_TOKENIZER_MAX_TOKEN_BYTES ||
        byte_token_count != 256u ||
        tokenizer->vocab_offset < COLI_TOKENIZER_HEADER_BYTES ||
        tokenizer->vocab_offset > coli_store_size(store) ||
        tokenizer->vocab_size >
            (UINT64_MAX - tokenizer->vocab_offset) / COLI_TOKENIZER_ENTRY_BYTES ||
        tokenizer->merge_count >
            (UINT64_MAX - tokenizer->merge_offset) / COLI_TOKENIZER_MERGE_BYTES) {
        return COLI_ERR_FORMAT;
    }

    uint64_t vocab_bytes = (uint64_t)tokenizer->vocab_size *
                           COLI_TOKENIZER_ENTRY_BYTES;
    uint64_t merge_bytes = (uint64_t)tokenizer->merge_count *
                           COLI_TOKENIZER_MERGE_BYTES;
    if (vocab_bytes > coli_store_size(store) - tokenizer->vocab_offset ||
        tokenizer->merge_offset > coli_store_size(store) ||
        merge_bytes > coli_store_size(store) - tokenizer->merge_offset ||
        tokenizer->token_data_offset > coli_store_size(store)) {
        return COLI_ERR_FORMAT;
    }

    for (uint32_t token_id = 0; token_id < tokenizer->vocab_size; ++token_id) {
        coli_token_entry_t entry;
        status = validate_token_entry(tokenizer, token_id, &entry);
        if (status != COLI_OK) {
            coli_tokenizer_close(tokenizer);
            return status;
        }
        if ((entry.flags & COLI_TOKEN_FLAG_BYTE) != 0) {
            uint32_t byte_value = entry.byte_value;
            if (tokenizer->has_byte_token[byte_value]) {
                coli_tokenizer_close(tokenizer);
                return COLI_ERR_FORMAT;
            }
            tokenizer->has_byte_token[byte_value] = true;
            tokenizer->byte_token_ids[byte_value] = token_id;
        }
    }

    for (uint32_t byte_value = 0; byte_value < 256u; ++byte_value) {
        if (!tokenizer->has_byte_token[byte_value]) {
            coli_tokenizer_close(tokenizer);
            return COLI_ERR_FORMAT;
        }
    }

    if (tokenizer->merge_count > 0) {
        coli_merge_t previous;
        status = read_merge_at(tokenizer, 0, &previous);
        if (status != COLI_OK) {
            coli_tokenizer_close(tokenizer);
            return status;
        }
        if (previous.left >= tokenizer->vocab_size ||
            previous.right >= tokenizer->vocab_size ||
            previous.result >= tokenizer->vocab_size) {
            coli_tokenizer_close(tokenizer);
            return COLI_ERR_FORMAT;
        }
        for (uint32_t i = 1; i < tokenizer->merge_count; ++i) {
            coli_merge_t current;
            status = read_merge_at(tokenizer, i, &current);
            if (status != COLI_OK) {
                coli_tokenizer_close(tokenizer);
                return status;
            }
            if (current.left >= tokenizer->vocab_size ||
                current.right >= tokenizer->vocab_size ||
                current.result >= tokenizer->vocab_size ||
                compare_pair(previous.left, previous.right, current.left,
                             current.right) >= 0) {
                coli_tokenizer_close(tokenizer);
                return COLI_ERR_FORMAT;
            }
            previous = current;
        }
    }

    return COLI_OK;
}

coli_status_t coli_tokenizer_encode(const coli_tokenizer_t *tokenizer,
                                    const uint8_t *text, size_t text_bytes,
                                    uint32_t *token_ids,
                                    size_t token_capacity,
                                    size_t *out_token_count,
                                    uint32_t flags)
{
    if (tokenizer == NULL || (text == NULL && text_bytes != 0) ||
        token_ids == NULL || out_token_count == NULL) {
        return COLI_ERR_ARGUMENT;
    }

    size_t token_count = 0;
    size_t position = 0;
    while (position < text_bytes) {
        if ((flags & COLI_TOKENIZER_ENCODE_ALLOW_SPECIAL) != 0) {
            const uint32_t special_ids[] = {tokenizer->eos_token_id,
                                            tokenizer->pad_token_id};
            bool matched_any = false;
            for (size_t i = 0; i < sizeof(special_ids) / sizeof(special_ids[0]);
                 ++i) {
                bool matched = false;
                size_t matched_bytes = 0;
                coli_status_t status = match_special(tokenizer, special_ids[i],
                                                     text, text_bytes, position,
                                                     &matched_bytes, &matched);
                if (status != COLI_OK) {
                    return status;
                }
                if (matched) {
                    status = push_token(special_ids[i], token_ids,
                                        token_capacity, &token_count);
                    if (status != COLI_OK) {
                        return status;
                    }
                    position += matched_bytes;
                    matched_any = true;
                    break;
                }
            }
            if (matched_any) {
                continue;
            }
        }

        uint8_t byte_value = text[position];
        if (!tokenizer->has_byte_token[byte_value]) {
            return COLI_ERR_FORMAT;
        }
        coli_status_t status = push_token(tokenizer->byte_token_ids[byte_value],
                                          token_ids, token_capacity,
                                          &token_count);
        if (status != COLI_OK) {
            return status;
        }
        ++position;
    }

    coli_status_t status = apply_bpe_merges_to_plain_spans(tokenizer, token_ids,
                                                           &token_count);
    if (status != COLI_OK) {
        return status;
    }
    *out_token_count = token_count;
    return COLI_OK;
}

coli_status_t coli_tokenizer_decode(const coli_tokenizer_t *tokenizer,
                                    const uint32_t *token_ids,
                                    size_t token_count,
                                    uint8_t *text,
                                    size_t text_capacity,
                                    size_t *out_text_bytes)
{
    if (tokenizer == NULL || (token_ids == NULL && token_count != 0) ||
        text == NULL || out_text_bytes == NULL) {
        return COLI_ERR_ARGUMENT;
    }

    size_t written = 0;
    for (size_t i = 0; i < token_count; ++i) {
        coli_token_entry_t entry;
        coli_status_t status = read_token_entry(tokenizer, token_ids[i], &entry);
        if (status != COLI_OK) {
            return status;
        }
        if (entry.byte_length > text_capacity - written) {
            return COLI_ERR_RANGE;
        }
        status = read_token_bytes(tokenizer, &entry, text + written,
                                  text_capacity - written);
        if (status != COLI_OK) {
            return status;
        }
        written += entry.byte_length;
    }

    *out_text_bytes = written;
    return COLI_OK;
}

void coli_tokenizer_close(coli_tokenizer_t *tokenizer)
{
    if (tokenizer != NULL) {
        memset(tokenizer, 0, sizeof(*tokenizer));
    }
}
