#include "at91sam9xe_min.h"

#include <stdint.h>

static const char kBanner[] = "NEATOOS RAW V0\r\n";

static void dbgu_putc(char value) {
    while ((AT91_DBGU_CSR & AT91_DBGU_CSR_TXRDY) == 0u) {
    }
    AT91_DBGU_THR = (uint32_t)(uint8_t)value;
}

static void dbgu_puts(const char *value) {
    while (*value != '\0') {
        dbgu_putc(*value++);
    }
}

void neatoos_main(void) {
    /*
     * P6 prints stock bootstrap logs before the application starts, so Phase A
     * preserves the inherited DBGU mode/baud configuration and only enables TX.
     */
    AT91_DBGU_CR = AT91_DBGU_CR_TXEN;

    for (;;) {
        dbgu_puts(kBanner);
    }
}
