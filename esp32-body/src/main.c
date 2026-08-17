/* M0 spike: ESP32-S3 as USB host talking to a Neato XV-12 over CDC-ACM.
 *
 * Plugs: Neato mini-USB  <-- cable -->  ESP32-S3 "USB" (OTG) port.
 * Power the devkit through its "COM"/UART port; logs appear there too.
 *
 * On connect it sends GetVersion, then polls GetCharger every 10 s.
 * The quiet poll acts as a keepalive so the Neato does not sit idle long
 * enough to drop the USB session. All transport lives in neato_usb.c; this
 * file is startup wiring plus the connect demo and heartbeat.
 */
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"
#include "debug_uart.h"
#include "faces.h"
#include "neato_audio.h"
#include "neato_protocol.h"
#include "neato_usb.h"
#include "remote_soundboard.h"
#include "web.h"
#include "wifi_log.h"
#include "coli_mcu.h"

static void on_neato_connect(void)
{
    /* Audible proof of life: no serial monitor needed. */
    neato_test_mode(true);
    vTaskDelay(pdMS_TO_TICKS(300));
    neato_led("BacklightOn");
    vTaskDelay(pdMS_TO_TICKS(200));
    neato_play_slot(1);
    vTaskDelay(pdMS_TO_TICKS(300));
    neato_get_version();
}

void app_main(void)
{
    ESP_ERROR_CHECK(neato_audio_init());
    ESP_ERROR_CHECK(remote_soundboard_init());
    wifi_log_start();
    web_start();
    debug_uart_start();
    faces_start();

    ESP_ERROR_CHECK(neato_usb_install());
    /* MSC is a peer USB class client. coli_mcu never installs a second host. */
    ESP_ERROR_CHECK(coli_mcu_start());
    neato_usb_start(on_neato_connect);

    while (1) {
        vTaskDelay(pdMS_TO_TICKS(10000));
        if (neato_usb_connected()) {
            esp_err_t err = neato_get_charger();
            if (err != ESP_OK) {
                ESP_LOGW("main", "USB keepalive failed: %s", esp_err_to_name(err));
            }
        }
    }
}
