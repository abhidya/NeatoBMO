#include "tuya_scene.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/time.h>

#include "cJSON.h"
#include "esp_http_client.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "mbedtls/md.h"
#include "mbedtls/sha256.h"

static const char *TAG = "tuya_scene";

static tuya_scene_config_t s_cfg;
static char s_token[160];
static int64_t s_token_expiry_us;
static bool s_initialized;

static const char EMPTY_SHA256[] =
    "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855";

static int64_t unix_ms(void)
{
    struct timeval tv;
    gettimeofday(&tv, NULL);
    return (int64_t)tv.tv_sec * 1000LL + tv.tv_usec / 1000;
}

static void bytes_to_hex_upper(const unsigned char *in, size_t n, char *out)
{
    static const char hex[] = "0123456789ABCDEF";
    for (size_t i = 0; i < n; ++i) {
        out[i * 2] = hex[(in[i] >> 4) & 0x0f];
        out[i * 2 + 1] = hex[in[i] & 0x0f];
    }
    out[n * 2] = '\0';
}

static esp_err_t hmac_sha256_hex(const char *key, const char *msg, char out[65])
{
    const mbedtls_md_info_t *info = mbedtls_md_info_from_type(MBEDTLS_MD_SHA256);
    if (!info) {
        return ESP_FAIL;
    }
    unsigned char digest[32];
    int rc = mbedtls_md_hmac(info,
                             (const unsigned char *)key, strlen(key),
                             (const unsigned char *)msg, strlen(msg), digest);
    if (rc != 0) {
        return ESP_FAIL;
    }
    bytes_to_hex_upper(digest, sizeof(digest), out);
    return ESP_OK;
}

static esp_err_t body_sha256_hex(const char *body, char out[65])
{
    unsigned char digest[32];
    const unsigned char *data = (const unsigned char *)(body ? body : "");
    size_t len = body ? strlen(body) : 0;
    int rc = mbedtls_sha256(data, len, digest, 0);
    if (rc != 0) {
        return ESP_FAIL;
    }
    static const char hex[] = "0123456789abcdef";
    for (size_t i = 0; i < sizeof(digest); ++i) {
        out[i * 2] = hex[digest[i] >> 4];
        out[i * 2 + 1] = hex[digest[i] & 0x0f];
    }
    out[64] = '\0';
    return ESP_OK;
}

typedef struct {
    char *buf;
    size_t cap;
    size_t len;
} response_buf_t;

static esp_err_t http_event(esp_http_client_event_t *evt)
{
    response_buf_t *resp = (response_buf_t *)evt->user_data;
    if (evt->event_id == HTTP_EVENT_ON_DATA && resp && evt->data_len > 0) {
        size_t room = resp->cap > resp->len ? resp->cap - resp->len - 1 : 0;
        size_t copy = (size_t)evt->data_len < room ? (size_t)evt->data_len : room;
        if (copy > 0) {
            memcpy(resp->buf + resp->len, evt->data, copy);
            resp->len += copy;
            resp->buf[resp->len] = '\0';
        }
    }
    return ESP_OK;
}

static esp_err_t signed_request(const char *method,
                                const char *path,
                                const char *body,
                                bool include_token,
                                char *response,
                                size_t response_cap)
{
    if (!s_initialized || !path || !response || response_cap < 2) {
        return ESP_ERR_INVALID_ARG;
    }

    const int64_t t = unix_ms();
    /* Tuya requires a real 13-digit Unix-ms timestamp. Wi-Fi/SNTP must be ready. */
    if (t < 1600000000000LL) {
        ESP_LOGE(TAG, "system clock is not synchronized");
        return ESP_ERR_INVALID_STATE;
    }

    char body_hash[65];
    if (!body || body[0] == '\0') {
        memcpy(body_hash, EMPTY_SHA256, sizeof(EMPTY_SHA256));
    } else {
        ESP_RETURN_ON_ERROR(body_sha256_hex(body, body_hash), TAG, "body SHA256 failed");
    }

    char string_to_sign[1024];
    int n = snprintf(string_to_sign, sizeof(string_to_sign),
                     "%s\n%s\n\n%s", method, body_hash, path);
    if (n < 0 || n >= (int)sizeof(string_to_sign)) {
        return ESP_ERR_INVALID_SIZE;
    }

    char sign_source[1500];
    n = snprintf(sign_source, sizeof(sign_source), "%s%s%lld%s",
                 s_cfg.client_id, include_token ? s_token : "",
                 (long long)t, string_to_sign);
    if (n < 0 || n >= (int)sizeof(sign_source)) {
        return ESP_ERR_INVALID_SIZE;
    }

    char signature[65];
    ESP_RETURN_ON_ERROR(hmac_sha256_hex(s_cfg.client_secret, sign_source, signature),
                        TAG, "HMAC failed");

    char url[1024];
    n = snprintf(url, sizeof(url), "%s%s", s_cfg.endpoint, path);
    if (n < 0 || n >= (int)sizeof(url)) {
        return ESP_ERR_INVALID_SIZE;
    }

    response[0] = '\0';
    response_buf_t resp = {.buf = response, .cap = response_cap, .len = 0};
    esp_http_client_config_t cfg = {
        .url = url,
        .event_handler = http_event,
        .user_data = &resp,
        .timeout_ms = 8000,
    };
    esp_http_client_handle_t client = esp_http_client_init(&cfg);
    if (!client) {
        return ESP_ERR_NO_MEM;
    }

    esp_http_client_method_t http_method = HTTP_METHOD_GET;
    if (strcmp(method, "POST") == 0) {
        http_method = HTTP_METHOD_POST;
    } else if (strcmp(method, "PUT") == 0) {
        http_method = HTTP_METHOD_PUT;
    } else if (strcmp(method, "DELETE") == 0) {
        http_method = HTTP_METHOD_DELETE;
    }
    esp_http_client_set_method(client, http_method);

    char tbuf[24];
    snprintf(tbuf, sizeof(tbuf), "%lld", (long long)t);
    esp_http_client_set_header(client, "client_id", s_cfg.client_id);
    esp_http_client_set_header(client, "sign", signature);
    esp_http_client_set_header(client, "sign_method", "HMAC-SHA256");
    esp_http_client_set_header(client, "t", tbuf);
    if (include_token) {
        esp_http_client_set_header(client, "access_token", s_token);
    }
    if (body && body[0]) {
        esp_http_client_set_header(client, "Content-Type", "application/json");
        esp_http_client_set_post_field(client, body, strlen(body));
    }

    esp_err_t err = esp_http_client_perform(client);
    int status = esp_http_client_get_status_code(client);
    esp_http_client_cleanup(client);
    if (err != ESP_OK) {
        return err;
    }
    if (status < 200 || status >= 300) {
        ESP_LOGW(TAG, "Tuya HTTP status %d: %s", status, response);
        return ESP_FAIL;
    }
    return ESP_OK;
}

static esp_err_t refresh_token(void)
{
    char response[1024];
    ESP_RETURN_ON_ERROR(signed_request("GET", "/v1.0/token?grant_type=1", NULL,
                                       false, response, sizeof(response)),
                        TAG, "token request failed");

    cJSON *root = cJSON_Parse(response);
    if (!root) {
        return ESP_ERR_INVALID_RESPONSE;
    }
    cJSON *success = cJSON_GetObjectItem(root, "success");
    cJSON *result = cJSON_GetObjectItem(root, "result");
    cJSON *access = result ? cJSON_GetObjectItem(result, "access_token") : NULL;
    cJSON *expires = result ? cJSON_GetObjectItem(result, "expire_time") : NULL;
    if (!cJSON_IsTrue(success) || !cJSON_IsString(access)) {
        ESP_LOGW(TAG, "Tuya token response rejected: %s", response);
        cJSON_Delete(root);
        return ESP_FAIL;
    }

    strlcpy(s_token, access->valuestring, sizeof(s_token));
    int expiry_s = cJSON_IsNumber(expires) ? expires->valueint : 7200;
    if (expiry_s < 60) {
        expiry_s = 60;
    }
    s_token_expiry_us = esp_timer_get_time() + ((int64_t)expiry_s - 30) * 1000000LL;
    cJSON_Delete(root);
    return ESP_OK;
}

esp_err_t tuya_scene_init(const tuya_scene_config_t *config)
{
    if (!config || !config->endpoint || !config->client_id || !config->client_secret ||
        !config->endpoint[0] || !config->client_id[0] || !config->client_secret[0]) {
        return ESP_ERR_INVALID_ARG;
    }
    s_cfg = *config;
    s_token[0] = '\0';
    s_token_expiry_us = 0;
    s_initialized = true;
    return ESP_OK;
}

esp_err_t tuya_scene_trigger(const char *rule_id)
{
    if (!s_initialized || !rule_id || !rule_id[0]) {
        return ESP_ERR_INVALID_ARG;
    }
    if (!s_token[0] || esp_timer_get_time() >= s_token_expiry_us) {
        ESP_RETURN_ON_ERROR(refresh_token(), TAG, "could not refresh Tuya token");
    }

    char path[512];
    int n = snprintf(path, sizeof(path),
                     "/v2.0/cloud/scene/rule/%s/actions/trigger", rule_id);
    if (n < 0 || n >= (int)sizeof(path)) {
        return ESP_ERR_INVALID_SIZE;
    }

    char response[1024];
    esp_err_t err = signed_request("POST", path, NULL, true, response, sizeof(response));
    if (err != ESP_OK) {
        return err;
    }

    cJSON *root = cJSON_Parse(response);
    if (!root) {
        return ESP_ERR_INVALID_RESPONSE;
    }
    cJSON *success = cJSON_GetObjectItem(root, "success");
    bool ok = cJSON_IsTrue(success);
    cJSON_Delete(root);
    if (!ok) {
        ESP_LOGW(TAG, "Tuya scene trigger rejected: %s", response);
        return ESP_FAIL;
    }
    return ESP_OK;
}
