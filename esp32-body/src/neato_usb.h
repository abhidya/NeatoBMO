/* Serialized USB command and binary-transfer access to the Neato. */
#pragma once

#include <stddef.h>
#include <stdint.h>
#include "esp_err.h"

#define NEATO_MAX_WAV_BYTES (512U * 1024U)

void neato_send(const char *cmd);
esp_err_t neato_send_binary(const char *cmd, const uint8_t *payload,
                            size_t payload_len, uint32_t timeout_ms);
