#include "neato_usb_health.h"

static bool elapsed_at_least(uint32_t now_ms, uint32_t then_ms, uint32_t limit_ms)
{
    return (uint32_t)(now_ms - then_ms) >= limit_ms;
}

void neato_usb_health_init(neato_usb_health_t *health)
{
    if (!health) return;
    *health = (neato_usb_health_t){0};
}

void neato_usb_health_connected(neato_usb_health_t *health, uint32_t now_ms)
{
    if (!health) return;
    health->connected = true;
    health->connected_ms = now_ms;
    health->last_rx_ms = now_ms;
    health->stale_rx_probes = 0;
    health->consecutive_tx_failures = 0;
}

void neato_usb_health_disconnected(neato_usb_health_t *health)
{
    if (!health) return;
    health->connected = false;
    health->stale_rx_probes = 0;
    health->consecutive_tx_failures = 0;
}

void neato_usb_health_rx(neato_usb_health_t *health, uint32_t now_ms)
{
    if (!health || !health->connected) return;
    health->last_rx_ms = now_ms;
    health->stale_rx_probes = 0;
    health->consecutive_tx_failures = 0;
}

neato_usb_health_result_t neato_usb_health_tx(neato_usb_health_t *health,
                                              bool tx_ok,
                                              uint32_t now_ms)
{
    if (!health || !health->connected) return NEATO_USB_HEALTH_OK;

    if (!tx_ok) {
        if (health->consecutive_tx_failures < UINT8_MAX)
            health->consecutive_tx_failures++;
        if (health->consecutive_tx_failures >= NEATO_USB_HEALTH_TX_FAILURES)
            return NEATO_USB_HEALTH_RECOVER_TX_FAILURES;
        return NEATO_USB_HEALTH_OK;
    }

    health->consecutive_tx_failures = 0;
    if (!elapsed_at_least(now_ms, health->last_rx_ms,
                          NEATO_USB_HEALTH_RX_STALL_MS)) {
        health->stale_rx_probes = 0;
        return NEATO_USB_HEALTH_OK;
    }

    if (health->stale_rx_probes < UINT8_MAX)
        health->stale_rx_probes++;
    if (health->stale_rx_probes >= NEATO_USB_HEALTH_STALL_PROBES)
        return NEATO_USB_HEALTH_RECOVER_RX_STALL;

    return NEATO_USB_HEALTH_OK;
}

const char *neato_usb_health_reason_name(neato_usb_health_result_t result)
{
    switch (result) {
    case NEATO_USB_HEALTH_RECOVER_RX_STALL:
        return "rx_stall";
    case NEATO_USB_HEALTH_RECOVER_TX_FAILURES:
        return "tx_failures";
    case NEATO_USB_HEALTH_OK:
    default:
        return "ok";
    }
}
