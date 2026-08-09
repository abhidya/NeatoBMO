/* WiFi manager: NVS-provisioned STA with captive-portal fallback AP.
 *
 * Boot: try NVS creds (else compiled-in wifi_secrets.h). If join fails,
 * start an open softAP "NeatoBMO-Setup" at 192.168.4.1 with a DNS hijack
 * so phones auto-open the /wifi config page.
 */
#pragma once

#include <stdbool.h>
#include <stddef.h>
#include "esp_err.h"

void wifi_mgr_start(void); /* blocks until STA has IP or fallback AP is up */
bool wifi_mgr_is_ap(void);

/* JSON helpers for the /wifi HTTP endpoints (return bytes written) */
int wifi_mgr_scan_json(char *buf, size_t cap);
int wifi_mgr_status_json(char *buf, size_t cap);

/* Both persist to NVS then reboot after a short delay (reply to HTTP first) */
void wifi_mgr_save_and_reboot(const char *ssid, const char *pass);
void wifi_mgr_forget_and_reboot(void);
