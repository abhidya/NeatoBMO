#include "coli_tokenizer.h"

#include <limits.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>

#include "coli_tok_unicode.h"
#include "coli_tok_unicode_o200k.h"

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
        entry.byte_length > COLI_TOKENIZER_MAX_SPECIAL_TOKEN_BYTES) {
        return COLI_OK;
    }

    uint8_t bytes[COLI_TOKENIZER_MAX_SPECIAL_TOKEN_BYTES];
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

static coli_status_t match_any_special(const coli_tokenizer_t *tokenizer,
                                       const uint8_t *text, size_t text_bytes,
                                       size_t position, uint32_t *token_id,
                                       size_t *matched_bytes, bool *matched)
{
    *matched = false;
    *matched_bytes = 0;
    *token_id = 0;
    for (uint32_t i = 0; i < tokenizer->special_token_count; ++i) {
        uint32_t candidate = tokenizer->special_token_ids[i];
        bool candidate_matched = false;
        size_t candidate_bytes = 0;
        coli_status_t status =
            match_special(tokenizer, candidate, text, text_bytes, position,
                          &candidate_bytes, &candidate_matched);
        if (status != COLI_OK) {
            return status;
        }
        if (candidate_matched && candidate_bytes > *matched_bytes) {
            *matched = true;
            *matched_bytes = candidate_bytes;
            *token_id = candidate;
        }
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

static coli_status_t is_special_token_id(const coli_tokenizer_t *tokenizer,
                                         uint32_t token_id, bool *out_special)
{
    coli_token_entry_t entry;
    coli_status_t status = read_token_entry(tokenizer, token_id, &entry);
    if (status != COLI_OK) {
        return status;
    }
    *out_special = (entry.flags & COLI_TOKEN_FLAG_SPECIAL) != 0;
    return COLI_OK;
}

static coli_status_t apply_bpe_merges_to_plain_spans(
    const coli_tokenizer_t *tokenizer, uint32_t *token_ids,
    size_t *token_count)
{
    size_t start = 0;
    while (start < *token_count) {
        bool special = false;
        coli_status_t status =
            is_special_token_id(tokenizer, token_ids[start], &special);
        if (status != COLI_OK) {
            return status;
        }
        if (special) {
            ++start;
            continue;
        }

        size_t end = start;
        while (end < *token_count) {
            status = is_special_token_id(tokenizer, token_ids[end], &special);
            if (status != COLI_OK) {
                return status;
            }
            if (special) {
                break;
            }
            ++end;
        }

        size_t old_span_count = end - start;
        size_t new_span_count = old_span_count;
        status = apply_bpe_merges(tokenizer, token_ids + start,
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

static coli_status_t encode_byte_piece(const coli_tokenizer_t *tokenizer,
                                       const uint8_t *text, size_t start,
                                       size_t end, uint32_t *token_ids,
                                       size_t token_capacity,
                                       size_t *token_count)
{
    size_t span_start = *token_count;
    for (size_t position = start; position < end; ++position) {
        uint8_t byte_value = text[position];
        if (!tokenizer->has_byte_token[byte_value]) {
            return COLI_ERR_FORMAT;
        }
        coli_status_t status =
            push_token(tokenizer->byte_token_ids[byte_value], token_ids,
                       token_capacity, token_count);
        if (status != COLI_OK) {
            return status;
        }
    }

    size_t span_count = *token_count - span_start;
    coli_status_t status =
        apply_bpe_merges(tokenizer, token_ids + span_start, &span_count);
    if (status != COLI_OK) {
        return status;
    }
    if (span_count < *token_count - span_start) {
        *token_count = span_start + span_count;
    }
    return COLI_OK;
}

static size_t u8_advance(const uint8_t *text, size_t text_bytes,
                         size_t position, uint32_t *out_cp)
{
    uint8_t c = text[position];
    if (c < 0x80u) {
        *out_cp = c;
        return position + 1u;
    }
    if ((c >> 5) == 0x6u && position + 1u < text_bytes) {
        *out_cp = ((uint32_t)(c & 0x1fu) << 6) |
                  (uint32_t)(text[position + 1u] & 0x3fu);
        return position + 2u;
    }
    if ((c >> 4) == 0xeu && position + 2u < text_bytes) {
        *out_cp = ((uint32_t)(c & 0x0fu) << 12) |
                  ((uint32_t)(text[position + 1u] & 0x3fu) << 6) |
                  (uint32_t)(text[position + 2u] & 0x3fu);
        return position + 3u;
    }
    if ((c >> 3) == 0x1eu && position + 3u < text_bytes) {
        *out_cp = ((uint32_t)(c & 0x07u) << 18) |
                  ((uint32_t)(text[position + 1u] & 0x3fu) << 12) |
                  ((uint32_t)(text[position + 2u] & 0x3fu) << 6) |
                  (uint32_t)(text[position + 3u] & 0x3fu);
        return position + 4u;
    }
    *out_cp = c;
    return position + 1u;
}

static bool is_newline(uint32_t cp)
{
    return cp == '\r' || cp == '\n';
}

static uint32_t ascii_lower(uint32_t cp)
{
    return cp >= 'A' && cp <= 'Z' ? cp + 32u : cp;
}

static bool o200k_s1(uint32_t cp)
{
    return is_U(cp) || is_X(cp);
}

static bool o200k_s2(uint32_t cp)
{
    return is_X(cp) || (is_L(cp) && !is_U(cp));
}

static bool is_han(uint32_t cp)
{
    static const uint32_t ranges[][2] = {
        {0x2E80, 0x2E99},   {0x2E9B, 0x2EF3},   {0x2F00, 0x2FD5},
        {0x3005, 0x3005},   {0x3007, 0x3007},   {0x3021, 0x3029},
        {0x3038, 0x303B},   {0x3400, 0x4DBF},   {0x4E00, 0x9FFF},
        {0xF900, 0xFA6D},   {0xFA70, 0xFAD9},   {0x16FE2, 0x16FE3},
        {0x16FF0, 0x16FF1}, {0x20000, 0x2A6DF}, {0x2A700, 0x2B739},
        {0x2B740, 0x2B81D}, {0x2B820, 0x2CEA1}, {0x2CEB0, 0x2EBE0},
        {0x2EBF0, 0x2EE5D}, {0x2F800, 0x2FA1D}, {0x30000, 0x3134A},
        {0x31350, 0x323AF},
    };
    if (cp < ranges[0][0]) {
        return false;
    }
    int low = 0;
    int high = (int)(sizeof(ranges) / sizeof(ranges[0])) - 1;
    while (low <= high) {
        int mid = low + (high - low) / 2;
        if (cp < ranges[mid][0]) {
            high = mid - 1;
        } else if (cp > ranges[mid][1]) {
            low = mid + 1;
        } else {
            return true;
        }
    }
    return false;
}

static bool kimi_s1(uint32_t cp)
{
    return (is_U(cp) || is_X(cp)) && !is_han(cp);
}

static bool kimi_s2(uint32_t cp)
{
    return (is_X(cp) || (is_L(cp) && !is_U(cp))) && !is_han(cp);
}

static size_t o200k_contraction_end(const uint8_t *text, size_t text_bytes,
                                    size_t position)
{
    uint32_t cp;
    if (position >= text_bytes ||
        u8_advance(text, text_bytes, position, &cp) != position + 1u ||
        cp != '\'' || position + 1u >= text_bytes) {
        return position;
    }
    uint32_t d;
    size_t after_d = u8_advance(text, text_bytes, position + 1u, &d);
    d = ascii_lower(d);
    if (after_d < text_bytes) {
        uint32_t e;
        size_t after_e = u8_advance(text, text_bytes, after_d, &e);
        e = ascii_lower(e);
        if ((d == 'r' && e == 'e') || (d == 'v' && e == 'e') ||
            (d == 'l' && e == 'l')) {
            return after_e;
        }
    }
    if (d == 's' || d == 't' || d == 'm' || d == 'd') {
        return after_d;
    }
    return position;
}

static size_t o200k_letters_end(const uint8_t *text, size_t text_bytes,
                                size_t position)
{
    for (int pfx = 1; pfx >= 0; --pfx) {
        uint32_t first;
        size_t j0 = position;
        if (pfx) {
            size_t first_next = u8_advance(text, text_bytes, position, &first);
            if (is_newline(first) || is_L(first) || is_N(first) ||
                first_next >= text_bytes) {
                continue;
            }
            j0 = first_next;
        }

        size_t scan = j0;
        size_t last_s2 = SIZE_MAX;
        while (scan < text_bytes) {
            uint32_t cp;
            size_t next = u8_advance(text, text_bytes, scan, &cp);
            if (!o200k_s1(cp)) {
                break;
            }
            if (o200k_s2(cp)) {
                last_s2 = scan;
            }
            scan = next;
        }
        size_t s = scan;
        if (s < text_bytes) {
            uint32_t cp;
            (void)u8_advance(text, text_bytes, s, &cp);
            if (!o200k_s2(cp)) {
                s = last_s2;
            }
        } else {
            s = last_s2;
        }
        if (s != SIZE_MAX) {
            scan = s;
            while (scan < text_bytes) {
                uint32_t cp;
                size_t next = u8_advance(text, text_bytes, scan, &cp);
                if (!o200k_s2(cp)) {
                    break;
                }
                scan = next;
            }
            return o200k_contraction_end(text, text_bytes, scan);
        }
    }

    for (int pfx = 1; pfx >= 0; --pfx) {
        uint32_t first;
        size_t j0 = position;
        if (pfx) {
            size_t first_next = u8_advance(text, text_bytes, position, &first);
            if (is_newline(first) || is_L(first) || is_N(first) ||
                first_next >= text_bytes) {
                continue;
            }
            j0 = first_next;
        }

        size_t scan = j0;
        while (scan < text_bytes) {
            uint32_t cp;
            size_t next = u8_advance(text, text_bytes, scan, &cp);
            if (!o200k_s1(cp)) {
                break;
            }
            scan = next;
        }
        if (scan > j0) {
            while (scan < text_bytes) {
                uint32_t cp;
                size_t next = u8_advance(text, text_bytes, scan, &cp);
                if (!o200k_s2(cp)) {
                    break;
                }
                scan = next;
            }
            return o200k_contraction_end(text, text_bytes, scan);
        }
    }
    return position;
}

static size_t kimi_letters_end(const uint8_t *text, size_t text_bytes,
                               size_t position)
{
    for (int pfx = 1; pfx >= 0; --pfx) {
        uint32_t first;
        size_t j0 = position;
        if (pfx) {
            size_t first_next = u8_advance(text, text_bytes, position, &first);
            if (is_newline(first) || is_L(first) || is_N(first) ||
                first_next >= text_bytes) {
                continue;
            }
            j0 = first_next;
        }

        size_t scan = j0;
        size_t last_s2 = SIZE_MAX;
        while (scan < text_bytes) {
            uint32_t cp;
            size_t next = u8_advance(text, text_bytes, scan, &cp);
            if (!kimi_s1(cp)) {
                break;
            }
            if (kimi_s2(cp)) {
                last_s2 = scan;
            }
            scan = next;
        }
        size_t s = scan;
        if (s < text_bytes) {
            uint32_t cp;
            (void)u8_advance(text, text_bytes, s, &cp);
            if (!kimi_s2(cp)) {
                s = last_s2;
            }
        } else {
            s = last_s2;
        }
        if (s != SIZE_MAX) {
            scan = s;
            while (scan < text_bytes) {
                uint32_t cp;
                size_t next = u8_advance(text, text_bytes, scan, &cp);
                if (!kimi_s2(cp)) {
                    break;
                }
                scan = next;
            }
            return o200k_contraction_end(text, text_bytes, scan);
        }
    }

    for (int pfx = 1; pfx >= 0; --pfx) {
        uint32_t first;
        size_t j0 = position;
        if (pfx) {
            size_t first_next = u8_advance(text, text_bytes, position, &first);
            if (is_newline(first) || is_L(first) || is_N(first) ||
                first_next >= text_bytes) {
                continue;
            }
            j0 = first_next;
        }

        size_t scan = j0;
        while (scan < text_bytes) {
            uint32_t cp;
            size_t next = u8_advance(text, text_bytes, scan, &cp);
            if (!kimi_s1(cp)) {
                break;
            }
            scan = next;
        }
        if (scan > j0) {
            while (scan < text_bytes) {
                uint32_t cp;
                size_t next = u8_advance(text, text_bytes, scan, &cp);
                if (!kimi_s2(cp)) {
                    break;
                }
                scan = next;
            }
            return o200k_contraction_end(text, text_bytes, scan);
        }
    }
    return position;
}

static size_t regex_next_piece_end(const uint8_t *text, size_t text_bytes,
                                   size_t position, bool kimi)
{
    uint32_t cp;
    size_t next = u8_advance(text, text_bytes, position, &cp);

    if (kimi && is_han(cp)) {
        size_t scan = position;
        while (scan < text_bytes) {
            uint32_t han_cp;
            size_t han_next = u8_advance(text, text_bytes, scan, &han_cp);
            if (!is_han(han_cp)) {
                break;
            }
            scan = han_next;
        }
        return scan;
    }

    size_t end = kimi ? kimi_letters_end(text, text_bytes, position)
                      : o200k_letters_end(text, text_bytes, position);
    if (end > position) {
        return end;
    }

    if (is_N(cp)) {
        size_t scan = position;
        int count = 0;
        while (scan < text_bytes && count < 3) {
            uint32_t digit_cp;
            size_t digit_next = u8_advance(text, text_bytes, scan, &digit_cp);
            if (!is_N(digit_cp)) {
                break;
            }
            scan = digit_next;
            ++count;
        }
        return scan;
    }

    size_t scan = position;
    if (cp == ' ' && next < text_bytes) {
        uint32_t following;
        (void)u8_advance(text, text_bytes, next, &following);
        if (!is_S(following) && !is_L(following) && !is_N(following)) {
            scan = next;
            cp = following;
        }
    }
    if (!is_S(cp) && !is_L(cp) && !is_N(cp)) {
        while (scan < text_bytes) {
            uint32_t punct;
            size_t punct_next = u8_advance(text, text_bytes, scan, &punct);
            if (is_S(punct) || is_L(punct) || is_N(punct)) {
                break;
            }
            scan = punct_next;
        }
        while (scan < text_bytes) {
            uint32_t tail;
            size_t tail_next = u8_advance(text, text_bytes, scan, &tail);
            if (!is_newline(tail) && (kimi || tail != '/')) {
                break;
            }
            scan = tail_next;
        }
        return scan;
    }

    scan = position;
    while (scan < text_bytes) {
        uint32_t ws;
        size_t ws_next = u8_advance(text, text_bytes, scan, &ws);
        if (!is_S(ws)) {
            break;
        }
        scan = ws_next;
    }
    if (scan > position) {
        size_t last_newline_end = SIZE_MAX;
        size_t ws_scan = position;
        while (ws_scan < scan) {
            uint32_t ws;
            size_t ws_next = u8_advance(text, text_bytes, ws_scan, &ws);
            if (is_newline(ws)) {
                last_newline_end = ws_next;
            }
            ws_scan = ws_next;
        }
        if (last_newline_end != SIZE_MAX) {
            return last_newline_end;
        }
        if (scan < text_bytes) {
            size_t prev = position;
            size_t cur = position;
            while (cur < scan) {
                prev = cur;
                uint32_t ws;
                cur = u8_advance(text, text_bytes, cur, &ws);
            }
            return prev == position ? cur : prev;
        }
        return scan;
    }

    return next;
}

static size_t o200k_next_piece_end(const uint8_t *text, size_t text_bytes,
                                   size_t position)
{
    return regex_next_piece_end(text, text_bytes, position, false);
}

static size_t kimi_next_piece_end(const uint8_t *text, size_t text_bytes,
                                  size_t position)
{
    return regex_next_piece_end(text, text_bytes, position, true);
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
    tokenizer->pretokenizer_family = get_u16(header + 62);
    tokenizer->special_token_count = get_u32(header + 64);
    tokenizer->special_token_offset = get_u64(header + 68);

    if (tokenizer->vocab_size == 0 ||
        tokenizer->max_token_bytes == 0 ||
        tokenizer->max_token_bytes > COLI_TOKENIZER_MAX_TOKEN_BYTES ||
        tokenizer->pretokenizer_family > COLI_TOKENIZER_PRETOKENIZER_KIMI_K3 ||
        tokenizer->special_token_count > COLI_TOKENIZER_MAX_SPECIAL_TOKENS ||
        (tokenizer->special_token_count > 0 &&
         tokenizer->special_token_offset == 0) ||
        byte_token_count > 256u ||
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
        tokenizer->token_data_offset > coli_store_size(store) ||
        (tokenizer->special_token_count > 0 &&
         (tokenizer->special_token_offset > coli_store_size(store) ||
          (uint64_t)tokenizer->special_token_count * sizeof(uint32_t) >
              coli_store_size(store) - tokenizer->special_token_offset))) {
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

    /* A complete 256-entry byte alphabet is not required. Real byte-level BPE
     * vocabularies omit standalone tokens for byte values that cannot begin or
     * appear in valid UTF-8 (allenai/OLMoE-1B-7B-0924 has no token for 0xC0,
     * 0xC1, or 0xF5..0xFF). Demanding all 256 rejected those tokenizers
     * outright. Encoding already fails per byte through has_byte_token, so a
     * gap only matters if such a byte actually shows up in the input. */
    if (!tokenizer->has_byte_token[(uint8_t)'\n'] ||
        !tokenizer->has_byte_token[(uint8_t)' ']) {
        coli_tokenizer_close(tokenizer);
        return COLI_ERR_FORMAT;
    }

    if (tokenizer->special_token_count > 0) {
        for (uint32_t i = 0; i < tokenizer->special_token_count; ++i) {
            uint8_t raw[4];
            status = coli_store_read_at(
                tokenizer->store,
                tokenizer->special_token_offset + (uint64_t)i * sizeof(raw),
                raw, sizeof(raw));
            if (status != COLI_OK) {
                coli_tokenizer_close(tokenizer);
                return status;
            }
            uint32_t token_id = get_u32(raw);
            coli_token_entry_t entry;
            status = read_token_entry(tokenizer, token_id, &entry);
            if (status != COLI_OK ||
                (entry.flags & COLI_TOKEN_FLAG_SPECIAL) == 0) {
                coli_tokenizer_close(tokenizer);
                return status == COLI_OK ? COLI_ERR_FORMAT : status;
            }
            tokenizer->special_token_ids[i] = token_id;
            for (uint32_t j = 0; j < i; ++j) {
                if (tokenizer->special_token_ids[j] == token_id) {
                    coli_tokenizer_close(tokenizer);
                    return COLI_ERR_FORMAT;
                }
            }
        }
    } else {
        tokenizer->special_token_ids[tokenizer->special_token_count++] =
            tokenizer->eos_token_id;
        if (tokenizer->pad_token_id != tokenizer->eos_token_id) {
            tokenizer->special_token_ids[tokenizer->special_token_count++] =
                tokenizer->pad_token_id;
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
            bool matched = false;
            size_t matched_bytes = 0;
            uint32_t special_id = 0;
            coli_status_t status =
                match_any_special(tokenizer, text, text_bytes, position,
                                  &special_id, &matched_bytes, &matched);
            if (status != COLI_OK) {
                return status;
            }
            if (matched) {
                status = push_token(special_id, token_ids, token_capacity,
                                    &token_count);
                if (status != COLI_OK) {
                    return status;
                }
                position += matched_bytes;
                continue;
            }
        }

        size_t plain_end = position + 1u;
        if (tokenizer->pretokenizer_family ==
            COLI_TOKENIZER_PRETOKENIZER_O200K) {
            plain_end = o200k_next_piece_end(text, text_bytes, position);
        } else if (tokenizer->pretokenizer_family ==
                   COLI_TOKENIZER_PRETOKENIZER_KIMI_K3) {
            plain_end = kimi_next_piece_end(text, text_bytes, position);
        }
        coli_status_t status =
            encode_byte_piece(tokenizer, text, position, plain_end, token_ids,
                              token_capacity, &token_count);
        if (status != COLI_OK) {
            return status;
        }
        position = plain_end;
    }

    if (tokenizer->pretokenizer_family ==
        COLI_TOKENIZER_PRETOKENIZER_BYTE_BPE) {
        coli_status_t status =
            apply_bpe_merges_to_plain_spans(tokenizer, token_ids,
                                            &token_count);
        if (status != COLI_OK) {
            return status;
        }
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
