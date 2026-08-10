/* BMO face engine: draws emoji-driven expression cascades on the Neato LCD.
 *
 *   POST /emote  body = UTF-8 text (a BMO reply); emojis found in it are
 *                mapped to faces and played as a cascade, ending in a short
 *                sprite animation (hearts / tear / zzz / confetti) for the
 *                final emotion.
 *
 * Drawing uses SetLCD HLine/VLine segments; sprites move via FGWhite
 * selective erase so a frame costs ~10-25 serial commands, not a full
 * screen redraw.
 */
#pragma once
#include "esp_http_server.h"

void faces_start(void);                 /* create the animation task */
esp_err_t emote_post(httpd_req_t *req); /* /emote handler (web.c registers) */
