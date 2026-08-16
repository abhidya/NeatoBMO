/* Embedded web dashboard + WebSocket bridge + OTA updates + WiFi setup.
 *
 *   GET  /            -> dashboard UI (index.html embedded in flash)
 *   WS   /ws          -> bidirectional raw bridge to the Neato serial
 *   POST /ota         -> flash a new firmware image over WiFi
 *   GET  /wifi        -> WiFi settings / captive-portal setup page
 *   GET  /wifi/scan   -> JSON list of nearby SSIDs
 *   GET  /wifi/status -> JSON current mode/ssid/ip/rssi
 *   POST /wifi/save   -> store creds in NVS, reboot into STA
 *   POST /wifi/forget -> erase creds, reboot into setup AP
 *
 * In setup-AP mode all unknown URIs 302 to /wifi so OS captive-portal
 * probes (generate_204, hotspot-detect.html, ...) pop the config page.
 */
#include <inttypes.h>
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include "cJSON.h"
#include "coli_mcu.h"
#include "coli_runtime.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"
#include "esp_http_server.h"
#include "esp_ota_ops.h"
#include "esp_system.h"
#include "faces.h"
#include "neato_audio.h"
#include "neato_usb.h"
#include "remote_soundboard.h"
#include "web.h"
#include "wifi_mgr.h"

static const char *TAG = "web";
static httpd_handle_t s_server = NULL;
static int s_ws_fd = -1;

#define COLI_API_PROMPT_BYTES 2048u
#define COLI_API_DECODED_CHUNK_BYTES 256u
#define COLI_API_DEFAULT_MAX_TOKENS 64u

typedef struct {
    httpd_req_t *request;
    bool failed;
} coli_http_stream_t;

extern const char index_html_start[] asm("_binary_index_html_start");
extern const char index_html_end[] asm("_binary_index_html_end");
extern const char wifi_html_start[] asm("_binary_wifi_html_start");
extern const char wifi_html_end[] asm("_binary_wifi_html_end");

/* ---- push Neato bytes to the websocket client (rx subscriber, called
 * from the USB rx task) */
typedef struct {
    size_t len;
    uint8_t data[];
} ws_msg_t;

static void ws_async_send(void *arg)
{
    ws_msg_t *msg = arg;
    if (s_ws_fd >= 0 && s_server) {
        httpd_ws_frame_t frame = {
            .type = HTTPD_WS_TYPE_TEXT,
            .payload = msg->data,
            .len = msg->len,
        };
        if (httpd_ws_send_frame_async(s_server, s_ws_fd, &frame) != ESP_OK) {
            s_ws_fd = -1;
        }
    }
    free(msg);
}

static void net_ws_push(const uint8_t *data, size_t len)
{
    if (s_ws_fd < 0 || !s_server) return;
    ws_msg_t *msg = malloc(sizeof(ws_msg_t) + len);
    if (!msg) return;
    msg->len = len;
    memcpy(msg->data, data, len);
    if (httpd_queue_work(s_server, ws_async_send, msg) != ESP_OK) {
        free(msg);
    }
}

/* ---- handlers */
static esp_err_t root_get(httpd_req_t *req)
{
    httpd_resp_set_type(req, "text/html");
    return httpd_resp_send(req, index_html_start, index_html_end - index_html_start);
}

static esp_err_t ws_handler(httpd_req_t *req)
{
    if (req->method == HTTP_GET) { /* handshake */
        s_ws_fd = httpd_req_to_sockfd(req);
        ESP_LOGI(TAG, "ws client connected");
        return ESP_OK;
    }
    httpd_ws_frame_t frame = { 0 };
    if (httpd_ws_recv_frame(req, &frame, 0) != ESP_OK) return ESP_FAIL;
    if (frame.len == 0 || frame.len > 256) return ESP_OK;
    uint8_t buf[257];
    frame.payload = buf;
    if (httpd_ws_recv_frame(req, &frame, frame.len) != ESP_OK) return ESP_FAIL;
    buf[frame.len] = 0;
    /* strip trailing newline; neato_send adds its own */
    while (frame.len && (buf[frame.len - 1] == '\n' || buf[frame.len - 1] == '\r'))
        buf[--frame.len] = 0;
    if (frame.len) neato_send((char *)buf);
    return ESP_OK;
}

static esp_err_t ota_post(httpd_req_t *req)
{
    const esp_partition_t *part = esp_ota_get_next_update_partition(NULL);
    if (!part) return httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "no ota part");
    esp_ota_handle_t ota;
    if (esp_ota_begin(part, req->content_len, &ota) != ESP_OK)
        return httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "ota begin fail");
    ESP_LOGI(TAG, "OTA start: %d bytes -> %s", req->content_len, part->label);

    char buf[2048];
    int remaining = req->content_len;
    while (remaining > 0) {
        int n = httpd_req_recv(req, buf, remaining < (int)sizeof(buf) ? remaining : (int)sizeof(buf));
        if (n <= 0) {
            esp_ota_abort(ota);
            return httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "recv fail");
        }
        if (esp_ota_write(ota, buf, n) != ESP_OK) {
            esp_ota_abort(ota);
            return httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "write fail");
        }
        remaining -= n;
    }
    if (esp_ota_end(ota) != ESP_OK ||
        esp_ota_set_boot_partition(part) != ESP_OK)
        return httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "finalize fail");

    httpd_resp_sendstr(req, "OK, rebooting\n");
    ESP_LOGW(TAG, "OTA done, rebooting");
    vTaskDelay(pdMS_TO_TICKS(500));
    esp_restart();
    return ESP_OK;
}

static bool coli_http_cancelled(void *context)
{
    const coli_http_stream_t *stream = context;
    return stream->failed || !coli_mcu_storage_ready();
}

static void coli_http_yield(void *context)
{
    (void)context;
    taskYIELD();
}

static void coli_http_chunk(void *context, const uint8_t *bytes, size_t count)
{
    coli_http_stream_t *stream = context;
    char event[COLI_API_DECODED_CHUNK_BYTES * 6u + 48u];
    size_t used = 0;
    static const char prefix[] = "data: {\"choices\":[{\"text\":\"";
    static const char suffix[] = "\"}]}\n\n";
    memcpy(event, prefix, sizeof(prefix) - 1u);
    used = sizeof(prefix) - 1u;
    for (size_t i = 0; i < count && used + sizeof(suffix) + 6u < sizeof(event);
         ++i) {
        uint8_t byte = bytes[i];
        if (byte == '\"' || byte == '\\') {
            event[used++] = '\\';
            event[used++] = (char)byte;
        } else if (byte == '\n' || byte == '\r' || byte == '\t') {
            event[used++] = '\\';
            event[used++] = byte == '\n' ? 'n' : byte == '\r' ? 'r' : 't';
        } else if (byte < 0x20u) {
            static const char hex[] = "0123456789abcdef";
            event[used++] = '\\';
            event[used++] = 'u';
            event[used++] = '0';
            event[used++] = '0';
            event[used++] = hex[byte >> 4u];
            event[used++] = hex[byte & 0x0fu];
        } else {
            event[used++] = (char)byte;
        }
    }
    memcpy(event + used, suffix, sizeof(suffix) - 1u);
    used += sizeof(suffix) - 1u;
    if (httpd_resp_send_chunk(stream->request, event, used) != ESP_OK)
        stream->failed = true;
}

static esp_err_t coli_completion_post(httpd_req_t *req)
{
    if (!coli_mcu_storage_ready()) {
        httpd_resp_set_status(req, "503 Service Unavailable");
        return httpd_resp_sendstr(req, "Colibri SSD is not mounted");
    }
    if (req->content_len <= 0 || req->content_len > COLI_API_PROMPT_BYTES)
        return httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST,
                                   "prompt body must be 1..2048 bytes");

    char *body = malloc((size_t)req->content_len + 1u);
    if (!body)
        return httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR,
                                   "out of memory");
    int received = 0;
    while (received < req->content_len) {
        int got = httpd_req_recv(req, body + received,
                                 req->content_len - received);
        if (got == HTTPD_SOCK_ERR_TIMEOUT) continue;
        if (got <= 0) {
            free(body);
            return ESP_FAIL;
        }
        received += got;
    }
    body[received] = 0;

    const char *prompt = body;
    size_t prompt_bytes = (size_t)received;
    size_t max_tokens = COLI_API_DEFAULT_MAX_TOKENS;
    cJSON *json = NULL;
    if (body[0] == '{') {
        json = cJSON_ParseWithLength(body, (size_t)received);
        cJSON *prompt_item = cJSON_GetObjectItemCaseSensitive(json, "prompt");
        cJSON *max_item = cJSON_GetObjectItemCaseSensitive(json, "max_tokens");
        if (!cJSON_IsString(prompt_item) || !prompt_item->valuestring) {
            cJSON_Delete(json);
            free(body);
            return httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST,
                                       "JSON prompt string required");
        }
        prompt = prompt_item->valuestring;
        prompt_bytes = strlen(prompt);
        if (cJSON_IsNumber(max_item) && max_item->valuedouble >= 1.0) {
            double requested = max_item->valuedouble;
            max_tokens = requested >= CONFIG_COLI_MCU_CONTEXT_TOKENS
                             ? CONFIG_COLI_MCU_CONTEXT_TOKENS - 1u
                             : (size_t)requested;
        }
    }
    if (prompt_bytes == 0) {
        cJSON_Delete(json);
        free(body);
        return httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST,
                                   "prompt cannot be empty");
    }
    if (max_tokens >= CONFIG_COLI_MCU_CONTEXT_TOKENS)
        max_tokens = CONFIG_COLI_MCU_CONTEXT_TOKENS - 1u;

    coli_http_stream_t stream = {.request = req};
    const coli_runtime_request_t request = {
        .model_path = CONFIG_COLI_MCU_MOUNT_PATH "/" CONFIG_COLI_MCU_MODEL_FILE,
        .tokenizer_path =
            CONFIG_COLI_MCU_MOUNT_PATH "/" CONFIG_COLI_MCU_TOKENIZER_FILE,
        .generation = {
            .prompt = (const uint8_t *)prompt,
            .prompt_bytes = prompt_bytes,
            .context_tokens = CONFIG_COLI_MCU_CONTEXT_TOKENS,
            .max_prompt_tokens = CONFIG_COLI_MCU_CONTEXT_TOKENS - max_tokens,
            .max_new_tokens = max_tokens,
            .workspace_bytes = CONFIG_COLI_MCU_WORKSPACE_BYTES,
            .decoded_chunk_bytes = COLI_API_DECODED_CHUNK_BYTES,
            .kv_cache_path =
                CONFIG_COLI_MCU_MOUNT_PATH "/" CONFIG_COLI_MCU_KV_FILE,
            .kv_page_bytes = CONFIG_COLI_MCU_KV_PAGE_BYTES,
            .should_cancel = coli_http_cancelled,
            .yield = coli_http_yield,
            .log_chunk = coli_http_chunk,
            .callback_context = &stream,
        },
    };
    httpd_resp_set_type(req, "text/event-stream");
    httpd_resp_set_hdr(req, "Cache-Control", "no-cache");
    coli_runtime_result_t result;
    coli_status_t status = coli_runtime_generate(&request, &result);
    cJSON_Delete(json);
    free(body);
    if (!stream.failed) {
        if (status == COLI_OK)
            httpd_resp_send_chunk(req, "data: [DONE]\n\n",
                                  HTTPD_RESP_USE_STRLEN);
        else {
            char error[96];
            int length = snprintf(error, sizeof(error),
                                  "data: {\"error\":%d,\"architecture\":%" PRIu32
                                  "}\n\n",
                                  status, result.architecture);
            httpd_resp_send_chunk(req, error, length);
        }
        httpd_resp_send_chunk(req, NULL, 0);
    }
    return stream.failed ? ESP_FAIL : ESP_OK;
}

/* ---- WiFi setup/settings handlers */
static esp_err_t wifi_page_get(httpd_req_t *req)
{
    httpd_resp_set_type(req, "text/html");
    return httpd_resp_send(req, wifi_html_start, wifi_html_end - wifi_html_start);
}

static esp_err_t wifi_scan_get(httpd_req_t *req)
{
    char *json = malloc(2048);
    if (!json) return httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "oom");
    int n = wifi_mgr_scan_json(json, 2048);
    httpd_resp_set_type(req, "application/json");
    esp_err_t r = httpd_resp_send(req, json, n);
    free(json);
    return r;
}

static esp_err_t wifi_status_get(httpd_req_t *req)
{
    char json[256];
    int n = wifi_mgr_status_json(json, sizeof(json));
    httpd_resp_set_type(req, "application/json");
    return httpd_resp_send(req, json, n);
}

/* pull a urlencoded form field out of a POST body, %XX/+ decoded */
static void form_value(const char *body, const char *key, char *out, size_t cap)
{
    out[0] = 0;
    char raw[192];
    if (httpd_query_key_value(body, key, raw, sizeof(raw)) != ESP_OK) return;
    size_t o = 0;
    for (size_t i = 0; raw[i] && o < cap - 1; i++) {
        if (raw[i] == '%' && raw[i + 1] && raw[i + 2]) {
            char h[3] = { raw[i + 1], raw[i + 2], 0 };
            out[o++] = (char)strtol(h, NULL, 16);
            i += 2;
        } else {
            out[o++] = raw[i] == '+' ? ' ' : raw[i];
        }
    }
    out[o] = 0;
}

static esp_err_t wifi_save_post(httpd_req_t *req)
{
    char body[384];
    if (req->content_len >= (int)sizeof(body)) {
        httpd_resp_set_status(req, "413 Payload Too Large");
        return httpd_resp_sendstr(req, "form body over 383 bytes\n");
    }
    int len = req->content_len;
    int received = 0;
    while (received < len) {
        int got = httpd_req_recv(req, body + received, len - received);
        if (got == HTTPD_SOCK_ERR_TIMEOUT) continue;
        if (got <= 0)
            return httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "recv fail");
        received += got;
    }
    body[received] = 0;
    char ssid[33], pass[65];
    form_value(body, "ssid", ssid, sizeof(ssid));
    form_value(body, "pass", pass, sizeof(pass));
    if (!ssid[0]) return httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "ssid required");
    httpd_resp_sendstr(req, "OK, rebooting to join\n");
    wifi_mgr_save_and_reboot(ssid, pass);
    return ESP_OK;
}

static esp_err_t wifi_forget_post(httpd_req_t *req)
{
    httpd_resp_sendstr(req, "OK, rebooting into setup AP\n");
    wifi_mgr_forget_and_reboot();
    return ESP_OK;
}

/* Captive portal: in setup-AP mode every unknown URI (incl. the OS probe
 * URLs like /generate_204, /hotspot-detect.html) 302s to the config page,
 * same pattern as ESP-IDF's captive_portal example. */
static esp_err_t captive_404_handler(httpd_req_t *req, httpd_err_code_t err)
{
    httpd_resp_set_status(req, "302 Temporary Redirect");
    httpd_resp_set_hdr(req, "Location", "http://192.168.4.1/wifi");
    httpd_resp_send(req, "Redirecting to setup", HTTPD_RESP_USE_STRLEN);
    return ESP_OK;
}

void web_start(void)
{
    neato_rx_subscribe(net_ws_push);
    httpd_config_t cfg = HTTPD_DEFAULT_CONFIG();
    cfg.lru_purge_enable = true;
    cfg.max_uri_handlers = 15;
    cfg.stack_size = 12288;
    if (httpd_start(&s_server, &cfg) != ESP_OK) {
        ESP_LOGE(TAG, "httpd start failed");
        return;
    }
    static const httpd_uri_t root = { .uri = "/", .method = HTTP_GET, .handler = root_get };
    static const httpd_uri_t ws = { .uri = "/ws", .method = HTTP_GET, .handler = ws_handler,
                                    .is_websocket = true };
    static const httpd_uri_t ota = { .uri = "/ota", .method = HTTP_POST, .handler = ota_post };
    static const httpd_uri_t wifi = { .uri = "/wifi", .method = HTTP_GET, .handler = wifi_page_get };
    static const httpd_uri_t wscan = { .uri = "/wifi/scan", .method = HTTP_GET, .handler = wifi_scan_get };
    static const httpd_uri_t wstat = { .uri = "/wifi/status", .method = HTTP_GET, .handler = wifi_status_get };
    static const httpd_uri_t wsave = { .uri = "/wifi/save", .method = HTTP_POST, .handler = wifi_save_post };
    static const httpd_uri_t wforget = { .uri = "/wifi/forget", .method = HTTP_POST, .handler = wifi_forget_post };
    static const httpd_uri_t speak = { .uri = "/speak", .method = HTTP_POST, .handler = speak_post };
    static const httpd_uri_t bank = { .uri = "/soundbank", .method = HTTP_POST, .handler = soundbank_post };
    static const httpd_uri_t soundplay = { .uri = "/soundboard/play", .method = HTTP_POST,
                                          .handler = soundboard_play_post };
    static const httpd_uri_t soundstatus = { .uri = "/soundboard/status", .method = HTTP_GET,
                                            .handler = soundboard_status_get };
    static const httpd_uri_t emote = { .uri = "/emote", .method = HTTP_POST, .handler = emote_post };
    static const httpd_uri_t completion = {
        .uri = "/v1/completions", .method = HTTP_POST,
        .handler = coli_completion_post,
    };
    httpd_register_uri_handler(s_server, &speak);
    httpd_register_uri_handler(s_server, &bank);
    httpd_register_uri_handler(s_server, &soundplay);
    httpd_register_uri_handler(s_server, &soundstatus);
    httpd_register_uri_handler(s_server, &emote);
    httpd_register_uri_handler(s_server, &completion);
    httpd_register_uri_handler(s_server, &root);
    httpd_register_uri_handler(s_server, &ws);
    httpd_register_uri_handler(s_server, &ota);
    httpd_register_uri_handler(s_server, &wifi);
    httpd_register_uri_handler(s_server, &wscan);
    httpd_register_uri_handler(s_server, &wstat);
    httpd_register_uri_handler(s_server, &wsave);
    httpd_register_uri_handler(s_server, &wforget);
    if (wifi_mgr_is_ap())
        httpd_register_err_handler(s_server, HTTPD_404_NOT_FOUND, captive_404_handler);
    ESP_LOGI(TAG, "web server up on port 80%s", wifi_mgr_is_ap() ? " (captive portal)" : "");
}
