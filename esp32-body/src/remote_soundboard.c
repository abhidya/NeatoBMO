/* Fetch a precompiled Neato .bin from HTTP(S), verify, install, and play.
 *
 * POST /soundboard/play JSON:
 *   {"url":"https://.../page.bin","sha256":"...","key":"hello",
 *    "slots":[7],"slot_seconds":[2.017324]}
 * GET /soundboard/status returns the background job state.
 *
 * Downloads are fully staged in PSRAM before Upload sound begins.  A failed
 * or truncated network request therefore never strands the Neato bootloader.
 */
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "cJSON.h"
#include "esp_crt_bundle.h"
#include "esp_heap_caps.h"
#include "esp_http_client.h"
#include "esp_http_server.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "mbedtls/sha256.h"
#include "neato_audio.h"
#include "neato_protocol.h"
#include "neato_usb.h"
#include "remote_soundboard.h"

#define MAX_URL_BYTES 768
#define MAX_KEY_BYTES 96
#define MAX_SLOTS 10

typedef struct {
    char url[MAX_URL_BYTES];
    char sha256[65];
    char key[MAX_KEY_BYTES];
    int slots[MAX_SLOTS];
    uint32_t waits_ms[MAX_SLOTS];
    size_t slot_count;
} sound_job_t;

typedef struct {
    char state[24];
    char key[MAX_KEY_BYTES];
    char module_sha256[65];
    char error[160];
    size_t slot_index;
    size_t slot_count;
    bool reused_module;
} sound_status_t;

static SemaphoreHandle_t s_lock;
static bool s_active;
static sound_status_t s_status = {.state = "idle"};

static bool ensure_lock(void)
{
    if (!s_lock) remote_soundboard_init();
    return s_lock != NULL;
}

esp_err_t remote_soundboard_init(void)
{
    if (s_lock) return ESP_OK;
    s_lock = xSemaphoreCreateMutex();
    return s_lock ? ESP_OK : ESP_ERR_NO_MEM;
}

static void set_status(const char *state, const char *error)
{
    if (!ensure_lock()) return;
    if (xSemaphoreTake(s_lock, pdMS_TO_TICKS(1000)) != pdTRUE) return;
    snprintf(s_status.state, sizeof(s_status.state), "%s", state);
    snprintf(s_status.error, sizeof(s_status.error), "%s", error ? error : "");
    xSemaphoreGive(s_lock);
}

static bool valid_sha256(const char *value)
{
    if (!value || strlen(value) != 64) return false;
    for (size_t i = 0; i < 64; i++) {
        char c = value[i];
        if (!((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f'))) return false;
    }
    return true;
}

static bool valid_key(const char *value)
{
    if (!value || !value[0] || strlen(value) >= MAX_KEY_BYTES) return false;
    for (const char *at = value; *at; at++) {
        char c = *at;
        if (!((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') ||
              (c >= '0' && c <= '9') || c == '-' || c == '_' || c == '.'))
            return false;
    }
    return true;
}

static esp_err_t fetch_module(const char *url, uint8_t **output)
{
    if (strncmp(url, "http://", 7) && strncmp(url, "https://", 8))
        return ESP_ERR_INVALID_ARG;

    esp_http_client_config_t config = {
        .url = url,
        .timeout_ms = 30000,
        .buffer_size = 4096,
        .crt_bundle_attach = esp_crt_bundle_attach,
        .max_redirection_count = 5,
    };
    esp_http_client_handle_t client = esp_http_client_init(&config);
    if (!client) return ESP_ERR_NO_MEM;
    esp_err_t result = esp_http_client_open(client, 0);
    if (result != ESP_OK) goto done;
    int64_t declared = esp_http_client_fetch_headers(client);
    int status = esp_http_client_get_status_code(client);
    if (status < 200 || status >= 300) {
        result = ESP_ERR_HTTP_FETCH_HEADER;
        goto done;
    }
    if (declared >= 0 && declared != NEATO_SOUND_BANK_BYTES) {
        result = ESP_ERR_INVALID_SIZE;
        goto done;
    }

    *output = heap_caps_malloc(NEATO_SOUND_BANK_BYTES,
                               MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (!*output) *output = heap_caps_malloc(NEATO_SOUND_BANK_BYTES,
                                             MALLOC_CAP_8BIT);
    if (!*output) {
        result = ESP_ERR_NO_MEM;
        goto done;
    }
    size_t received = 0;
    while (received < NEATO_SOUND_BANK_BYTES) {
        int got = esp_http_client_read(client, (char *)*output + received,
                                       NEATO_SOUND_BANK_BYTES - received);
        if (got < 0) {
            result = ESP_FAIL;
            goto failed_payload;
        }
        if (got == 0) break;
        received += (size_t)got;
    }
    if (received != NEATO_SOUND_BANK_BYTES) {
        result = ESP_ERR_INVALID_SIZE;
        goto failed_payload;
    }
    /* Prove there is no trailing data when the server used chunked encoding. */
    char extra;
    if (declared < 0 && esp_http_client_read(client, &extra, 1) > 0) {
        result = ESP_ERR_INVALID_SIZE;
        goto failed_payload;
    }
    result = ESP_OK;
    goto done;

failed_payload:
    free(*output);
    *output = NULL;
done:
    esp_http_client_close(client);
    esp_http_client_cleanup(client);
    return result;
}

static void sound_task(void *arg)
{
    sound_job_t *job = arg;
    uint8_t *bank = NULL;
    bool operation_locked = false;
    set_status("downloading", NULL);
    esp_err_t err = fetch_module(job->url, &bank);
    if (err != ESP_OK) {
        char message[160];
        snprintf(message, sizeof(message), "module download failed: %s",
                 esp_err_to_name(err));
        set_status("error", message);
        goto done;
    }

    err = neato_sound_operation_begin(1000);
    if (err != ESP_OK) {
        set_status("error", "another sound operation is active");
        goto done;
    }
    operation_locked = true;
    bool reused = neato_soundbank_matches(job->sha256);
    if (!reused) {
        set_status("installing", NULL);
        err = neato_install_soundbank(bank, NEATO_SOUND_BANK_BYTES,
                                      job->sha256);
        if (err != ESP_OK) {
            char message[160];
            snprintf(message, sizeof(message), "module install failed: %s",
                     esp_err_to_name(err));
            set_status("error", message);
            goto done;
        }
    }
    free(bank);
    bank = NULL;

    ensure_lock();
    if (xSemaphoreTake(s_lock, pdMS_TO_TICKS(1000)) == pdTRUE) {
        s_status.reused_module = reused;
        snprintf(s_status.module_sha256, sizeof(s_status.module_sha256), "%s",
                 job->sha256);
        snprintf(s_status.state, sizeof(s_status.state), "playing");
        xSemaphoreGive(s_lock);
    }
    for (size_t i = 0; i < job->slot_count; i++) {
        if (xSemaphoreTake(s_lock, pdMS_TO_TICKS(1000)) == pdTRUE) {
            s_status.slot_index = i;
            xSemaphoreGive(s_lock);
        }
        err = neato_play_slot(job->slots[i]);
        if (err != ESP_OK) {
            char message[160];
            snprintf(message, sizeof(message), "PlaySound failed: %s",
                     esp_err_to_name(err));
            set_status("error", message);
            goto done;
        }
        vTaskDelay(pdMS_TO_TICKS(job->waits_ms[i]));
    }
    set_status("ready", NULL);

done:
    if (operation_locked) neato_sound_operation_end();
    free(bank);
    free(job);
    if (ensure_lock() && xSemaphoreTake(s_lock, pdMS_TO_TICKS(1000)) == pdTRUE) {
        s_active = false;
        xSemaphoreGive(s_lock);
    }
    vTaskDelete(NULL);
}

static esp_err_t json_error(httpd_req_t *req, const char *status,
                            const char *message)
{
    char body[224];
    snprintf(body, sizeof(body), "{\"error\":\"%s\"}", message);
    httpd_resp_set_status(req, status);
    httpd_resp_set_type(req, "application/json");
    return httpd_resp_sendstr(req, body);
}

esp_err_t soundboard_play_post(httpd_req_t *req)
{
    if (req->content_len <= 0 || req->content_len > 2048)
        return json_error(req, "400 Bad Request", "invalid JSON body size");
    char *body = malloc((size_t)req->content_len + 1);
    if (!body) return json_error(req, "503 Service Unavailable", "out of memory");
    int received = 0;
    while (received < req->content_len) {
        int got = httpd_req_recv(req, body + received, req->content_len - received);
        if (got == HTTPD_SOCK_ERR_TIMEOUT) continue;
        if (got <= 0) {
            free(body);
            return json_error(req, "400 Bad Request", "incomplete JSON body");
        }
        received += got;
    }
    body[received] = 0;
    cJSON *json = cJSON_Parse(body);
    free(body);
    if (!json) return json_error(req, "400 Bad Request", "malformed JSON");

    cJSON *url = cJSON_GetObjectItemCaseSensitive(json, "url");
    cJSON *sha = cJSON_GetObjectItemCaseSensitive(json, "sha256");
    cJSON *key = cJSON_GetObjectItemCaseSensitive(json, "key");
    cJSON *slots = cJSON_GetObjectItemCaseSensitive(json, "slots");
    cJSON *waits = cJSON_GetObjectItemCaseSensitive(json, "slot_seconds");
    size_t count = cJSON_IsArray(slots) ? (size_t)cJSON_GetArraySize(slots) : 0;
    bool valid = cJSON_IsString(url) && cJSON_IsString(sha) &&
                 cJSON_IsString(key) && cJSON_IsArray(waits) && count > 0 &&
                 count <= MAX_SLOTS && (size_t)cJSON_GetArraySize(waits) == count &&
                 strlen(url->valuestring) < MAX_URL_BYTES &&
                 valid_key(key->valuestring) &&
                 valid_sha256(sha->valuestring);
    if (!valid) {
        cJSON_Delete(json);
        return json_error(req, "400 Bad Request", "invalid module request");
    }
    sound_job_t *job = calloc(1, sizeof(*job));
    if (!job) {
        cJSON_Delete(json);
        return json_error(req, "503 Service Unavailable", "out of memory");
    }
    snprintf(job->url, sizeof(job->url), "%s", url->valuestring);
    snprintf(job->sha256, sizeof(job->sha256), "%s", sha->valuestring);
    snprintf(job->key, sizeof(job->key), "%s", key->valuestring);
    job->slot_count = count;
    for (size_t i = 0; i < count; i++) {
        cJSON *slot = cJSON_GetArrayItem(slots, (int)i);
        cJSON *wait = cJSON_GetArrayItem(waits, (int)i);
        if (!cJSON_IsNumber(slot) || !cJSON_IsNumber(wait) ||
            slot->valueint < 0 || slot->valueint > 20 ||
            wait->valuedouble <= 0 || wait->valuedouble > 20) {
            free(job);
            cJSON_Delete(json);
            return json_error(req, "400 Bad Request", "invalid slot sequence");
        }
        job->slots[i] = slot->valueint;
        job->waits_ms[i] = (uint32_t)(wait->valuedouble * 1000.0 + 0.5);
    }
    cJSON_Delete(json);

    if (!ensure_lock()) {
        free(job);
        return json_error(req, "503 Service Unavailable", "out of memory");
    }
    if (xSemaphoreTake(s_lock, pdMS_TO_TICKS(1000)) != pdTRUE) {
        free(job);
        return json_error(req, "503 Service Unavailable", "soundboard is busy");
    }
    if (s_active) {
        xSemaphoreGive(s_lock);
        free(job);
        return json_error(req, "409 Conflict", "another sound is active");
    }
    s_active = true;
    xSemaphoreGive(s_lock);

    if (xSemaphoreTake(s_lock, pdMS_TO_TICKS(1000)) == pdTRUE) {
        memset(&s_status, 0, sizeof(s_status));
        snprintf(s_status.state, sizeof(s_status.state), "queued");
        snprintf(s_status.key, sizeof(s_status.key), "%s", job->key);
        s_status.slot_count = job->slot_count;
        xSemaphoreGive(s_lock);
    }
    TaskHandle_t task = NULL;
    if (xTaskCreate(sound_task, "soundboard", 8192, job, 5, &task) != pdPASS) {
        if (xSemaphoreTake(s_lock, pdMS_TO_TICKS(1000)) == pdTRUE) {
            s_active = false;
            xSemaphoreGive(s_lock);
        }
        free(job);
        return json_error(req, "503 Service Unavailable", "could not start job");
    }
    httpd_resp_set_status(req, "202 Accepted");
    httpd_resp_set_type(req, "application/json");
    return httpd_resp_sendstr(req, "{\"ok\":true,\"state\":\"queued\"}");
}

esp_err_t soundboard_status_get(httpd_req_t *req)
{
    if (!ensure_lock())
        return httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "oom");
    sound_status_t status;
    memset(&status, 0, sizeof(status));
    if (xSemaphoreTake(s_lock, pdMS_TO_TICKS(1000)) == pdTRUE) {
        status = s_status;
        xSemaphoreGive(s_lock);
    }
    char *body = malloc(640);
    if (!body) return httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "oom");
    int length = snprintf(body, 640,
        "{\"state\":\"%s\",\"key\":\"%s\",\"module_sha256\":\"%s\","
        "\"error\":\"%s\",\"slot_index\":%u,\"slot_count\":%u,"
        "\"reused_module\":%s}",
        status.state, status.key, status.module_sha256, status.error,
        (unsigned)status.slot_index, (unsigned)status.slot_count,
        status.reused_module ? "true" : "false");
    httpd_resp_set_type(req, "application/json");
    esp_err_t result = httpd_resp_send(req, body, length);
    free(body);
    return result;
}
