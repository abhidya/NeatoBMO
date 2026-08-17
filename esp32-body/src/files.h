/* SPIFFS-backed file store for the web portal ("BMO's recovered software").
 *
 *   POST /file?name=<name>   body = file bytes   -> "OK"
 *   GET  /file?name=<name>                       -> file bytes
 *   GET  /files                                  -> JSON [{name,size}, ...]
 *
 * Names are restricted to [A-Za-z0-9._-]{1,64} so a caller cannot path
 * traverse outside the mounted SPIFFS base. */
#pragma once

#include <stddef.h>
#include "esp_err.h"
#include "esp_http_server.h"

esp_err_t files_init(void);
esp_err_t files_put_post(httpd_req_t *req);
esp_err_t files_get(httpd_req_t *req);
esp_err_t files_list_get(httpd_req_t *req);
