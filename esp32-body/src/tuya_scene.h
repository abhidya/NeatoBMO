#pragma once

#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    /* Example US endpoint: https://openapi.tuyaus.com */
    const char *endpoint;
    const char *client_id;
    const char *client_secret;
} tuya_scene_config_t;

/* Uses Tuya Cloud project credentials, NOT the user's Smart Life password.
 * Link the Smart Life account to the cloud project by QR authorization. */
esp_err_t tuya_scene_init(const tuya_scene_config_t *config);

/* Trigger a Smart Life/Tuya Tap-to-Run rule by rule ID. */
esp_err_t tuya_scene_trigger(const char *rule_id);

#ifdef __cplusplus
}
#endif
