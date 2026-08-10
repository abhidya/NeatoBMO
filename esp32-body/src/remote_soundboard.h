/* Asynchronous remote Neato sound-module loader and player. */
#pragma once

#include "esp_http_server.h"

esp_err_t soundboard_play_post(httpd_req_t *req);
esp_err_t soundboard_status_get(httpd_req_t *req);

