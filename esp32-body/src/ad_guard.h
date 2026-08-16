#pragma once

#include <stdbool.h>
#include <stdint.h>
#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef esp_err_t (*ad_guard_output_fn)(bool suppress, void *ctx);

typedef struct {
    float min_confidence;
    uint32_t release_grace_ms;
    ad_guard_output_fn output;
    void *output_ctx;
} ad_guard_config_t;

typedef struct {
    const char *ad_id;
    uint32_t ad_duration_ms;
    uint32_t matched_offset_ms;
    float confidence;
} ad_guard_match_t;

/* Start the always-on ad suppression state machine. */
esp_err_t ad_guard_init(const ad_guard_config_t *config);

/* Report a fingerprint hit. The guard suppresses output for the known
 * remainder of the ad and extends suppression across back-to-back ads. */
esp_err_t ad_guard_report_match(const ad_guard_match_t *match);

/* Manual learning hook. A future fingerprint collector can use these marks
 * to label the current ring-buffer segment as advertising. */
void ad_guard_mark_manual_ad(void);

bool ad_guard_is_suppressing(void);
uint64_t ad_guard_suppress_until_ms(void);

#ifdef __cplusplus
}
#endif
