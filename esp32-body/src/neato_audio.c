/* Receive a bounded WAV over HTTP and relay it through Neato USB framing.
 *
 * The complete body is staged in PSRAM before the robot transaction starts.
 * That prevents a broken HTTP upload from leaving the robot's binary receiver
 * waiting for bytes indefinitely.  No audio is played by the ESP32 itself.
 */
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include "esp_heap_caps.h"
#include "esp_http_server.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "mbedtls/sha256.h"
#include "neato_audio.h"
#include "neato_usb.h"

static const char *TAG = "neato_audio";
static SemaphoreHandle_t s_sound_lock;
static char s_installed_soundbank_sha256[65];

static bool ensure_sound_lock(void)
{
    if (!s_sound_lock) neato_audio_init();
    return s_sound_lock != NULL;
}

esp_err_t neato_audio_init(void)
{
    if (s_sound_lock) return ESP_OK;
    s_sound_lock = xSemaphoreCreateRecursiveMutex();
    return s_sound_lock ? ESP_OK : ESP_ERR_NO_MEM;
}

esp_err_t neato_sound_operation_begin(uint32_t timeout_ms)
{
    if (!ensure_sound_lock()) return ESP_ERR_NO_MEM;
    return xSemaphoreTakeRecursive(s_sound_lock, pdMS_TO_TICKS(timeout_ms)) == pdTRUE
               ? ESP_OK : ESP_ERR_TIMEOUT;
}

void neato_sound_operation_end(void)
{
    if (s_sound_lock) xSemaphoreGiveRecursive(s_sound_lock);
}

bool neato_soundbank_matches(const char *sha256)
{
    if (!sha256 || neato_sound_operation_begin(1000) != ESP_OK) return false;
    bool matches = !strcmp(s_installed_soundbank_sha256, sha256);
    neato_sound_operation_end();
    return matches;
}

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

esp_err_t neato_install_soundbank(const uint8_t *bank, size_t len,
                                  const char *expected_sha256)
{
    if (!bank || !expected_sha256 || len != NEATO_SOUND_BANK_BYTES)
        return ESP_ERR_INVALID_SIZE;
    if (bank[0] != 'K' || bank[1] != 'T') return ESP_ERR_INVALID_ARG;

    uint8_t digest[32];
    mbedtls_sha256(bank, len, digest, 0);
    char actual_sha[65];
    for (int i = 0; i < 32; i++)
        snprintf(actual_sha + i * 2, 3, "%02x", digest[i]);
    if (strcasecmp(actual_sha, expected_sha256) != 0) {
        ESP_LOGW(TAG, "soundbank sha mismatch: got %s", actual_sha);
        return ESP_ERR_INVALID_CRC;
    }

    esp_err_t lock_err = neato_sound_operation_begin(1000);
    if (lock_err != ESP_OK) return lock_err;

    ESP_LOGI(TAG, "soundbank: flashing %u bytes sha256=%s", (unsigned)len,
             actual_sha);
    /* A timeout or rejected transfer may still have altered flash. Never let
     * the module-reuse cache survive an uncertain write. */
    s_installed_soundbank_sha256[0] = 0;
    esp_err_t result = neato_send_binary("Upload sound", bank, len, 60000);
    if (result == ESP_OK)
        snprintf(s_installed_soundbank_sha256,
                 sizeof(s_installed_soundbank_sha256), "%s", actual_sha);
    neato_sound_operation_end();
    return result;
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

    esp_err_t err = neato_sound_operation_begin(1000);
    if (err == ESP_OK) {
        err = neato_send_binary("PlaySound File", wav, len, 15000);
        neato_sound_operation_end();
    }
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

/* Relay one complete sound-bank image to the robot ("Upload sound").
 *
 * The host validates the bank byte-exactly before posting; the firmware
 * re-proves integrity end to end with the mandatory X-Bank-SHA256 header
 * over the staged bytes, plus the exact image size and the KT directory
 * marker.  Only then does the USB binary transaction start, so a broken
 * upload can never leave the robot's receiver mid-transfer.
 */
esp_err_t soundbank_post(httpd_req_t *req)
{
    if ((size_t)req->content_len != NEATO_SOUND_BANK_BYTES)
        return error_reply(req, "400 Bad Request",
                           "sound bank must be exactly 770048 bytes\n");

    char expected_sha[65];
    if (httpd_req_get_hdr_value_str(req, "X-Bank-SHA256", expected_sha,
                                    sizeof(expected_sha)) != ESP_OK)
        return error_reply(req, "400 Bad Request",
                           "X-Bank-SHA256 header is required\n");

    size_t len = (size_t)req->content_len;
    uint8_t *bank = heap_caps_malloc(len, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (!bank) bank = heap_caps_malloc(len, MALLOC_CAP_8BIT);
    if (!bank)
        return error_reply(req, "503 Service Unavailable",
                           "not enough staging memory\n");

    size_t received = 0;
    while (received < len) {
        int got = httpd_req_recv(req, (char *)bank + received, len - received);
        if (got == HTTPD_SOCK_ERR_TIMEOUT) continue;
        if (got <= 0) {
            free(bank);
            return error_reply(req, "400 Bad Request", "incomplete bank upload\n");
        }
        received += (size_t)got;
    }

    esp_err_t err = neato_install_soundbank(bank, len, expected_sha);
    free(bank);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "Upload sound failed: %s", esp_err_to_name(err));
        if (err == ESP_ERR_INVALID_ARG)
            return error_reply(req, "415 Unsupported Media Type",
                               "image lacks the KT directory marker\n");
        if (err == ESP_ERR_INVALID_CRC)
            return error_reply(req, "422 Unprocessable Entity",
                               "staged bytes do not match X-Bank-SHA256\n");
        if (err == ESP_ERR_INVALID_STATE)
            return error_reply(req, "503 Service Unavailable",
                               "Neato USB is not connected\n");
        if (err == ESP_ERR_TIMEOUT)
            return error_reply(req, "504 Gateway Timeout",
                               "Neato binary transfer timed out\n");
        return error_reply(req, "409 Conflict",
                           "Neato did not ACK the sound-bank write\n");
    }

    httpd_resp_set_type(req, "text/plain");
    return httpd_resp_sendstr(req, "OK\n");
}
