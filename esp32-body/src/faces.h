/* BMO face engine: draws emoji-driven expression cascades on the Neato LCD.
 *
 *   POST /emote  body = UTF-8 text (a BMO reply); emojis found in it are
 *                mapped to faces and played as a cascade, ending in a short
 *                lingering animation (contrast throb / fade / breathe /
 *                bars strobe) for the final emotion. The hearts/tear/zzz/
 *                confetti sprites exist only in the bmo_web browser preview.
 *
 * Hardware-verified: SetLCD HLine/VLine draw 1px black full-span lines
 * only; FGWhite is a no-op (no selective erase); BGWhite/BGBlack are the
 * only erase. Faces are grids — full-height eye pillars crossed by
 * full-width mouth bands, fully redrawn each time (~20-40 cmds). Blinks
 * use the LCD backlight; lingering animations use contrast/bars/fills.
 */
#pragma once
#include "esp_http_server.h"

void faces_start(void);                 /* create the animation task */
esp_err_t emote_post(httpd_req_t *req); /* /emote handler (web.c registers) */
