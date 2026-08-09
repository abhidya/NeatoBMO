/* WiFi + network log mirror for the neato body board.
 *
 * Joins the configured WiFi and serves logs on TCP port 2323.
 * Everything printed via ESP_LOG or printf is teed to any connected client,
 * so "nc <board-ip> 2323" becomes the serial monitor.
 */
#pragma once

#include "wifi_secrets.h" /* gitignored; copy from wifi_secrets.example.h */
#define LOG_TCP_PORT 2323

void wifi_log_start(void);
