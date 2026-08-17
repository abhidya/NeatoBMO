/* SPIFFS-backed file store for the web portal.
 *
 * The `storage` partition (10 MB SPIFFS) is mounted at /spiffs.  Handlers
 * here accept a bounded upload, store it under a sanitized flat name, serve
 * it back, and list what exists.  This is the "local file on the ESP32"
 * seam: BMO's decrypted software and its report land here and are served on
 * the device's own web dashboard at /file?name=...
 */
#include <ctype.h>
#include <dirent.h>
#include <stdio.h>
#include <string.h>
#include <strings.h>
#include <sys/stat.h>
#include <unistd.h>
#include "cJSON.h"
#include "esp_http_server.h"
#include "esp_log.h"
#include "esp_spiffs.h"
#include "files.h"

static const char *TAG = "files";
#define FILES_BASE "/spiffs"
#define MAX_NAME 64
#define MAX_FILE (2 * 1024 * 1024)   /* ~805 KB firmware + headroom */

static bool name_ok(const char *name)
{
    size_t n;
    if (!name || !name[0]) return false;
    n = strlen(name);
    if (n > MAX_NAME) return false;
    for (size_t i = 0; i < n; i++) {
        char c = name[i];
        if (!isalnum((unsigned char)c) && c != '.' && c != '_' && c != '-')
            return false;
    }
    return true;
}

static esp_err_t query_name(httpd_req_t *req, char *out, size_t cap)
{
    char qs[128];
    if (httpd_req_get_url_query_str(req, qs, sizeof(qs)) != ESP_OK)
        return ESP_ERR_INVALID_ARG;
    if (httpd_query_key_value(qs, "name", out, cap) != ESP_OK)
        return ESP_ERR_INVALID_ARG;
    return ESP_OK;
}

static void file_path(char *out, size_t cap, const char *name)
{
    snprintf(out, cap, FILES_BASE "/%s", name);
}

static const char *content_type_for(const char *name)
{
    const char *dot = strrchr(name, '.');
    if (!dot) return "application/octet-stream";
    if (!strcasecmp(dot, ".json")) return "application/json";
    if (!strcasecmp(dot, ".txt")) return "text/plain";
    if (!strcasecmp(dot, ".html")) return "text/html";
    if (!strcasecmp(dot, ".bin")) return "application/octet-stream";
    return "application/octet-stream";
}

esp_err_t files_init(void)
{
    esp_vfs_spiffs_conf_t conf = {
        .base_path = FILES_BASE,
        .partition_label = "storage",
        .max_files = 5,
        .format_if_mount_failed = true,
    };
    esp_err_t err = esp_vfs_spiffs_register(&conf);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "spiffs mount failed: %s", esp_err_to_name(err));
        return err;
    }
    size_t total = 0, used = 0;
    esp_spiffs_info("storage", &total, &used);
    ESP_LOGI(TAG, "spiffs up: %u/%u bytes used", (unsigned)used, (unsigned)total);
    return ESP_OK;
}

esp_err_t files_put_post(httpd_req_t *req)
{
    char name[MAX_NAME + 1];
    if (query_name(req, name, sizeof(name)) != ESP_OK || !name_ok(name)) {
        httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST,
                            "name required (A-Za-z0-9._-)");
        return ESP_FAIL;
    }
    if (req->content_len <= 0 || req->content_len > MAX_FILE) {
        httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "file too large");
        return ESP_FAIL;
    }

    char *buf = malloc(req->content_len);
    if (!buf) {
        httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "oom");
        return ESP_FAIL;
    }
    int received = 0;
    while (received < req->content_len) {
        int got = httpd_req_recv(req, buf + received,
                                 req->content_len - received);
        if (got == HTTPD_SOCK_ERR_TIMEOUT) continue;
        if (got <= 0) {
            free(buf);
            httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR,
                                "recv fail");
            return ESP_FAIL;
        }
        received += got;
    }

    char path[MAX_NAME + sizeof(FILES_BASE) + 2];
    file_path(path, sizeof(path), name);
    FILE *f = fopen(path, "wb");
    if (!f) {
        free(buf);
        httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "open fail");
        return ESP_FAIL;
    }
    size_t written = fwrite(buf, 1, received, f);
    fclose(f);
    free(buf);
    if (written != (size_t)received) {
        unlink(path);
        httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "write fail");
        return ESP_FAIL;
    }

    ESP_LOGI(TAG, "stored %s (%d bytes)", name, received);
    httpd_resp_sendstr(req, "OK");
    return ESP_OK;
}

esp_err_t files_get(httpd_req_t *req)
{
    char name[MAX_NAME + 1];
    if (query_name(req, name, sizeof(name)) != ESP_OK || !name_ok(name)) {
        httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "bad name");
        return ESP_FAIL;
    }

    char path[MAX_NAME + sizeof(FILES_BASE) + 2];
    file_path(path, sizeof(path), name);
    struct stat st;
    if (stat(path, &st) != 0 || st.st_size <= 0 || st.st_size > MAX_FILE) {
        httpd_resp_send_err(req, HTTPD_404_NOT_FOUND, "not found");
        return ESP_FAIL;
    }

    FILE *f = fopen(path, "rb");
    if (!f) {
        httpd_resp_send_err(req, HTTPD_404_NOT_FOUND, "not found");
        return ESP_FAIL;
    }

    httpd_resp_set_type(req, content_type_for(name));
    httpd_resp_set_hdr(req, "Content-Disposition", "inline");
    char buf[1024];
    size_t n;
    while ((n = fread(buf, 1, sizeof(buf), f)) > 0) {
        if (httpd_resp_send_chunk(req, buf, n) != ESP_OK) {
            fclose(f);
            return ESP_FAIL;
        }
    }
    fclose(f);
    httpd_resp_send_chunk(req, NULL, 0);
    return ESP_OK;
}

esp_err_t files_list_get(httpd_req_t *req)
{
    DIR *dir = opendir(FILES_BASE);
    if (!dir) {
        httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "opendir fail");
        return ESP_FAIL;
    }

    cJSON *root = cJSON_CreateArray();
    struct dirent *ent;
    while ((ent = readdir(dir)) != NULL) {
        if (ent->d_name[0] == '.') continue;
        char path[MAX_NAME + sizeof(FILES_BASE) + 2];
        file_path(path, sizeof(path), ent->d_name);
        struct stat st;
        if (stat(path, &st) != 0) continue;
        cJSON *item = cJSON_CreateObject();
        cJSON_AddStringToObject(item, "name", ent->d_name);
        cJSON_AddNumberToObject(item, "size", st.st_size);
        cJSON_AddItemToArray(root, item);
    }
    closedir(dir);

    char *js = cJSON_PrintUnformatted(root);
    if (!js) {
        cJSON_Delete(root);
        httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "json fail");
        return ESP_FAIL;
    }
    httpd_resp_set_type(req, "application/json");
    esp_err_t r = httpd_resp_sendstr(req, js);
    cJSON_free(js);
    cJSON_Delete(root);
    return r;
}
