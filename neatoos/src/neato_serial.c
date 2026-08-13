#include "neato_serial.h"

static const char kVersionBody[] =
    "Component,Major,Minor,Build\r\n"
    "ModelID,-1,XV12,\r\n"
    "ConfigID,1,,\r\n"
    "Serial Number,NEATOOS,0000000,P\r\n"
    "Software,0,1,0\r\n"
    "MainBoard Version,7,1,\r\n"
    "ChassisRev,1,,\r\n"
    "UIPanelRev,1,,\r\n";

static const char kHelpBody[] =
    "Help - Without any argument, this prints a list of supported cmds.\r\n"
    "GetVersion - Get the version information for the system software and hardware.\r\n"
    "Help - Print the commands implemented by this clean-room serial slice.\r\n"
    "TestMode - Sets TestMode on or off. No actuator commands are enabled.\r\n";

static const char kTestModeHelp[] =
    "TestMode - Sets TestMode on or off. No actuator commands are enabled.\r\n"
    "    On - Turns TestMode on. Mutually exclusive with Off.\r\n"
    "    Off - Turns TestMode off. Mutually exclusive with On.\r\n";

static size_t string_length(const char *value) {
    size_t length = 0u;
    while (value[length] != '\0') {
        ++length;
    }
    return length;
}

static uint8_t ascii_lower(uint8_t value) {
    if (value >= (uint8_t)'A' && value <= (uint8_t)'Z') {
        return (uint8_t)(value + ((uint8_t)'a' - (uint8_t)'A'));
    }
    return value;
}

static uint8_t command_equals(const char *actual, const char *expected) {
    size_t index = 0u;
    while (actual[index] != '\0' && expected[index] != '\0') {
        if (ascii_lower((uint8_t)actual[index]) !=
            ascii_lower((uint8_t)expected[index])) {
            return 0u;
        }
        ++index;
    }
    return (uint8_t)(actual[index] == '\0' && expected[index] == '\0');
}

static void write_bytes(neato_serial_t *serial,
                        const uint8_t *data,
                        size_t size) {
    if (size != 0u) {
        serial->write(serial->write_context, data, size);
    }
}

static void write_string(neato_serial_t *serial, const char *value) {
    write_bytes(serial, (const uint8_t *)value, string_length(value));
}

static void write_decimal(neato_serial_t *serial, size_t value) {
    char digits[3u * sizeof(size_t)];
    size_t count = 0u;
    do {
        digits[count++] = (char)('0' + (value % 10u));
        value /= 10u;
    } while (value != 0u);
    while (count != 0u) {
        --count;
        write_bytes(serial, (const uint8_t *)&digits[count], 1u);
    }
}

static void write_terminated(neato_serial_t *serial) {
    const uint8_t terminator = NEATO_SERIAL_TERMINATOR;
    write_bytes(serial, &terminator, 1u);
}

static void write_help(neato_serial_t *serial,
                       const char *command,
                       const char *body) {
    write_string(serial, command);
    write_string(serial, "\r\nHelp Strlen = ");
    write_decimal(serial, string_length(body));
    write_string(serial, "\r\n");
    write_string(serial, body);
    write_string(serial, "\r\n");
    write_terminated(serial);
}

static void dispatch(neato_serial_t *serial) {
    serial->line[serial->line_length] = '\0';

    if (serial->overflowed != 0u) {
        write_terminated(serial);
    } else if (command_equals(serial->line, "GetVersion") != 0u) {
        write_string(serial, "GetVersion\r\n");
        write_string(serial, kVersionBody);
        write_terminated(serial);
    } else if (command_equals(serial->line, "Help") != 0u) {
        write_help(serial, "Help", kHelpBody);
    } else if (command_equals(serial->line, "Help TestMode") != 0u) {
        write_help(serial, "Help TestMode", kTestModeHelp);
    } else if (command_equals(serial->line, "TestMode On") != 0u) {
        serial->test_mode = 1u;
        write_string(serial, "TestMode On\r\n");
        write_terminated(serial);
    } else if (command_equals(serial->line, "TestMode Off") != 0u) {
        serial->test_mode = 0u;
        write_string(serial, "TestMode Off\r\n");
        write_terminated(serial);
    } else {
        write_string(serial, serial->line);
        write_string(serial, "\r\n");
        write_terminated(serial);
    }

    serial->line_length = 0u;
    serial->overflowed = 0u;
}

void neato_serial_init(neato_serial_t *serial,
                       neato_serial_write_fn write,
                       void *write_context) {
    serial->write = write;
    serial->write_context = write_context;
    serial->line_length = 0u;
    serial->test_mode = 0u;
    serial->overflowed = 0u;
}

void neato_serial_feed(neato_serial_t *serial,
                       const uint8_t *data,
                       size_t size) {
    size_t index;
    for (index = 0u; index < size; ++index) {
        const uint8_t value = data[index];
        if (value == (uint8_t)'\r' || value == (uint8_t)'\n') {
            if (serial->line_length != 0u || serial->overflowed != 0u) {
                dispatch(serial);
            }
        } else if (serial->overflowed == 0u) {
            if (serial->line_length + 1u < NEATO_SERIAL_LINE_CAPACITY) {
                serial->line[serial->line_length++] = (char)value;
            } else {
                serial->overflowed = 1u;
            }
        }
    }
}

uint8_t neato_serial_test_mode(const neato_serial_t *serial) {
    return serial->test_mode;
}
