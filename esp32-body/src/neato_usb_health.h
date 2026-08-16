#pragma once

#include <stdbool.h>
#include <stdint.h>

#define NEATO_USB_HEALTH_RX_STALL_MS 90000U
#define NEATO_USB_HEALTH_STALL_PROBES 3U
#define NEATO_USB_HEALTH_TX_FAILURES 3U

typedef enum {
    NEATO_USB_HEALTH_OK = 0,
    NEATO_USB_HEALTH_RECOVER_RX_STALL,
    NEATO_USB_HEALTH_RECOVER_TX_FAILURES,
} neato_usb_health_result_t;

typedef struct {
    bool connected;
    uint32_t connected_ms;
    uint32_t last_rx_ms;
    uint8_t stale_rx_probes;
    uint8_t consecutive_tx_failures;
} neato_usb_health_t;

void neato_usb_health_init(neato_usb_health_t *health);
void neato_usb_health_connected(neato_usb_health_t *health, uint32_t now_ms);
void neato_usb_health_disconnected(neato_usb_health_t *health);
void neato_usb_health_rx(neato_usb_health_t *health, uint32_t now_ms);
neato_usb_health_result_t neato_usb_health_tx(neato_usb_health_t *health,
                                              bool tx_ok,
                                              uint32_t now_ms);
const char *neato_usb_health_reason_name(neato_usb_health_result_t result);
