/* Encode stdin with a .ctok tokenizer and print the token ids, one per line.
 * Used to check the exported CTOK against the reference tokenizer. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "coli_tokenizer.h"

int main(int argc, char **argv)
{
    if (argc < 2) {
        fprintf(stderr, "usage: %s tokenizer.ctok < text\n", argv[0]);
        return 2;
    }
    coli_store_t *store = NULL;
    if (coli_store_open_file(argv[1], &store) != COLI_OK) {
        fprintf(stderr, "cannot open %s\n", argv[1]);
        return 1;
    }
    coli_tokenizer_t tokenizer;
    coli_status_t status = coli_tokenizer_open(store, &tokenizer);
    if (status != COLI_OK) {
        fprintf(stderr, "coli_tokenizer_open failed: %d\n", status);
        return 1;
    }

    static uint8_t text[1u << 20];
    size_t text_bytes = fread(text, 1, sizeof(text), stdin);

    static uint32_t ids[1u << 20];
    size_t count = 0;
    status = coli_tokenizer_encode(&tokenizer, text, text_bytes, ids,
                                   sizeof(ids) / sizeof(ids[0]), &count, 0);
    if (status != COLI_OK) {
        fprintf(stderr, "coli_tokenizer_encode failed: %d\n", status);
        return 1;
    }
    for (size_t i = 0; i < count; ++i) printf("%u\n", ids[i]);
    coli_tokenizer_close(&tokenizer);
    coli_store_close(store);
    return 0;
}
