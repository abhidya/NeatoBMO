#include "neato_serial.h"

#include <stdio.h>

static void stdout_write(void *context, const uint8_t *data, size_t size) {
    (void)context;
    (void)fwrite(data, 1u, size, stdout);
    (void)fflush(stdout);
}

int main(void) {
    neato_serial_t serial;
    uint8_t buffer[256];
    size_t received;

    neato_serial_init(&serial, stdout_write, NULL);
    while ((received = fread(buffer, 1u, sizeof(buffer), stdin)) != 0u) {
        neato_serial_feed(&serial, buffer, received);
    }
    return ferror(stdin) != 0 ? 1 : 0;
}
