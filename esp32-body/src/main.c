/* M0 spike: ESP32-S3 as USB host talking to a Neato XV-12 over CDC-ACM.
 *
 * Plugs: Neato mini-USB  <-- cable -->  ESP32-S3 "USB" (OTG) port.
 * Power the devkit through its "COM"/UART port; logs appear there too.
 *
 * On connect it sends GetVersion, then polls GetCharger every 5 s.
 */
#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "esp_log.h"
#include "usb/usb_host.h"
#include "usb/cdc_acm_host.h"
#include "debug_uart.h"
#include "neato_audio.h"
#include "remote_soundboard.h"
#include "neato_usb.h"
#include "wifi_log.h"
#include "coli_mcu.h"

void net_log_raw(const uint8_t *data, size_t len);
void net_cmd_reply(const uint8_t *data, size_t len);
void net_ws_push(const uint8_t *data, size_t len);
void web_start(void);
void faces_start(void);

static const char *TAG = "neato";
static cdc_acm_dev_hdl_t s_dev = NULL;
static SemaphoreHandle_t s_tx_mutex;
static QueueHandle_t s_binary_events;
static volatile bool s_binary_active;

enum {
    BINARY_ENQ = 0x05,
    BINARY_ACK = 0x06,
    BINARY_NAK = 0x15,
    BINARY_TERM = 0x1a,
    BINARY_DISCONNECTED = 0xff,
};

static bool on_rx(const uint8_t *data, size_t len, void *arg)
{
    /* Neato replies are ASCII lines terminated by 0x1A (Ctrl-Z). */
    fwrite(data, 1, len, stdout);
    fflush(stdout);
    net_log_raw(data, len);
    net_cmd_reply(data, len);
    net_ws_push(data, len);
    if (s_binary_active) {
        for (size_t i = 0; i < len; i++) {
            uint8_t event = data[i];
            if (event == BINARY_ENQ || event == BINARY_ACK ||
                event == BINARY_NAK || event == BINARY_TERM)
                xQueueSend(s_binary_events, &event, 0);
        }
    }
    return true;
}

static void on_event(const cdc_acm_host_dev_event_data_t *event, void *ctx)
{
    if (event->type == CDC_ACM_HOST_DEVICE_DISCONNECTED) {
        ESP_LOGW(TAG, "Neato disconnected");
        cdc_acm_host_close(event->data.cdc_hdl);
        s_dev = NULL;
        if (s_binary_active) {
            uint8_t disconnected = BINARY_DISCONNECTED;
            xQueueSend(s_binary_events, &disconnected, 0);
        }
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

esp_err_t neato_send_checked(const char *cmd)
{
    if (xSemaphoreTake(s_tx_mutex, pdMS_TO_TICKS(2000)) != pdTRUE)
        return ESP_ERR_TIMEOUT;
    cdc_acm_dev_hdl_t dev = s_dev;
    if (!dev) {
        xSemaphoreGive(s_tx_mutex);
        return ESP_ERR_INVALID_STATE;
    }
    char buf[64];
    int n = snprintf(buf, sizeof(buf), "%s\n", cmd);
    if (n <= 0 || n >= (int)sizeof(buf)) {
        ESP_LOGE(TAG, "command too long");
        xSemaphoreGive(s_tx_mutex);
        return ESP_ERR_INVALID_ARG;
    }
    esp_err_t err = cdc_acm_host_data_tx_blocking(dev, (const uint8_t *)buf, n, 1000);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "tx failed: %s", esp_err_to_name(err));
    }
    xSemaphoreGive(s_tx_mutex);
    return err;
}

void neato_send(const char *cmd)
{
    (void)neato_send_checked(cmd);
}

static esp_err_t wait_binary_event(uint8_t expected, uint32_t timeout_ms)
{
    uint8_t event;
    TickType_t timeout = pdMS_TO_TICKS(timeout_ms);
    if (xQueueReceive(s_binary_events, &event, timeout) != pdTRUE)
        return ESP_ERR_TIMEOUT;
    if (event == BINARY_DISCONNECTED) return ESP_ERR_INVALID_STATE;
    return event == expected ? ESP_OK : ESP_ERR_INVALID_RESPONSE;
}

static uint32_t s_stream_checksum;
static bool s_stream_open;

esp_err_t neato_binary_begin(const char *cmd, size_t payload_len,
                             uint32_t timeout_ms)
{
    if (!cmd || payload_len == 0 || payload_len > NEATO_MAX_BINARY_BYTES)
        return ESP_ERR_INVALID_ARG;
    if (xSemaphoreTake(s_tx_mutex, pdMS_TO_TICKS(2000)) != pdTRUE)
        return ESP_ERR_TIMEOUT;

    esp_err_t result = ESP_ERR_INVALID_STATE;
    cdc_acm_dev_hdl_t dev = s_dev;
    if (!dev) goto fail;

    char header[80];
    int header_len = snprintf(header, sizeof(header), "%s Size %u\r", cmd,
                              (unsigned)(payload_len + 4));
    if (header_len <= 0 || header_len >= (int)sizeof(header)) {
        result = ESP_ERR_INVALID_ARG;
        goto fail;
    }

    xQueueReset(s_binary_events);
    s_binary_active = true;
    result = cdc_acm_host_data_tx_blocking(
        dev, (const uint8_t *)header, header_len, timeout_ms
    );
    if (result != ESP_OK) goto fail;
    result = wait_binary_event(BINARY_ENQ, timeout_ms);
    if (result != ESP_OK) goto fail;

    s_stream_checksum = 0;
    s_stream_open = true;
    return ESP_OK;

fail:
    s_binary_active = false;
    xQueueReset(s_binary_events);
    xSemaphoreGive(s_tx_mutex);
    return result;
}

esp_err_t neato_binary_write(const uint8_t *data, size_t len,
                             uint32_t timeout_ms)
{
    if (!s_stream_open) return ESP_ERR_INVALID_STATE;
    cdc_acm_dev_hdl_t dev = s_dev;
    if (!dev) return ESP_ERR_INVALID_STATE;
    for (size_t offset = 0; offset < len; offset += 512) {
        size_t chunk_len = len - offset;
        if (chunk_len > 512) chunk_len = 512;
        for (size_t i = 0; i < chunk_len; i++)
            s_stream_checksum += data[offset + i];
        esp_err_t result = cdc_acm_host_data_tx_blocking(
            dev, data + offset, chunk_len, timeout_ms
        );
        if (result != ESP_OK) return result;
    }
    return ESP_OK;
}

esp_err_t neato_binary_end(bool poison_checksum, uint32_t timeout_ms)
{
    if (!s_stream_open) return ESP_ERR_INVALID_STATE;
    esp_err_t result = ESP_ERR_INVALID_STATE;
    cdc_acm_dev_hdl_t dev = s_dev;
    uint32_t checksum = s_stream_checksum + (poison_checksum ? 1 : 0);
    if (dev) {
        uint8_t checksum_le[4] = {
            checksum & 0xff, (checksum >> 8) & 0xff,
            (checksum >> 16) & 0xff, (checksum >> 24) & 0xff,
        };
        result = cdc_acm_host_data_tx_blocking(
            dev, checksum_le, sizeof(checksum_le), timeout_ms
        );
        if (result == ESP_OK)
            result = wait_binary_event(BINARY_ACK, timeout_ms);
        if (result == ESP_OK && poison_checksum)
            result = ESP_ERR_INVALID_CRC; /* receiver ACKed a poisoned sum?! */
    }
    s_stream_open = false;
    s_binary_active = false;
    xQueueReset(s_binary_events);
    xSemaphoreGive(s_tx_mutex);
    return result;
}

esp_err_t neato_send_binary(const char *cmd, const uint8_t *payload,
                            size_t payload_len, uint32_t timeout_ms)
{
    if (!payload) return ESP_ERR_INVALID_ARG;
    esp_err_t result = neato_binary_begin(cmd, payload_len, timeout_ms);
    if (result != ESP_OK) return result;
    result = neato_binary_write(payload, payload_len, timeout_ms);
    if (result != ESP_OK) {
        neato_binary_end(true, timeout_ms); /* poison: abort cleanly */
        return result;
    }
    return neato_binary_end(false, timeout_ms);
}

void app_main(void)
{
    s_tx_mutex = xSemaphoreCreateMutex();
    s_binary_events = xQueueCreate(8, sizeof(uint8_t));
    ESP_ERROR_CHECK(s_tx_mutex && s_binary_events ? ESP_OK : ESP_ERR_NO_MEM);
    ESP_ERROR_CHECK(neato_audio_init());
    ESP_ERROR_CHECK(remote_soundboard_init());
    wifi_log_start();
    web_start();
    debug_uart_start();
    faces_start();

    const usb_host_config_t host_config = {
        .intr_flags = ESP_INTR_FLAG_LEVEL1,
    };
    ESP_ERROR_CHECK(usb_host_install(&host_config));
    xTaskCreate(usb_lib_task, "usb_lib", 4096, NULL, 10, NULL);

    ESP_ERROR_CHECK(cdc_acm_host_install(NULL));
    /* MSC is a peer USB class client. coli_mcu never installs a second host. */
    ESP_ERROR_CHECK(coli_mcu_start());

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
