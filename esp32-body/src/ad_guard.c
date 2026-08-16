#include "ad_guard.h"

#include <inttypes.h>
#include <string.h>

#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"

static const char *TAG = "ad_guard";

static ad_guard_config_t s_cfg;
static SemaphoreHandle_t s_lock;
static esp_timer_handle_t s_release_timer;
static bool s_initialized;
static bool s_suppressing;
static uint64_t s_until_ms;

static uint64_t now_ms(void)
{
    return (uint64_t)(esp_timer_get_time() / 1000ULL);
}

static void release_cb(void *arg)
{
    (void)arg;

    if (!s_lock) {
        return;
    }

    xSemaphoreTake(s_lock, portMAX_DELAY);
    const uint64_t now = now_ms();
    if (!s_suppressing || now < s_until_ms) {
        uint64_t remaining_us = 1000;
        if (s_suppressing && s_until_ms > now) {
            remaining_us = (s_until_ms - now) * 1000ULL;
        }
        xSemaphoreGive(s_lock);
        if (s_suppressing) {
            esp_timer_start_once(s_release_timer, remaining_us);
        }
        return;
    }

    s_suppressing = false;
    xSemaphoreGive(s_lock);

    if (s_cfg.output) {
        esp_err_t err = s_cfg.output(false, s_cfg.output_ctx);
        if (err != ESP_OK) {
            ESP_LOGW(TAG, "restore output failed: %s", esp_err_to_name(err));
        }
    }
    ESP_LOGI(TAG, "commercial interval ended; output restored");
}

esp_err_t ad_guard_init(const ad_guard_config_t *config)
{
    if (!config || !config->output) {
        return ESP_ERR_INVALID_ARG;
    }
    if (s_initialized) {
        return ESP_ERR_INVALID_STATE;
    }

    memset(&s_cfg, 0, sizeof(s_cfg));
    s_cfg = *config;
    if (s_cfg.min_confidence <= 0.0f) {
        s_cfg.min_confidence = 0.90f;
    }

    s_lock = xSemaphoreCreateMutex();
    if (!s_lock) {
        return ESP_ERR_NO_MEM;
    }

    const esp_timer_create_args_t args = {
        .callback = release_cb,
        .arg = NULL,
        .dispatch_method = ESP_TIMER_TASK,
        .name = "ad_release",
        .skip_unhandled_events = true,
    };
    esp_err_t err = esp_timer_create(&args, &s_release_timer);
    if (err != ESP_OK) {
        vSemaphoreDelete(s_lock);
        s_lock = NULL;
        return err;
    }

    s_initialized = true;
    ESP_LOGI(TAG, "ready; confidence threshold %.2f", s_cfg.min_confidence);
    return ESP_OK;
}

esp_err_t ad_guard_report_match(const ad_guard_match_t *match)
{
    if (!s_initialized) {
        return ESP_ERR_INVALID_STATE;
    }
    if (!match || !match->ad_id || match->ad_duration_ms == 0 ||
        match->matched_offset_ms >= match->ad_duration_ms) {
        return ESP_ERR_INVALID_ARG;
    }
    if (match->confidence < s_cfg.min_confidence) {
        return ESP_OK;
    }

    const uint64_t now = now_ms();
    const uint64_t remaining_ms =
        (uint64_t)match->ad_duration_ms - match->matched_offset_ms;
    const uint64_t candidate_until = now + remaining_ms + s_cfg.release_grace_ms;

    bool should_suppress = false;
    xSemaphoreTake(s_lock, portMAX_DELAY);
    if (!s_suppressing) {
        s_suppressing = true;
        should_suppress = true;
    }
    if (candidate_until > s_until_ms) {
        s_until_ms = candidate_until;
    }
    const uint64_t until = s_until_ms;
    xSemaphoreGive(s_lock);

    if (should_suppress) {
        esp_err_t err = s_cfg.output(true, s_cfg.output_ctx);
        if (err != ESP_OK) {
            ESP_LOGW(TAG, "suppress output failed: %s", esp_err_to_name(err));
        }
    }

    esp_timer_stop(s_release_timer);
    esp_timer_start_once(s_release_timer, (until - now) * 1000ULL);

    ESP_LOGI(TAG,
             "ad=%s confidence=%.3f offset=%" PRIu32 "/%" PRIu32
             "ms; suppressing for ~%" PRIu64 "ms",
             match->ad_id, match->confidence, match->matched_offset_ms,
             match->ad_duration_ms, until - now);
    return ESP_OK;
}

void ad_guard_mark_manual_ad(void)
{
    ESP_LOGI(TAG, "manual ad mark requested (fingerprint learner hook)");
}

bool ad_guard_is_suppressing(void)
{
    if (!s_initialized || !s_lock) {
        return false;
    }
    xSemaphoreTake(s_lock, portMAX_DELAY);
    bool value = s_suppressing;
    xSemaphoreGive(s_lock);
    return value;
}

uint64_t ad_guard_suppress_until_ms(void)
{
    if (!s_initialized || !s_lock) {
        return 0;
    }
    xSemaphoreTake(s_lock, portMAX_DELAY);
    uint64_t value = s_until_ms;
    xSemaphoreGive(s_lock);
    return value;
}
