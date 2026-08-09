/* M0 spike: ESP32-S3 as USB host talking to a Neato XV-12 over CDC-ACM.
 *
 * Plugs: Neato mini-USB  <-- cable -->  ESP32-S3 "USB" (OTG) port.
 * Power the devkit through its "COM"/UART port; logs appear there too.
 *
 * On connect it sends GetVersion, then polls GetCharger every 5 s.
 */
#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"
#include "usb/usb_host.h"
#include "usb/cdc_acm_host.h"
#include "wifi_log.h"

void net_log_raw(const uint8_t *data, size_t len);
void net_cmd_reply(const uint8_t *data, size_t len);
void net_ws_push(const uint8_t *data, size_t len);
void web_start(void);

static const char *TAG = "neato";
static cdc_acm_dev_hdl_t s_dev = NULL;
static SemaphoreHandle_t s_connected;

static bool on_rx(const uint8_t *data, size_t len, void *arg)
{
    /* Neato replies are ASCII lines terminated by 0x1A (Ctrl-Z). */
    fwrite(data, 1, len, stdout);
    fflush(stdout);
    net_log_raw(data, len);
    net_cmd_reply(data, len);
    net_ws_push(data, len);
    return true;
}

static void on_event(const cdc_acm_host_dev_event_data_t *event, void *ctx)
{
    if (event->type == CDC_ACM_HOST_DEVICE_DISCONNECTED) {
        ESP_LOGW(TAG, "Neato disconnected");
        cdc_acm_host_close(event->data.cdc_hdl);
        s_dev = NULL;
    }
}

static void usb_lib_task(void *arg)
{
    while (1) {
        uint32_t events;
        usb_host_lib_handle_events(portMAX_DELAY, &events);
        if (events & USB_HOST_LIB_EVENT_FLAGS_NO_CLIENTS) {
            usb_host_device_free_all();
        }
    }
}

void neato_send(const char *cmd)
{
    if (!s_dev) return;
    char buf[64];
    int n = snprintf(buf, sizeof(buf), "%s\n", cmd);
    esp_err_t err = cdc_acm_host_data_tx_blocking(s_dev, (const uint8_t *)buf, n, 1000);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "tx failed: %s", esp_err_to_name(err));
    }
}

void app_main(void)
{
    s_connected = xSemaphoreCreateBinary();
    wifi_log_start();
    web_start();

    const usb_host_config_t host_config = {
        .intr_flags = ESP_INTR_FLAG_LEVEL1,
    };
    ESP_ERROR_CHECK(usb_host_install(&host_config));
    xTaskCreate(usb_lib_task, "usb_lib", 4096, NULL, 10, NULL);

    ESP_ERROR_CHECK(cdc_acm_host_install(NULL));

    const cdc_acm_host_device_config_t dev_config = {
        .connection_timeout_ms = 0,      /* wait forever for the robot */
        .out_buffer_size = 512,
        .in_buffer_size = 2048,
        .event_cb = on_event,
        .data_cb = on_rx,
        .user_arg = NULL,
    };

    while (1) {
        ESP_LOGI(TAG, "Waiting for Neato on USB...");
        /* VID/PID any: match the first CDC-ACM device that shows up. */
        esp_err_t err = cdc_acm_host_open(CDC_HOST_ANY_VID, CDC_HOST_ANY_PID,
                                          0, &dev_config, &s_dev);
        if (err != ESP_OK) {
            vTaskDelay(pdMS_TO_TICKS(1000));
            continue;
        }
        ESP_LOGI(TAG, "Neato connected!");
        cdc_acm_line_coding_t coding = {
            .dwDTERate = 115200, .bDataBits = 8, .bParityType = 0, .bCharFormat = 0,
        };
        cdc_acm_host_line_coding_set(s_dev, &coding);

        vTaskDelay(pdMS_TO_TICKS(500));
        /* Audible proof of life: no serial monitor needed. */
        neato_send("TestMode On");
        vTaskDelay(pdMS_TO_TICKS(300));
        neato_send("SetLED BacklightOn");
        vTaskDelay(pdMS_TO_TICKS(200));
        neato_send("PlaySound 1");
        vTaskDelay(pdMS_TO_TICKS(300));
        neato_send("GetVersion");

        while (s_dev) {
            vTaskDelay(pdMS_TO_TICKS(30000));
            neato_send("PlaySound 2"); /* heartbeat chirp every 30 s */
            neato_send("GetCharger");
        }
    }
}
