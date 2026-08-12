#ifndef NEATOOS_AT91SAM9XE_MIN_H
#define NEATOOS_AT91SAM9XE_MIN_H

#include <stdint.h>

/*
 * Minimal AT91SAM9XE DBGU definitions for the offline Phase A raw canary.
 *
 * Address hypothesis: Cruz Rev113 is documented in this repository as
 * AT91SAM9XE-based, and P6 visibly emits bootstrap logs before the application.
 * The DBGU base below is the AT91SAM9XE system-controller DBGU base used by
 * Atmel documentation, but this Phase A tree does not claim the application
 * canary has been uploaded or executed.
 */
#define AT91_DBGU_BASE 0xFFFFF200u

#define AT91_REG32(address) (*(volatile uint32_t *)(uintptr_t)(address))

#define AT91_DBGU_CR AT91_REG32(AT91_DBGU_BASE + 0x00u)
#define AT91_DBGU_THR AT91_REG32(AT91_DBGU_BASE + 0x1Cu)
#define AT91_DBGU_CSR AT91_REG32(AT91_DBGU_BASE + 0x14u)

#define AT91_DBGU_CR_TXEN (1u << 6)

#define AT91_DBGU_CSR_TXRDY (1u << 1)

#endif
