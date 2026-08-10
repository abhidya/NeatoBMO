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

#define CMD_GAP_MS 25   /* pacing between SetLCD commands */
#define MAX_CASCADE 8

typedef enum {
    F_NEUTRAL, F_HAPPY, F_LAUGH, F_LOVE, F_SAD, F_SURPRISED,
    F_WINK, F_SLEEPY, F_ANGRY, F_PARTY, F_BLINK,
} face_t;

typedef struct { uint8_t x0, y0, x1, y1; } rect_t;

typedef struct {
    uint8_t n;
    face_t seq[MAX_CASCADE];
} cascade_t;

static QueueHandle_t s_q;

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

/* Filled rect from line segments, whichever orientation needs fewer cmds. */
static void rect(rect_t r)
{
    int h = r.y1 - r.y0 + 1, w = r.x1 - r.x0 + 1;
    if (h <= w)
        for (int y = r.y0; y <= r.y1; y++) cmdf("SetLCD HLine %d %d %d", y, r.x0, r.x1);
    else
        for (int x = r.x0; x <= r.x1; x++) cmdf("SetLCD VLine %d %d %d", x, r.y0, r.y1);
}

static void rects(const rect_t *r, int n)
{
    for (int i = 0; i < n; i++) rect(r[i]);
}

static void erase(const rect_t *r, int n)
{
    cmdf("SetLCD FGWhite");
    rects(r, n);
    cmdf("SetLCD FGBlack");
}

static void clear_screen(void)
{
    cmdf("SetLCD BGWhite");
    cmdf("SetLCD FGBlack");
}

/* ---- face vocabulary (coords match the approved simulator designs) ------ */

#define RC(X, Y, W, H) { (X), (Y), (X) + (W) - 1, (Y) + (H) - 1 }

static const rect_t EYES_DOT[]   = { RC(34,20,8,8),  RC(86,20,8,8) };
static const rect_t EYES_LINE[]  = { RC(32,23,12,3), RC(84,23,12,3) };
static const rect_t EYES_WIDE[]  = { RC(33,18,10,10), RC(85,18,10,10) };
static const rect_t EYES_HAT[]   = { RC(32,24,12,3), RC(34,21,8,3),
                                     RC(84,24,12,3), RC(86,21,8,3) };
static const rect_t EYES_LID[]   = { RC(32,18,12,3), RC(33,22,10,6),
                                     RC(84,18,12,3), RC(85,22,10,6) };
static const rect_t EYES_BROW[]  = { RC(32,16,12,3), RC(34,21,8,8),
                                     RC(84,16,12,3), RC(86,21,8,8) };
#define HEART(X, Y) RC((X)+1,(Y),3,2), RC((X)+6,(Y),3,2), RC((X),(Y)+2,10,3), \
                    RC((X)+2,(Y)+5,6,2), RC((X)+4,(Y)+7,2,1)
static const rect_t EYES_HEART[] = { HEART(31,17), HEART(83,17) };

static const rect_t M_FLAT[]  = { RC(52,44,25,3) };
static const rect_t M_SMILE[] = { RC(44,44,41,6), RC(48,50,33,3) };
static const rect_t M_GRIN[]  = { RC(44,42,41,10), RC(50,52,29,3) };
static const rect_t M_SAD[]   = { RC(52,50,25,3), RC(46,46,6,2), RC(77,46,6,2) };
static const rect_t M_O[]     = { RC(56,42,17,13) };
static const rect_t M_SLEEP[] = { RC(56,48,17,3) };

#define DRAW(a) rects(a, sizeof(a) / sizeof((a)[0]))

static void draw_face(face_t f)
{
    clear_screen();
    switch (f) {
    case F_HAPPY:     DRAW(EYES_HAT);   DRAW(M_SMILE); break;
    case F_LAUGH:     DRAW(EYES_HAT);   DRAW(M_GRIN);  break;
    case F_LOVE:      DRAW(EYES_HEART); DRAW(M_SMILE); break;
    case F_SAD:       DRAW(EYES_DOT);   DRAW(M_SAD);   break;
    case F_SURPRISED: DRAW(EYES_WIDE);  DRAW(M_O);     break;
    case F_WINK:      rect(EYES_DOT[0]); rect(EYES_LINE[1]); DRAW(M_SMILE); break;
    case F_SLEEPY:    DRAW(EYES_LID);   DRAW(M_SLEEP); break;
    case F_ANGRY:     DRAW(EYES_BROW);  DRAW(M_SAD);   break;
    case F_PARTY:     DRAW(EYES_WIDE);  DRAW(M_GRIN);  break;
    case F_BLINK:     DRAW(EYES_LINE);  DRAW(M_FLAT);  break;
    default:          DRAW(EYES_DOT);   DRAW(M_FLAT);  break;
    }
}

/* ---- sprite animations (delta-drawn, a few seconds after the cascade) --- */

static bool interrupted(void)
{
    return uxQueueMessagesWaiting(s_q) > 0;
}

static void anim_hearts(void)
{
    for (int step = 0; step < 14 && !interrupted(); step++) {
        int y = 40 - step * 3;
        rect_t h[] = { HEART(52, y) };
        rects(h, sizeof(h) / sizeof(h[0]));
        vTaskDelay(pdMS_TO_TICKS(60));
        if (y > 4) erase(h, sizeof(h) / sizeof(h[0]));
    }
}

static void anim_tear(void)
{
    for (int step = 0; step < 8 && !interrupted(); step++) {
        rect_t t[] = { RC(88, 30 + step * 3, 2, 4) };
        rects(t, 1);
        vTaskDelay(pdMS_TO_TICKS(80));
        erase(t, 1);
    }
}

static void anim_zzz(void)
{
    for (int step = 0; step < 10 && !interrupted(); step++) {
        int y = 34 - step * 3;
        if (y < 4) break;
        rect_t z[] = { RC(104, y, 8, 1), RC(108, y + 2, 3, 1), RC(104, y + 4, 8, 1) };
        rects(z, 3);
        vTaskDelay(pdMS_TO_TICKS(120));
        erase(z, 3);
    }
}

static void anim_confetti(void)
{
    for (int step = 0; step < 10 && !interrupted(); step++) {
        rect_t c[] = { RC(10 + (step * 37) % 100, 2 + (step * 13) % 12, 2, 2),
                       RC(20 + (step * 53) % 90,  2 + (step * 29) % 12, 2, 2) };
        rects(c, 2);
        vTaskDelay(pdMS_TO_TICKS(90));
        erase(c, 2);
    }
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
        for (int i = 0; i < c.n && !interrupted(); i++) {
            draw_face(c.seq[i]);
            vTaskDelay(pdMS_TO_TICKS(650));
            if (i < c.n - 1) {
                draw_face(F_BLINK);
                vTaskDelay(pdMS_TO_TICKS(120));
            }
        }
        if (interrupted()) continue;
        switch (c.seq[c.n - 1]) {            /* linger on the last emotion */
        case F_LOVE:   anim_hearts();   break;
        case F_SAD:    anim_tear();     break;
        case F_SLEEPY: anim_zzz();      break;
        case F_PARTY:
        case F_LAUGH:  anim_confetti(); break;
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
