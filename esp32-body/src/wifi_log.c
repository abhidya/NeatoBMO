#include <string.h>
#include <stdio.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"
#include "lwip/sockets.h"
#include "wifi_log.h"
#include "wifi_mgr.h"

static int s_client = -1;
static vprintf_like_t s_orig_vprintf;

static int tee_vprintf(const char *fmt, va_list args)
{
    char buf[512];
    int len = vsnprintf(buf, sizeof(buf), fmt, args);
    if (len > 0 && s_client >= 0) {
        int n = len < (int)sizeof(buf) ? len : (int)sizeof(buf) - 1;
        if (send(s_client, buf, n, MSG_DONTWAIT) < 0) {
            close(s_client);
            s_client = -1;
        }
    }
    return s_orig_vprintf ? s_orig_vprintf(fmt, args) : len;
}

/* also used by main.c to mirror raw Neato bytes */
void net_log_raw(const uint8_t *data, size_t len)
{
    if (s_client >= 0) {
        if (send(s_client, data, len, MSG_DONTWAIT) < 0) {
            close(s_client);
            s_client = -1;
        }
    }
}

static void log_server_task(void *arg)
{
    int srv = socket(AF_INET, SOCK_STREAM, 0);
    struct sockaddr_in addr = {
        .sin_family = AF_INET,
        .sin_port = htons(LOG_TCP_PORT),
        .sin_addr.s_addr = htonl(INADDR_ANY),
    };
    int one = 1;
    setsockopt(srv, SOL_SOCKET, SO_REUSEADDR, &one, sizeof(one));
    bind(srv, (struct sockaddr *)&addr, sizeof(addr));
    listen(srv, 1);
    while (1) {
        int c = accept(srv, NULL, NULL);
        if (c < 0) continue;
        if (s_client >= 0) close(s_client);
        s_client = c;
        const char *hello = "== neato body log ==\r\n";
        send(c, hello, strlen(hello), 0);
    }
}

/* Raw command bridge: every line received on TCP 3333 goes to the Neato.
 * Replies come back on this same socket (and the log socket). */
void neato_send(const char *cmd); /* from main.c */
static int s_cmd_client = -1;

void net_cmd_reply(const uint8_t *data, size_t len)
{
    if (s_cmd_client >= 0) {
        if (send(s_cmd_client, data, len, MSG_DONTWAIT) < 0) {
            close(s_cmd_client);
            s_cmd_client = -1;
        }
    }
}

static void cmd_server_task(void *arg)
{
    int srv = socket(AF_INET, SOCK_STREAM, 0);
    struct sockaddr_in addr = {
        .sin_family = AF_INET,
        .sin_port = htons(3333),
        .sin_addr.s_addr = htonl(INADDR_ANY),
    };
    int one = 1;
    setsockopt(srv, SOL_SOCKET, SO_REUSEADDR, &one, sizeof(one));
    bind(srv, (struct sockaddr *)&addr, sizeof(addr));
    listen(srv, 1);
    char line[128];
    int fill = 0;
    while (1) {
        int c = accept(srv, NULL, NULL);
        if (c < 0) continue;
        if (s_cmd_client >= 0) close(s_cmd_client);
        s_cmd_client = c;
        fill = 0;
        char ch;
        while (recv(c, &ch, 1, 0) == 1) {
            if (ch == '\n' || ch == '\r') {
                if (fill > 0) {
                    line[fill] = 0;
                    neato_send(line);
                    fill = 0;
                }
            } else if (fill < (int)sizeof(line) - 1) {
                line[fill++] = ch;
            }
        }
        close(c);
        if (s_cmd_client == c) s_cmd_client = -1;
    }
}

void wifi_log_start(void)
{
    wifi_mgr_start(); /* STA from NVS/secrets, or setup AP -- either way we get an IP */

    s_orig_vprintf = esp_log_set_vprintf(tee_vprintf);
    xTaskCreate(log_server_task, "log_srv", 4096, NULL, 5, NULL);
    xTaskCreate(cmd_server_task, "cmd_srv", 4096, NULL, 5, NULL);
}
