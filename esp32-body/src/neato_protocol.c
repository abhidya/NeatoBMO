/* Neato command vocabulary: formats one command, sends it via neato_usb. */
#include <stdio.h>
#include "neato_protocol.h"
#include "neato_usb.h"

static esp_err_t sendf(const char *fmt, int value)
{
    char buf[48];
    int n = snprintf(buf, sizeof(buf), fmt, value);
    if (n <= 0 || n >= (int)sizeof(buf)) return ESP_ERR_INVALID_ARG;
    return neato_send(buf);
}

static esp_err_t sendf_str(const char *fmt, const char *value)
{
    char buf[48];
    int n = snprintf(buf, sizeof(buf), fmt, value);
    if (n <= 0 || n >= (int)sizeof(buf)) return ESP_ERR_INVALID_ARG;
    return neato_send(buf);
}

esp_err_t neato_test_mode(bool on)
{
    return neato_send(on ? "TestMode On" : "TestMode Off");
}

esp_err_t neato_led(const char *mode)
{
    return sendf_str("SetLED %s", mode);
}

esp_err_t neato_play_slot(int id)
{
    return sendf("PlaySound %d", id);
}

esp_err_t neato_get_version(void)
{
    return neato_send("GetVersion");
}

esp_err_t neato_get_charger(void)
{
    return neato_send("GetCharger");
}

esp_err_t neato_lcd_op(const char *op)
{
    return sendf_str("SetLCD %s", op);
}

/* One number only: a trailing number would be parsed as Contrast and
 * written to NAND (see neato_protocol.h). */
esp_err_t neato_lcd_hline(int row)
{
    return sendf("SetLCD HLine %d", row);
}

esp_err_t neato_lcd_vline(int col)
{
    return sendf("SetLCD VLine %d", col);
}

esp_err_t neato_lcd_contrast(int value)
{
    return sendf("SetLCD Contrast %d", value);
}
