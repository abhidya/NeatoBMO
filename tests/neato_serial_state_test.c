#include "neato_serial.h"

#include <stddef.h>
#include <stdint.h>

static void discard(void *context, const uint8_t *data, size_t size) {
    (void)context;
    (void)data;
    (void)size;
}

int main(void) {
    static const uint8_t on[] = "TestMode On\n";
    static const uint8_t off[] = "TestMode Off\n";
    neato_serial_t serial;

    neato_serial_init(&serial, discard, NULL);
    if (neato_serial_test_mode(&serial) != 0u) {
        return 1;
    }
    neato_serial_feed(&serial, on, sizeof(on) - 1u);
    if (neato_serial_test_mode(&serial) != 1u) {
        return 2;
    }
    neato_serial_feed(&serial, off, sizeof(off) - 1u);
    if (neato_serial_test_mode(&serial) != 0u) {
        return 3;
    }
    return 0;
}
