#include <assert.h>
#include <stdbool.h>
#include <stdint.h>

#include "../esp32-body/src/neato_usb_health.h"

static void rx_stall_requires_bounded_successful_probes(void)
{
    neato_usb_health_t health;
    neato_usb_health_init(&health);
    neato_usb_health_connected(&health, 1000);

    uint32_t first_stale = 1000 + NEATO_USB_HEALTH_RX_STALL_MS;
    assert(neato_usb_health_tx(&health, true, first_stale - 1) ==
           NEATO_USB_HEALTH_OK);
    assert(neato_usb_health_tx(&health, true, first_stale) ==
           NEATO_USB_HEALTH_OK);
    assert(neato_usb_health_tx(&health, true, first_stale + 30000) ==
           NEATO_USB_HEALTH_OK);
    assert(neato_usb_health_tx(&health, true, first_stale + 60000) ==
           NEATO_USB_HEALTH_RECOVER_RX_STALL);
}

static void rx_resets_stall_counter(void)
{
    neato_usb_health_t health;
    neato_usb_health_init(&health);
    neato_usb_health_connected(&health, 0);

    uint32_t first_stale = NEATO_USB_HEALTH_RX_STALL_MS;
    assert(neato_usb_health_tx(&health, true, first_stale) ==
           NEATO_USB_HEALTH_OK);
    assert(neato_usb_health_tx(&health, true, first_stale + 30000) ==
           NEATO_USB_HEALTH_OK);
    neato_usb_health_rx(&health, first_stale + 31000);
    assert(neato_usb_health_tx(&health, true, first_stale + 60000) ==
           NEATO_USB_HEALTH_OK);
    assert(neato_usb_health_tx(
               &health, true,
               first_stale + 31000 + NEATO_USB_HEALTH_RX_STALL_MS
           ) == NEATO_USB_HEALTH_OK);
}

static void consecutive_tx_failures_request_recovery(void)
{
    neato_usb_health_t health;
    neato_usb_health_init(&health);
    neato_usb_health_connected(&health, 0);

    assert(neato_usb_health_tx(&health, false, 1000) == NEATO_USB_HEALTH_OK);
    assert(neato_usb_health_tx(&health, false, 2000) == NEATO_USB_HEALTH_OK);
    assert(neato_usb_health_tx(&health, false, 3000) ==
           NEATO_USB_HEALTH_RECOVER_TX_FAILURES);
}

static void tx_success_clears_failure_counter(void)
{
    neato_usb_health_t health;
    neato_usb_health_init(&health);
    neato_usb_health_connected(&health, 0);

    assert(neato_usb_health_tx(&health, false, 1000) == NEATO_USB_HEALTH_OK);
    assert(neato_usb_health_tx(&health, false, 2000) == NEATO_USB_HEALTH_OK);
    assert(neato_usb_health_tx(&health, true, 3000) == NEATO_USB_HEALTH_OK);
    assert(neato_usb_health_tx(&health, false, 4000) == NEATO_USB_HEALTH_OK);
    assert(neato_usb_health_tx(&health, false, 5000) == NEATO_USB_HEALTH_OK);
}

int main(void)
{
    rx_stall_requires_bounded_successful_probes();
    rx_resets_stall_counter();
    consecutive_tx_failures_request_recovery();
    tx_success_clears_failure_counter();
    return 0;
}
