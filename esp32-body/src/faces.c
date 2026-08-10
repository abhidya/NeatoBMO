#include <stdarg.h>
#include <stdio.h>
#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"
#include "esp_log.h"
#include "faces.h"

void neato_send(const char *cmd); /* main.c */

static const char *TAG = "faces";

#define CMD_GAP_MS 25   /* gap between SetLCD commands; fw drains them on a
                           fixed 10 Hz tick regardless of send rate (queued,
                           zero drops), so this only bounds our send burst */
#define MAX_CASCADE 8
#define CONTRAST_DEF 45

/* Hardware-verified SetLCD reality (probed 2026-08-09 on the live robot):
 * HLine/VLine draw 1px BLACK full-span lines; FGWhite is a complete no-op
 * (ACKs, draws nothing, erases nothing); BGWhite/BGBlack are the only way
 * to remove ink. Faces are therefore grids: full-height eye pillars
 * crossed by full-width mouth bands, redrawn from scratch each time
 * (~20-40 cmds, ~2-4 s at the fw's 10 Hz SetLCD tick). Expression =
 * pillar width + band thickness/position. */

typedef enum {
    F_NEUTRAL, F_HAPPY, F_LAUGH, F_LOVE, F_SAD, F_SURPRISED,
    F_WINK, F_SLEEPY, F_ANGRY, F_PARTY, F_BLINK,
} face_t;

/* Column spans for the two eye pillars + up to two row bands for the
 * mouth ([lo,hi] inclusive; second band unused when b2hi == 0). */
typedef struct {
    uint8_t l0, l1, r0, r1;     /* left / right pillar columns */
    uint8_t b1lo, b1hi;         /* mouth band 1 rows */
    uint8_t b2lo, b2hi;         /* mouth band 2 rows (0,0 = none) */
} geo_t;

static const geo_t GEO[] = {
    [F_NEUTRAL]   = { 34, 42, 86, 94, 44, 46, 0, 0 },
    [F_HAPPY]     = { 34, 42, 86, 94, 42, 50, 0, 0 },
    [F_LAUGH]     = { 32, 44, 84, 96, 40, 52, 0, 0 },
    [F_LOVE]      = { 32, 44, 84, 96, 42, 50, 0, 0 },
    [F_SAD]       = { 34, 42, 86, 94, 52, 54, 0, 0 },
    [F_SURPRISED] = { 30, 46, 82, 98, 36, 55, 0, 0 },
    [F_WINK]      = { 32, 44, 89, 91, 42, 50, 0, 0 },
    [F_SLEEPY]    = { 37, 39, 89, 91, 48, 50, 0, 0 },
    [F_ANGRY]     = { 32, 44, 84, 96, 48, 50, 54, 56 },
    [F_PARTY]     = { 30, 46, 82, 98, 40, 42, 46, 52 },
    [F_BLINK]     = { 37, 39, 89, 91, 44, 46, 0, 0 },
};

typedef struct {
    uint8_t n;
    face_t seq[MAX_CASCADE];
} cascade_t;

static QueueHandle_t s_q;
static face_t s_cur = F_NEUTRAL;

/* ---- serial drawing primitives ----------------------------------------- */

static void cmdf(const char *fmt, ...)
{
    char buf[48];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(buf, sizeof(buf), fmt, ap);
    va_end(ap);
    neato_send(buf);
    vTaskDelay(pdMS_TO_TICKS(CMD_GAP_MS));
}

static void draw_face(face_t f)
{
    const geo_t *g = &GEO[f];
    cmdf("SetLCD BGWhite");
    cmdf("SetLCD FGBlack");
    for (int c = g->l0; c <= g->l1; c++) cmdf("SetLCD VLine %d", c);
    for (int c = g->r0; c <= g->r1; c++) cmdf("SetLCD VLine %d", c);
    for (int r = g->b1lo; r <= g->b1hi; r++) cmdf("SetLCD HLine %d", r);
    if (g->b2hi)
        for (int r = g->b2lo; r <= g->b2hi; r++) cmdf("SetLCD HLine %d", r);
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
        cmdf("SetLCD Contrast 60");
        vTaskDelay(pdMS_TO_TICKS(180));
        cmdf("SetLCD Contrast %d", CONTRAST_DEF);
        vTaskDelay(pdMS_TO_TICKS(420));
    }
}

static void anim_dim(void)              /* sad: world slowly fades */
{
    for (int c = CONTRAST_DEF; c >= 20 && !interrupted(); c -= 5) {
        cmdf("SetLCD Contrast %d", c);
        vTaskDelay(pdMS_TO_TICKS(150));
    }
    vTaskDelay(pdMS_TO_TICKS(600));
    cmdf("SetLCD Contrast %d", CONTRAST_DEF);
}

static void anim_drowse(void)           /* sleepy: breathing fade + lights out */
{
    for (int i = 0; i < 2 && !interrupted(); i++) {
        for (int c = CONTRAST_DEF; c >= 15; c -= 5) {
            cmdf("SetLCD Contrast %d", c);
            vTaskDelay(pdMS_TO_TICKS(90));
        }
        for (int c = 15; c <= CONTRAST_DEF; c += 5) {
            cmdf("SetLCD Contrast %d", c);
            vTaskDelay(pdMS_TO_TICKS(90));
        }
    }
    if (!interrupted()) cmdf("SetLED BacklightOff");
}

static void anim_flicker(void)          /* party/laugh: strobe bars, then face */
{
    for (int i = 0; i < 6 && !interrupted(); i++) {
        cmdf("SetLCD BGWhite");
        cmdf("SetLCD FGBlack");
        cmdf(i & 1 ? "SetLCD HBars" : "SetLCD VBars");
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
        neato_send("TestMode On");           /* SetLCD is TestMode-only */
        vTaskDelay(pdMS_TO_TICKS(100));
        cmdf("SetLED BacklightOn");
        cmdf("SetLCD Contrast %d", CONTRAST_DEF);
        for (int i = 0; i < c.n && !interrupted(); i++) {
            draw_face(c.seq[i]);
            vTaskDelay(pdMS_TO_TICKS(650));
            if (i < c.n - 1) {               /* eyelid flash between faces */
                cmdf("SetLED BacklightOff");
                vTaskDelay(pdMS_TO_TICKS(120));
                cmdf("SetLED BacklightOn");
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

/* ---- emoji -> face parsing ---------------------------------------------- */

typedef struct { const char *utf8; face_t face; } emap_t;

static const emap_t EMAP[] = {
    { "\xF0\x9F\x98\x80", F_HAPPY },  /* 😀 */
    { "\xF0\x9F\x98\x84", F_HAPPY },  /* 😄 */
    { "\xF0\x9F\x98\x8A", F_HAPPY },  /* 😊 */
    { "\xF0\x9F\x99\x82", F_HAPPY },  /* 🙂 */
    { "\xF0\x9F\x98\x82", F_LAUGH },  /* 😂 */
    { "\xF0\x9F\xA4\xA3", F_LAUGH },  /* 🤣 */
    { "\xF0\x9F\x98\x8D", F_LOVE },   /* 😍 */
    { "\xE2\x9D\xA4",     F_LOVE },   /* ❤ (with or without VS16) */
    { "\xF0\x9F\x92\x96", F_LOVE },   /* 💖 */
    { "\xF0\x9F\x92\x9A", F_LOVE },   /* 💚 */
    { "\xF0\x9F\x98\xA2", F_SAD },    /* 😢 */
    { "\xF0\x9F\x98\xAD", F_SAD },    /* 😭 */
    { "\xF0\x9F\x98\x9E", F_SAD },    /* 😞 */
    { "\xE2\x98\xB9",     F_SAD },    /* ☹ */
    { "\xF0\x9F\x98\xAE", F_SURPRISED }, /* 😮 */
    { "\xF0\x9F\x98\xB2", F_SURPRISED }, /* 😲 */
    { "\xF0\x9F\x98\xB1", F_SURPRISED }, /* 😱 */
    { "\xF0\x9F\x98\x89", F_WINK },   /* 😉 */
    { "\xF0\x9F\x98\xB4", F_SLEEPY }, /* 😴 */
    { "\xF0\x9F\x92\xA4", F_SLEEPY }, /* 💤 */
    { "\xF0\x9F\x98\xA0", F_ANGRY },  /* 😠 */
    { "\xF0\x9F\x98\xA4", F_ANGRY },  /* 😤 */
    { "\xF0\x9F\x8E\x89", F_PARTY },  /* 🎉 */
    { "\xF0\x9F\x8E\xAE", F_PARTY },  /* 🎮 */
    { "\xE2\x9C\xA8",     F_PARTY },  /* ✨ */
    { "\xF0\x9F\xA4\x96", F_NEUTRAL },/* 🤖 */
};

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
    int len = req->content_len < (int)sizeof(body) - 1 ? req->content_len : (int)sizeof(body) - 1;
    int got = httpd_req_recv(req, body, len);
    if (got <= 0) return httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "recv fail");
    body[got] = 0;

    cascade_t c;
    c.n = parse_emojis(body, c.seq, MAX_CASCADE);
    if (c.n == 0) { c.n = 1; c.seq[0] = F_HAPPY; }  /* plain text: just smile */
    xQueueOverwrite(s_q, &c);                        /* newest cascade wins */

    char resp[32];
    snprintf(resp, sizeof(resp), "OK %d faces\n", c.n);
    return httpd_resp_sendstr(req, resp);
}

void faces_start(void)
{
    s_q = xQueueCreate(1, sizeof(cascade_t));       /* depth 1 + overwrite */
    xTaskCreate(faces_task, "faces", 4096, NULL, 4, NULL);
    ESP_LOGI(TAG, "face engine up: POST /emote with emoji text");
}
