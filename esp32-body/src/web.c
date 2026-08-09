/* Embedded web dashboard + WebSocket bridge + OTA updates.
 *
 *   GET  /      -> dashboard UI (index.html embedded in flash)
 *   WS   /ws    -> bidirectional raw bridge to the Neato serial
 *   POST /ota   -> flash a new firmware image over WiFi
 */
#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"
#include "esp_http_server.h"
#include "esp_ota_ops.h"
#include "esp_system.h"

static const char *TAG = "web";
static httpd_handle_t s_server = NULL;
static int s_ws_fd = -1;

extern const char index_html_start[] asm("_binary_index_html_start");
extern const char index_html_end[] asm("_binary_index_html_end");

void neato_send(const char *cmd); /* main.c */

/* ---- push Neato bytes to the websocket client (called from USB rx task) */
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

void net_ws_push(const uint8_t *data, size_t len)
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

void web_start(void)
{
    httpd_config_t cfg = HTTPD_DEFAULT_CONFIG();
    cfg.lru_purge_enable = true;
    if (httpd_start(&s_server, &cfg) != ESP_OK) {
        ESP_LOGE(TAG, "httpd start failed");
        return;
    }
    static const httpd_uri_t root = { .uri = "/", .method = HTTP_GET, .handler = root_get };
    static const httpd_uri_t ws = { .uri = "/ws", .method = HTTP_GET, .handler = ws_handler,
                                    .is_websocket = true };
    static const httpd_uri_t ota = { .uri = "/ota", .method = HTTP_POST, .handler = ota_post };
    httpd_register_uri_handler(s_server, &root);
    httpd_register_uri_handler(s_server, &ws);
    httpd_register_uri_handler(s_server, &ota);
    ESP_LOGI(TAG, "web server up on port 80");
}
