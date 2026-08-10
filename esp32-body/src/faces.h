/* BMO face engine: draws emoji-driven expression cascades on the Neato LCD.
 *
 *   POST /emote  body = UTF-8 text (a BMO reply); emojis found in it are
 *                mapped to faces and played as a cascade, ending in a short
 *                sprite animation (hearts / tear / zzz / confetti) for the
 *                final emotion.
 *
 * SetLCD HLine/VLine only draw full-span lines, so faces are carved:
 * black eye pillars (VLine) masked to row bands by white rows (HLine),
 * mouth = full-width band. Full carve ~150 cmds; nested-band changes
 * (blink) go through a cheap per-row delta. Lingering animations use
 * full-span-native effects (contrast throb/fade, bars, backlight).
 */
#pragma once
#include "esp_http_server.h"

void faces_start(void);                 /* create the animation task */
esp_err_t emote_post(httpd_req_t *req); /* /emote handler (web.c registers) */
