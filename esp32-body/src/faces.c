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

#define CMD_GAP_MS 25   /* pacing between SetLCD commands (verified safe) */
#define MAX_CASCADE 8
#define LCD_SIZE 128
#define CONTRAST_DEF 45

/* Eye columns are fixed for every face; expressions vary only row bands.
 * SetLCD HLine/VLine draw full-span lines only, so faces are carved:
 * black eye pillars (VLine) masked down to bands by white rows (HLine),
 * mouth = full-width black band. A band narrower than the eye span is
 * impossible with full-span primitives (2x2 checkerboard argument). */
#define EYE_L0 32
#define EYE_L1 44
#define EYE_R0 84
#define EYE_R1 96

typedef enum {
    F_NEUTRAL, F_HAPPY, F_LAUGH, F_LOVE, F_SAD, F_SURPRISED,
    F_WINK, F_SLEEPY, F_ANGRY, F_PARTY, F_BLINK,
} face_t;

/* Row bands: [lo,hi] inclusive; left/right eyes may differ (wink)
 * only when one eye's band is nested inside the other's. */
typedef struct {
    uint8_t el0, el1;   /* left eye rows  */
    uint8_t er0, er1;   /* right eye rows */
    uint8_t m0, m1;     /* mouth rows     */
} geo_t;

static const geo_t GEO[] = {
    [F_NEUTRAL]   = { 20, 27, 20, 27, 44, 46 },
    [F_HAPPY]     = { 20, 27, 20, 27, 42, 50 },
    [F_LAUGH]     = { 18, 28, 18, 28, 40, 52 },
    [F_LOVE]      = { 18, 28, 18, 28, 42, 50 },
    [F_SAD]       = { 20, 27, 20, 27, 50, 52 },
    [F_SURPRISED] = { 16, 28, 16, 28, 38, 55 },
    [F_WINK]      = { 20, 27, 23, 25, 42, 50 },
    [F_SLEEPY]    = { 24, 26, 24, 26, 48, 50 },
    [F_ANGRY]     = { 22, 26, 22, 26, 50, 52 },
    [F_PARTY]     = { 16, 28, 16, 28, 40, 52 },
    [F_BLINK]     = { 23, 25, 23, 25, 44, 46 },
};

typedef struct {
    uint8_t n;
    face_t seq[MAX_CASCADE];
} cascade_t;

static QueueHandle_t s_q;
static geo_t s_cur;
static bool s_valid;    /* s_cur reflects what is on the LCD */

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

static void hline_span(uint8_t lo, uint8_t hi)
{
    for (int r = lo; r <= hi; r++) cmdf("SetLCD HLine %d", r);
}

static void vline_span(uint8_t lo, uint8_t hi)
{
    for (int c = lo; c <= hi; c++) cmdf("SetLCD VLine %d", c);
}

static bool in_band(int r, uint8_t lo, uint8_t hi) { return r >= lo && r <= hi; }

/* Full carve, ~150 cmds. Later commands overwrite earlier full-span
 * lines, which is what makes a nested wink possible: the thin eye's
 * pillars go down first, its extra rows are whited out, then the tall
 * eye's pillars land on top of those white rows. */
static void carve_face(const geo_t *g)
{
    cmdf("SetLCD BGWhite");
    cmdf("SetLCD FGBlack");
    vline_span(EYE_R0, EYE_R1);
    if (g->el0 != g->er0 || g->el1 != g->er1) {
        cmdf("SetLCD FGWhite");
        for (int r = g->el0; r <= g->el1; r++)
            if (!in_band(r, g->er0, g->er1)) cmdf("SetLCD HLine %d", r);
        cmdf("SetLCD FGBlack");
    }
    vline_span(EYE_L0, EYE_L1);
    cmdf("SetLCD FGWhite");
    for (int r = 0; r < LCD_SIZE; r++)
        if (!in_band(r, g->el0, g->el1) && !in_band(r, g->m0, g->m1))
            cmdf("SetLCD HLine %d", r);
    cmdf("SetLCD FGBlack");
    hline_span(g->m0, g->m1);
    s_cur = *g;
    s_valid = true;
}

/* Delta update: legal only when the new eye bands are nested inside the
 * current ones (rows can be whited out per-row, but a row can never be
 * turned into "black at eye columns only" without redrawing pillars).
 * Mouth rows toggle freely: full-width black or white HLines. */
static bool delta_ok(const geo_t *g)
{
    return s_valid &&
           g->el0 >= s_cur.el0 && g->el1 <= s_cur.el1 &&
           g->er0 >= s_cur.er0 && g->er1 <= s_cur.er1;
}

static void delta_face(const geo_t *g)
{
    cmdf("SetLCD FGWhite");
    for (int r = s_cur.el0; r <= s_cur.el1; r++)
        if (!in_band(r, g->el0, g->el1) && !in_band(r, g->m0, g->m1))
            cmdf("SetLCD HLine %d", r);
    for (int r = s_cur.er0; r <= s_cur.er1; r++)
        if (!in_band(r, g->er0, g->er1) && !in_band(r, g->m0, g->m1) &&
            !in_band(r, s_cur.el0, s_cur.el1))
            cmdf("SetLCD HLine %d", r);
    for (int r = s_cur.m0; r <= s_cur.m1; r++)
        if (!in_band(r, g->m0, g->m1)) cmdf("SetLCD HLine %d", r);
    cmdf("SetLCD FGBlack");
    for (int r = g->m0; r <= g->m1; r++)
        if (!in_band(r, s_cur.m0, s_cur.m1)) cmdf("SetLCD HLine %d", r);
    s_cur = *g;
}

static void draw_face(face_t f)
{
    const geo_t *g = &GEO[f];
    if (delta_ok(g)) delta_face(g);
    else carve_face(g);
}

/* ---- lingering animations (full-span native: contrast + bars + light) --- */

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
    s_valid = false;                    /* bars trashed the carve */
    if (!interrupted()) carve_face(&s_cur);
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
            if (i < c.n - 1) {
                draw_face(F_BLINK);          /* nested rows: cheap delta */
                vTaskDelay(pdMS_TO_TICKS(120));
            }
        }
        if (interrupted()) continue;
        switch (c.seq[c.n - 1]) {            /* linger on the last emotion */
        case F_LOVE:   anim_pulse();   break;
        case F_SAD:    anim_dim();     break;
        case F_SLEEPY: anim_drowse(); break;
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
