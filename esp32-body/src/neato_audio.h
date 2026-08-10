/* HTTP entry points for native Neato-speaker WAV playback and the
 * sound-bank relay ("Upload sound" flash writes over WiFi). */
#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include "esp_http_server.h"

esp_err_t speak_post(httpd_req_t *req);
esp_err_t soundbank_post(httpd_req_t *req);
esp_err_t neato_install_soundbank(const uint8_t *bank, size_t len,
                                  const char *expected_sha256);
esp_err_t neato_sound_operation_begin(uint32_t timeout_ms);
void neato_sound_operation_end(void);
bool neato_soundbank_matches(const char *sha256);
