#ifndef NEATO_SERIAL_H
#define NEATO_SERIAL_H

#include <stddef.h>
#include <stdint.h>

#define NEATO_SERIAL_LINE_CAPACITY 128u
#define NEATO_SERIAL_TERMINATOR 0x1au

typedef void (*neato_serial_write_fn)(void *context,
                                      const uint8_t *data,
                                      size_t size);

typedef struct {
    neato_serial_write_fn write;
    void *write_context;
    char line[NEATO_SERIAL_LINE_CAPACITY];
    size_t line_length;
    uint8_t test_mode;
    uint8_t overflowed;
} neato_serial_t;

void neato_serial_init(neato_serial_t *serial,
                       neato_serial_write_fn write,
                       void *write_context);
void neato_serial_feed(neato_serial_t *serial,
                       const uint8_t *data,
                       size_t size);
uint8_t neato_serial_test_mode(const neato_serial_t *serial);

#endif
