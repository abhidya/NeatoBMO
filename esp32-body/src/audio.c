/* TTS/audio playback through the Neato's own speaker.
 *
 * The XV protocol can't stream audio, so the ESP32 drives the speaker
 * directly: sigma-delta modulated 1-bit stream on AUDIO_GPIO, one sample
 * per gptimer tick (ESP-IDF sdm_dac pattern). Wire AUDIO_GPIO through a
 * small amp (e.g. PAM8302) to the Neato's internal speaker.
 *
 *   POST /speak  body = raw unsigned 8-bit mono PCM @ 16000 Hz
 */
#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "esp_log.h"
#include "esp_heap_caps.h"
#include "esp_http_server.h"
#include "driver/sdm.h"
#include "driver/gptimer.h"

#define AUDIO_GPIO      17
#define AUDIO_RATE_HZ   16000
#define AUDIO_MAX_BYTES (2 * 1024 * 1024)   /* ~2 min at 16 kHz, lives in PSRAM */

static const char *TAG = "audio";
static sdm_channel_handle_t s_sdm;
static gptimer_handle_t s_timer;
static SemaphoreHandle_t s_done;

static volatile const uint8_t *s_buf;
static volatile size_t s_len;
static volatile size_t s_pos;

static bool IRAM_ATTR on_tick(gptimer_handle_t timer,
                              const gptimer_alarm_event_data_t *edata, void *ctx)
{
    BaseType_t hp = pdFALSE;
    size_t pos = s_pos;
    if (pos < s_len) {
        /* unsigned 8-bit sample -> signed pulse density */
        sdm_channel_set_pulse_density(s_sdm, (int8_t)(s_buf[pos] - 128));
        s_pos = pos + 1;
    } else {
        gptimer_stop(timer);
        sdm_channel_set_pulse_density(s_sdm, 0);
        xSemaphoreGiveFromISR(s_done, &hp);
    }
    return hp == pdTRUE;
}

void audio_init(void)
{
    s_done = xSemaphoreCreateBinary();

    sdm_config_t sdm_cfg = {
        .clk_src = SDM_CLK_SRC_DEFAULT,
        .gpio_num = AUDIO_GPIO,
        .sample_rate_hz = 1000000,          /* 1 MHz carrier, inaudible */
    };
    ESP_ERROR_CHECK(sdm_new_channel(&sdm_cfg, &s_sdm));
    ESP_ERROR_CHECK(sdm_channel_enable(s_sdm));
    sdm_channel_set_pulse_density(s_sdm, 0);

    gptimer_config_t tcfg = {
        .clk_src = GPTIMER_CLK_SRC_DEFAULT,
        .direction = GPTIMER_COUNT_UP,
        .resolution_hz = 8000000,           /* 8 MHz: divides 16 kHz exactly */
    };
    ESP_ERROR_CHECK(gptimer_new_timer(&tcfg, &s_timer));
    gptimer_event_callbacks_t cbs = { .on_alarm = on_tick };
    ESP_ERROR_CHECK(gptimer_register_event_callbacks(s_timer, &cbs, NULL));
    gptimer_alarm_config_t alarm = {
        .alarm_count = 8000000 / AUDIO_RATE_HZ,
        .reload_count = 0,
        .flags.auto_reload_on_alarm = true,
    };
    ESP_ERROR_CHECK(gptimer_set_alarm_action(s_timer, &alarm));
    ESP_ERROR_CHECK(gptimer_enable(s_timer));
    ESP_LOGI(TAG, "audio out on GPIO%d, %d Hz", AUDIO_GPIO, AUDIO_RATE_HZ);
}

/* Blocking: plays the buffer to the end. */
static void audio_play(const uint8_t *pcm, size_t len)
{
    xSemaphoreTake(s_done, 0);              /* clear any stale give */
    s_buf = pcm;
    s_len = len;
    s_pos = 0;
    gptimer_set_raw_count(s_timer, 0);
    ESP_ERROR_CHECK(gptimer_start(s_timer));
    uint32_t ms = (uint32_t)((uint64_t)len * 1000 / AUDIO_RATE_HZ) + 2000;
    if (xSemaphoreTake(s_done, pdMS_TO_TICKS(ms)) != pdTRUE) {
        gptimer_stop(s_timer);
        sdm_channel_set_pulse_density(s_sdm, 0);
        ESP_LOGW(TAG, "playback timed out");
    }
}

esp_err_t speak_post(httpd_req_t *req)
{
    int len = req->content_len;
    if (len <= 0 || len > AUDIO_MAX_BYTES)
        return httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "bad length");

    uint8_t *pcm = heap_caps_malloc(len, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (!pcm) pcm = malloc(len);
    if (!pcm) return httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "oom");

    int got = 0;
    while (got < len) {
        int n = httpd_req_recv(req, (char *)pcm + got, len - got);
        if (n <= 0) {
            free(pcm);
            return httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "recv fail");
        }
        got += n;
    }
    ESP_LOGI(TAG, "speaking %d bytes (%.1f s)", len, (float)len / AUDIO_RATE_HZ);
    audio_play(pcm, len);
    free(pcm);
    return httpd_resp_sendstr(req, "OK\n");
}
