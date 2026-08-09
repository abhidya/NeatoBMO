/* WiFi manager: NVS-provisioned STA with captive-portal fallback AP.
 * See wifi_mgr.h for the boot flow. DNS hijack uses espressif/dns_server
 * (same building block as ESP-IDF's captive_portal example).
 */
#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/event_groups.h"
#include "esp_wifi.h"
#include "esp_event.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "nvs_flash.h"
#include "nvs.h"
#include "dns_server.h"
#include "wifi_secrets.h" /* gitignored compiled-in fallback creds */
#include "wifi_mgr.h"

#define AP_SSID        "NeatoBMO-Setup"
#define STA_MAX_RETRY  5
#define STA_TIMEOUT_MS 15000
#define NVS_NS         "wificfg"

static const char *TAG = "wifi_mgr";

static EventGroupHandle_t s_ev;
#define BIT_GOT_IP BIT0
#define BIT_FAIL   BIT1

static bool s_ap_mode;
static bool s_connected;
static bool s_ever_connected;
static int  s_retries;
static char s_ssid[33];
static char s_ip[16] = "";

bool wifi_mgr_is_ap(void) { return s_ap_mode; }

static void on_event(void *arg, esp_event_base_t base, int32_t id, void *data)
{
    if (base == WIFI_EVENT && id == WIFI_EVENT_STA_START) {
        if (!s_ap_mode) esp_wifi_connect();
    } else if (base == WIFI_EVENT && id == WIFI_EVENT_STA_DISCONNECTED) {
        s_connected = false;
        if (s_ap_mode) return;
        if (!s_ever_connected && ++s_retries >= STA_MAX_RETRY) {
            xEventGroupSetBits(s_ev, BIT_FAIL);
            return;
        }
        if (s_ever_connected) vTaskDelay(pdMS_TO_TICKS(2000));
        esp_wifi_connect();
    } else if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP) {
        ip_event_got_ip_t *e = data;
        snprintf(s_ip, sizeof(s_ip), IPSTR, IP2STR(&e->ip_info.ip));
        s_connected = s_ever_connected = true;
        s_retries = 0;
        ESP_LOGI(TAG, "got ip: %s  (nc this on port 2323 for logs)", s_ip);
        xEventGroupSetBits(s_ev, BIT_GOT_IP);
    }
}

/* Load creds: NVS wins; an explicitly-saved empty ssid means "forget" was
 * used -> force setup AP. Absent keys (fresh flash) -> compiled-in secrets. */
static bool load_creds(char *ssid, size_t scap, char *pass, size_t pcap, bool *force_ap)
{
    *force_ap = false;
    nvs_handle_t h;
    if (nvs_open(NVS_NS, NVS_READONLY, &h) == ESP_OK) {
        size_t sl = scap, pl = pcap;
        esp_err_t e = nvs_get_str(h, "ssid", ssid, &sl);
        if (e == ESP_OK) {
            if (nvs_get_str(h, "pass", pass, &pl) != ESP_OK) pass[0] = 0;
            nvs_close(h);
            if (!ssid[0]) { *force_ap = true; return false; }
            return true;
        }
        nvs_close(h);
    }
    strlcpy(ssid, WIFI_SSID, scap);
    strlcpy(pass, WIFI_PASS, pcap);
    return ssid[0] != 0;
}

static void start_ap(void)
{
    s_ap_mode = true;
    esp_wifi_stop(); /* harmless if not started */
    esp_netif_create_default_wifi_ap();

    wifi_config_t ap = { 0 };
    strcpy((char *)ap.ap.ssid, AP_SSID);
    ap.ap.ssid_len = strlen(AP_SSID);
    ap.ap.authmode = WIFI_AUTH_OPEN;
    ap.ap.max_connection = 4;
    /* APSTA so esp_wifi_scan still works from the setup portal */
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_APSTA));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_AP, &ap));
    ESP_ERROR_CHECK(esp_wifi_start());

    /* answer every DNS query with the AP IP so phones auto-open the portal */
    dns_server_config_t dns = DNS_SERVER_CONFIG_SINGLE("*", "WIFI_AP_DEF");
    start_dns_server(&dns);
    ESP_LOGW(TAG, "setup AP \"%s\" up: connect and browse to http://192.168.4.1/wifi", AP_SSID);
}

void wifi_mgr_start(void)
{
    esp_err_t err = nvs_flash_init();
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        nvs_flash_erase();
        nvs_flash_init();
    }
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    s_ev = xEventGroupCreate();
    esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID, on_event, NULL);
    esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP, on_event, NULL);

    esp_netif_create_default_wifi_sta();
    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));

    char pass[65] = { 0 };
    bool force_ap;
    bool have = load_creds(s_ssid, sizeof(s_ssid), pass, sizeof(pass), &force_ap);
    if (have && !force_ap) {
        wifi_config_t wc = { 0 };
        strlcpy((char *)wc.sta.ssid, s_ssid, sizeof(wc.sta.ssid));
        strlcpy((char *)wc.sta.password, pass, sizeof(wc.sta.password));
        ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
        ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &wc));
        ESP_ERROR_CHECK(esp_wifi_start());
        ESP_LOGI(TAG, "joining \"%s\"...", s_ssid);
        EventBits_t bits = xEventGroupWaitBits(s_ev, BIT_GOT_IP | BIT_FAIL,
                                               pdFALSE, pdFALSE,
                                               pdMS_TO_TICKS(STA_TIMEOUT_MS));
        if (bits & BIT_GOT_IP) return;
        ESP_LOGW(TAG, "could not join \"%s\", falling back to setup AP", s_ssid);
    } else {
        ESP_LOGW(TAG, "%s", force_ap ? "wifi was forgotten" : "no wifi creds");
    }
    start_ap();
}

/* ---- JSON for /wifi endpoints ------------------------------------------ */

static int json_escape(char *out, size_t cap, const char *in)
{
    size_t o = 0;
    for (; *in && o < cap - 2; in++) {
        if (*in == '"' || *in == '\\') { out[o++] = '\\'; out[o++] = *in; }
        else if ((unsigned char)*in >= 0x20) out[o++] = *in;
    }
    out[o] = 0;
    return o;
}

int wifi_mgr_scan_json(char *buf, size_t cap)
{
    static wifi_ap_record_t recs[20]; /* off-stack; httpd task stack is small */
    uint16_t n = 20;
    if (esp_wifi_scan_start(NULL, true) != ESP_OK ||
        esp_wifi_scan_get_ap_records(&n, recs) != ESP_OK) n = 0;
    size_t o = snprintf(buf, cap, "[");
    for (int i = 0; i < n && o + 96 < cap; i++) {
        if (!recs[i].ssid[0]) continue;
        char esc[80];
        json_escape(esc, sizeof(esc), (const char *)recs[i].ssid);
        o += snprintf(buf + o, cap - o, "%s{\"ssid\":\"%s\",\"rssi\":%d,\"open\":%s}",
                      o > 1 ? "," : "", esc, recs[i].rssi,
                      recs[i].authmode == WIFI_AUTH_OPEN ? "true" : "false");
    }
    o += snprintf(buf + o, cap - o, "]");
    return o;
}

int wifi_mgr_status_json(char *buf, size_t cap)
{
    char esc[80];
    json_escape(esc, sizeof(esc), s_ssid);
    int rssi = 0;
    wifi_ap_record_t ap;
    if (s_connected && esp_wifi_sta_get_ap_info(&ap) == ESP_OK) rssi = ap.rssi;
    return snprintf(buf, cap,
        "{\"mode\":\"%s\",\"ssid\":\"%s\",\"ip\":\"%s\",\"rssi\":%d,\"connected\":%s}",
        s_ap_mode ? "ap" : "sta", esc, s_ip, rssi, s_connected ? "true" : "false");
}

/* ---- persist + reboot --------------------------------------------------- */

static void reboot_task(void *arg)
{
    vTaskDelay(pdMS_TO_TICKS(800)); /* let the HTTP reply flush */
    esp_restart();
}

static void save_creds(const char *ssid, const char *pass)
{
    nvs_handle_t h;
    if (nvs_open(NVS_NS, NVS_READWRITE, &h) != ESP_OK) return;
    nvs_set_str(h, "ssid", ssid);
    nvs_set_str(h, "pass", pass);
    nvs_commit(h);
    nvs_close(h);
}

void wifi_mgr_save_and_reboot(const char *ssid, const char *pass)
{
    ESP_LOGW(TAG, "saving wifi creds for \"%s\", rebooting", ssid);
    save_creds(ssid, pass);
    xTaskCreate(reboot_task, "reboot", 2048, NULL, 5, NULL);
}

void wifi_mgr_forget_and_reboot(void)
{
    ESP_LOGW(TAG, "forgetting wifi, rebooting into setup AP");
    save_creds("", ""); /* empty ssid marker = force setup AP over compiled creds */
    xTaskCreate(reboot_task, "reboot", 2048, NULL, 5, NULL);
}
