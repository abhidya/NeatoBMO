#pragma once

/* Raw bridge to the Neato mainboard's P6 debug UART.
 *
 * P6.2 (AT91 RX) <- ESP32 GPIO17 (TX)
 * P6.3 (AT91 TX) -> ESP32 GPIO18 (RX)
 * P6.4 (GND)     -- ESP32 GND
 */
void debug_uart_start(void);
