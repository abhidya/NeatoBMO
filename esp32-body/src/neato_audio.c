/* Receive a bounded WAV over HTTP and relay it through Neato USB framing.
 *
 * The complete body is staged in PSRAM before the robot transaction starts.
 * That prevents a broken HTTP upload from leaving the robot's binary receiver
 * waiting for bytes indefinitely.  No audio is played by the ESP32 itself.
 */
#include <stdbool.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include "esp_heap_caps.h"
#include "esp_http_server.h"
#include "esp_log.h"
#include "neato_audio.h"
#include "neato_usb.h"

static const char *TAG = "neato_audio";

static uint16_t read_le16(const uint8_t *p)
{
    return (uint16_t)p[0] | ((uint16_t)p[1] << 8);
}

static uint32_t read_le32(const uint8_t *p)
{
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) |
           ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

static bool valid_neato_wav(const uint8_t *wav, size_t len)
{
    if (len < 44 || memcmp(wav, "RIFF", 4) || memcmp(wav + 8, "WAVE", 4))
        return false;

    bool format_ok = false;
    bool has_audio = false;
    size_t offset = 12;
    while (offset + 8 <= len) {
        const uint8_t *chunk = wav + offset;
        uint32_t chunk_len = read_le32(chunk + 4);
        size_t body = offset + 8;
        if (chunk_len > len - body) return false;
        if (!memcmp(chunk, "fmt ", 4) && chunk_len >= 16) {
            const uint8_t *fmt = wav + body;
            format_ok = read_le16(fmt) == 1 &&
                        read_le16(fmt + 2) == 1 &&
                        read_le32(fmt + 4) == 22050 &&
                        read_le16(fmt + 14) == 16;
        } else if (!memcmp(chunk, "data", 4) && chunk_len > 0) {
            has_audio = true;
        }
        size_t padded = (size_t)chunk_len + (chunk_len & 1U);
        if (padded > len - body) break;
        offset = body + padded;
    }
    return format_ok && has_audio;
}

static esp_err_t error_reply(httpd_req_t *req, const char *status,
                             const char *message)
{
    httpd_resp_set_status(req, status);
    httpd_resp_set_type(req, "text/plain");
    return httpd_resp_sendstr(req, message);
}

esp_err_t speak_post(httpd_req_t *req)
{
    if (req->content_len < 44)
        return error_reply(req, "400 Bad Request", "a PCM WAV body is required\n");
    if ((size_t)req->content_len > NEATO_MAX_WAV_BYTES)
        return error_reply(req, "413 Payload Too Large", "WAV exceeds 512 KiB\n");

    size_t len = (size_t)req->content_len;
    uint8_t *wav = heap_caps_malloc(len, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (!wav) wav = heap_caps_malloc(len, MALLOC_CAP_8BIT);
    if (!wav)
        return error_reply(req, "503 Service Unavailable", "not enough staging memory\n");

    size_t received = 0;
    while (received < len) {
        int got = httpd_req_recv(req, (char *)wav + received, len - received);
        if (got == HTTPD_SOCK_ERR_TIMEOUT) continue;
        if (got <= 0) {
            free(wav);
            return error_reply(req, "400 Bad Request", "incomplete WAV upload\n");
        }
        received += (size_t)got;
    }

    if (!valid_neato_wav(wav, len)) {
        free(wav);
        return error_reply(
            req, "415 Unsupported Media Type",
            "WAV must be mono signed 16-bit PCM at 22050 Hz\n"
        );
    }

    esp_err_t err = neato_send_binary("PlaySound File", wav, len, 15000);
    free(wav);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "PlaySound File failed: %s", esp_err_to_name(err));
        if (err == ESP_ERR_INVALID_STATE)
            return error_reply(req, "503 Service Unavailable", "Neato USB is not connected\n");
        if (err == ESP_ERR_TIMEOUT)
            return error_reply(req, "504 Gateway Timeout", "Neato binary transfer timed out\n");
        return error_reply(
            req, "409 Conflict",
            "Neato firmware did not accept PlaySound File; install the runtime-sound patch\n"
        );
    }

    httpd_resp_set_type(req, "text/plain");
    return httpd_resp_sendstr(req, "OK\n");
}
