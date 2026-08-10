#include <stdio.h>
#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"
#include "esp_log.h"
#include "faces.h"
#include "neato_protocol.h"

static const char *TAG = "faces";

#define CMD_GAP_MS 25   /* gap between SetLCD commands; fw drains them on a
                           fixed 10 Hz tick regardless of send rate (queued,
                           zero drops), so this only bounds our send burst */
#define CONTRAST_DEF 45

/* Hardware-verified SetLCD reality (probed 2026-08-09 on the live robot):
 * HLine/VLine draw 1px BLACK full-span lines; FGWhite is a complete no-op
 * (ACKs, draws nothing, erases nothing); BGWhite/BGBlack are the only way
 * to remove ink. Faces are therefore grids: full-height eye pillars
 * crossed by full-width mouth bands, redrawn from scratch each time
 * (~20-40 cmds, ~2-4 s at the fw's 10 Hz SetLCD tick). Expression =
 * pillar width + band thickness/position. */

/* face_t, GEO[], EMAP[] are generated from neatobmo/faces.py — the single
 * source of truth shared with the Python cascade player and the web
 * preview. Edit faces.py and run tools/gen_faces_table.py. */
#include "faces_table.h"

#define MAX_CASCADE FACES_MAX_CASCADE

typedef struct {
    uint8_t n;
    face_t seq[MAX_CASCADE];
} cascade_t;

static QueueHandle_t s_q;
static face_t s_cur = F_NEUTRAL;
static uint32_t s_send_fails;   /* cumulative failed sends; /emote reports it */

/* ---- serial drawing primitives ----------------------------------------- */

/* Count the send result, then pace the burst (fw drains at 10 Hz). */
static void paced(esp_err_t err)
{
    if (err != ESP_OK) s_send_fails++;
    vTaskDelay(pdMS_TO_TICKS(CMD_GAP_MS));
}

static void draw_face(face_t f)
{
    const geo_t *g = &GEO[f];
    paced(neato_lcd_op("BGWhite"));
    paced(neato_lcd_op("FGBlack"));
    for (int c = g->l0; c <= g->l1; c++) paced(neato_lcd_vline(c));
    for (int c = g->r0; c <= g->r1; c++) paced(neato_lcd_vline(c));
    for (int r = g->b1lo; r <= g->b1hi; r++) paced(neato_lcd_hline(r));
    if (g->b2hi)
        for (int r = g->b2lo; r <= g->b2hi; r++) paced(neato_lcd_hline(r));
    s_cur = f;
}

/* ---- lingering animations (all verified primitives: contrast, bars,
 *      backlight, full fills) --------------------------------------------- */

static bool interrupted(void)
{
    return uxQueueMessagesWaiting(s_q) > 0;
}

static void anim_pulse(void)            /* love: heartbeat contrast throb */
{
    for (int i = 0; i < 4 && !interrupted(); i++) {
        paced(neato_lcd_contrast(60));
        vTaskDelay(pdMS_TO_TICKS(180));
        paced(neato_lcd_contrast(CONTRAST_DEF));
        vTaskDelay(pdMS_TO_TICKS(420));
    }
}

static void anim_dim(void)              /* sad: world slowly fades */
{
    for (int c = CONTRAST_DEF; c >= 20 && !interrupted(); c -= 5) {
        paced(neato_lcd_contrast(c));
        vTaskDelay(pdMS_TO_TICKS(150));
    }
    vTaskDelay(pdMS_TO_TICKS(600));
    paced(neato_lcd_contrast(CONTRAST_DEF));
}

static void anim_drowse(void)           /* sleepy: breathing fade + lights out */
{
    for (int i = 0; i < 2 && !interrupted(); i++) {
        for (int c = CONTRAST_DEF; c >= 15; c -= 5) {
            paced(neato_lcd_contrast(c));
            vTaskDelay(pdMS_TO_TICKS(90));
        }
        for (int c = 15; c <= CONTRAST_DEF; c += 5) {
            paced(neato_lcd_contrast(c));
            vTaskDelay(pdMS_TO_TICKS(90));
        }
    }
    if (!interrupted()) paced(neato_led("BacklightOff"));
}

static void anim_flicker(void)          /* party/laugh: strobe bars, then face */
{
    for (int i = 0; i < 6 && !interrupted(); i++) {
        paced(neato_lcd_op("BGWhite"));
        paced(neato_lcd_op("FGBlack"));
        paced(neato_lcd_op(i & 1 ? "HBars" : "VBars"));
        vTaskDelay(pdMS_TO_TICKS(220));
    }
    if (!interrupted()) draw_face(s_cur);
}

/* ---- cascade player ------------------------------------------------------ */

static void faces_task(void *arg)
{
    cascade_t c;
    while (1) {
        if (xQueueReceive(s_q, &c, portMAX_DELAY) != pdTRUE) continue;
        ESP_LOGI(TAG, "cascade of %d faces", c.n);
        neato_test_mode(true);               /* SetLCD is TestMode-only */
        vTaskDelay(pdMS_TO_TICKS(100));
        paced(neato_led("BacklightOn"));
        paced(neato_lcd_contrast(CONTRAST_DEF));
        for (int i = 0; i < c.n && !interrupted(); i++) {
            draw_face(c.seq[i]);
            vTaskDelay(pdMS_TO_TICKS(650));
            if (i < c.n - 1) {               /* eyelid flash between faces */
                paced(neato_led("BacklightOff"));
                vTaskDelay(pdMS_TO_TICKS(120));
                paced(neato_led("BacklightOn"));
            }
        }
        if (interrupted()) continue;
        switch (c.seq[c.n - 1]) {            /* linger on the last emotion */
        case F_LOVE:   anim_pulse();   break;
        case F_SAD:    anim_dim();     break;
        case F_SLEEPY: anim_drowse();  break;
        case F_PARTY:
        case F_LAUGH:  anim_flicker(); break;
        default: break;
        }
    }
}

/* ---- emoji -> face parsing (table generated, see faces_table.h) --------- */

static int parse_emojis(const char *text, face_t *out, int cap)
{
    int n = 0;
    for (const char *p = text; *p && n < cap; ) {
        int matched = 0;
        for (size_t i = 0; i < sizeof(EMAP) / sizeof(EMAP[0]); i++) {
            size_t len = strlen(EMAP[i].utf8);
            if (strncmp(p, EMAP[i].utf8, len) == 0) {
                out[n++] = EMAP[i].face;
                p += len;
                matched = 1;
                break;
            }
        }
        if (!matched) p++;
    }
    return n;
}

/* ---- HTTP ---------------------------------------------------------------- */

esp_err_t emote_post(httpd_req_t *req)
{
    char body[512];
    if (req->content_len >= (int)sizeof(body)) {
        httpd_resp_set_status(req, "413 Payload Too Large");
        return httpd_resp_sendstr(req, "emote body over 511 bytes\n");
    }
    int len = req->content_len;
    int received = 0;
    while (received < len) {
        int got = httpd_req_recv(req, body + received, len - received);
        if (got == HTTPD_SOCK_ERR_TIMEOUT) continue;
        if (got <= 0)
            return httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "recv fail");
        received += got;
    }
    body[received] = 0;

    cascade_t c;
    c.n = parse_emojis(body, c.seq, MAX_CASCADE);
    if (c.n == 0) { c.n = 1; c.seq[0] = F_HAPPY; }  /* plain text: just smile */
    xQueueOverwrite(s_q, &c);                        /* newest cascade wins */

    char resp[48];
    snprintf(resp, sizeof(resp), "OK %d faces, %u send errors\n", c.n,
             (unsigned)s_send_fails);
    return httpd_resp_sendstr(req, resp);
}

void faces_start(void)
{
    s_q = xQueueCreate(1, sizeof(cascade_t));       /* depth 1 + overwrite */
    xTaskCreate(faces_task, "faces", 4096, NULL, 4, NULL);
    ESP_LOGI(TAG, "face engine up: POST /emote with emoji text");
}
