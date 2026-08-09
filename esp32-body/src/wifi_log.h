/* Network log mirror for the neato body board.
 *
 * Brings WiFi up via wifi_mgr, then serves logs on TCP port 2323.
 * Everything printed via ESP_LOG or printf is teed to any connected client,
 * so "nc <board-ip> 2323" becomes the serial monitor.
 * Raw Neato command bridge lives on TCP 3333.
 */
#pragma once

#define LOG_TCP_PORT 2323

void wifi_log_start(void);
