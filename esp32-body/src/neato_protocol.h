/* Neato command vocabulary: thin, typed formatters over neato_usb.
 *
 * Each helper formats one ASCII command and sends it through the serialized
 * transport; nothing here waits for or parses the reply.  Binary transfers
 * ("PlaySound File", "Upload sound") stay at the neato_usb level.
 */
#pragma once

#include <stdbool.h>
#include "esp_err.h"

esp_err_t neato_test_mode(bool on);           /* TestMode On|Off */
esp_err_t neato_led(const char *mode);        /* SetLED <mode> */
esp_err_t neato_play_slot(int id);            /* PlaySound <id> */
esp_err_t neato_get_version(void);            /* GetVersion */
esp_err_t neato_get_charger(void);            /* GetCharger */

/* SetLCD <op> where op takes no numeric argument (BGWhite, FGBlack,
 * HBars, VBars, ...). */
esp_err_t neato_lcd_op(const char *op);

/* HARDWARE FOOTGUN: SetLCD HLine/VLine take exactly ONE number.  Any
 * trailing number on the line is parsed as a Contrast value and WRITTEN
 * TO NAND.  These helpers exist so no caller ever formats a second
 * number onto an HLine/VLine command. */
esp_err_t neato_lcd_hline(int row);           /* SetLCD HLine <row> */
esp_err_t neato_lcd_vline(int col);           /* SetLCD VLine <col> */
esp_err_t neato_lcd_contrast(int value);      /* SetLCD Contrast <value> */
