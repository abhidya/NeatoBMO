/* Serialized USB command and binary-transfer access to the Neato. */
#pragma once

#include <stddef.h>
#include <stdint.h>
#include "esp_err.h"

#define NEATO_MAX_WAV_BYTES (512U * 1024U)
/* One full sound-bank image (770048 B) plus headroom; the hard cap for any
 * binary transfer relayed to the robot. */
#define NEATO_MAX_BINARY_BYTES (896U * 1024U)
#define NEATO_SOUND_BANK_BYTES 770048U

void neato_send(const char *cmd);
esp_err_t neato_send_checked(const char *cmd);
esp_err_t neato_send_binary(const char *cmd, const uint8_t *payload,
                            size_t payload_len, uint32_t timeout_ms);
