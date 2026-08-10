/* HTTP entry point for native Neato-speaker WAV playback. */
#pragma once

#include "esp_http_server.h"

esp_err_t speak_post(httpd_req_t *req);
