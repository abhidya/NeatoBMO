/* TCP port 3334 <-> Neato P6 debug UART bridge.
 *
 * This is intentionally a raw byte tunnel. It is used to capture the AT91
 * boot log and interact with recovery/debug code while leaving the normal
 * Neato USB command bridge on port 3333 untouched.
 */
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <string.h>
#include <sys/select.h>

#include "driver/uart.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "lwip/sockets.h"

#include "debug_uart.h"

#define DEBUG_UART       UART_NUM_1
#define DEBUG_UART_TX    17
#define DEBUG_UART_RX    18
#define DEBUG_UART_BAUD  115200
#define DEBUG_TCP_PORT   3334

static const char *TAG = "debug_uart";

static int make_server(void)
{
    int server = socket(AF_INET, SOCK_STREAM, IPPROTO_IP);
    if (server < 0) return -1;
    int one = 1;
    setsockopt(server, SOL_SOCKET, SO_REUSEADDR, &one, sizeof(one));
    struct sockaddr_in addr = {
        .sin_family = AF_INET,
        .sin_port = htons(DEBUG_TCP_PORT),
        .sin_addr.s_addr = htonl(INADDR_ANY),
    };
    if (bind(server, (struct sockaddr *)&addr, sizeof(addr)) < 0 ||
        listen(server, 1) < 0) {
        close(server);
        return -1;
    }
    return server;
}

static void bridge_task(void *arg)
{
    /* Single UART reader that fans out to two sinks so bytes are never split:
     *   - the USB console (stdout), always — capture over the USB cable alone;
     *   - a TCP client on 3334, when one is connected — the over-Wi-Fi path.
     * Accept is non-blocking so console mirroring never stalls waiting for a
     * TCP client. If the TCP server can't come up, console mirroring still runs.
     */
    int server = make_server();
    if (server < 0) {
        ESP_LOGE(TAG, "TCP server failed: errno %d — USB console mirror only", errno);
    } else {
        int flags = fcntl(server, F_GETFL, 0);
        fcntl(server, F_SETFL, flags | O_NONBLOCK);
    }
    ESP_LOGI(TAG, "P6 debug bridge on USB console + TCP %d (TX GPIO%d, RX GPIO%d)",
             DEBUG_TCP_PORT, DEBUG_UART_TX, DEBUG_UART_RX);

    uint8_t buf[1024];
    int client = -1;
    while (true) {
        if (server >= 0 && client < 0) {
            int c = accept(server, NULL, NULL);
            if (c >= 0) {
                client = c;
                ESP_LOGI(TAG, "debug client connected");
            }
        }

        if (client >= 0) {
            fd_set readfds;
            FD_ZERO(&readfds);
            FD_SET(client, &readfds);
            struct timeval z = { .tv_sec = 0, .tv_usec = 0 };
            int selected = select(client + 1, &readfds, NULL, NULL, &z);
            if (selected > 0 && FD_ISSET(client, &readfds)) {
                int n = recv(client, buf, sizeof(buf), 0);
                if (n <= 0) {
                    close(client);
                    client = -1;
                    ESP_LOGI(TAG, "debug client disconnected");
                } else {
                    uart_write_bytes(DEBUG_UART, buf, n);
                }
            }
        }

        /* Block up to 20 ms for UART bytes; this also paces the accept poll. */
        int n = uart_read_bytes(DEBUG_UART, buf, sizeof(buf), pdMS_TO_TICKS(20));
        if (n > 0) {
            fwrite(buf, 1, n, stdout);
            fflush(stdout);
            if (client >= 0 && send(client, buf, n, 0) < 0) {
                close(client);
                client = -1;
                ESP_LOGI(TAG, "debug client disconnected");
            }
        }
    }
}

#ifdef P6_SELFTEST
static void selftest_task(void *arg)
{
    unsigned n = 0;
    char msg[48];
    while (true) {
        int len = snprintf(msg, sizeof(msg), "P6-SELFTEST %u\r\n", n++);
        uart_write_bytes(DEBUG_UART, msg, len);
        vTaskDelay(pdMS_TO_TICKS(1000));
    }
}
#endif

#ifdef P6_BAUDSWEEP
/* Diagnostic: RX is alive but the byte stream is garbage -> wrong baud. Cycle
 * DEBUG_UART through common rates, ~1.2 s each, printing a banner before each
 * window. Power-cycle the Neato during the sweep; whichever banner is followed
 * by readable text is the real P6 rate. Receive-only: never transmits. */
static void baudsweep_task(void *arg)
{
    static const int bauds[] = {
        115200, 57600, 38400, 19200, 9600, 4800,
        230400, 460800, 921600, 250000, 74880,
    };
    uint8_t buf[512];
    while (true) {
        for (size_t i = 0; i < sizeof(bauds) / sizeof(bauds[0]); i++) {
            uart_set_baudrate(DEBUG_UART, bauds[i]);
            uart_flush_input(DEBUG_UART);
            char hdr[48];
            int hl = snprintf(hdr, sizeof(hdr), "\r\n\r\n==== BAUD %d ====\r\n", bauds[i]);
            fwrite(hdr, 1, hl, stdout);
            fflush(stdout);
            for (int t = 0; t < 60; t++) {  /* ~1.2 s window */
                int n = uart_read_bytes(DEBUG_UART, buf, sizeof(buf), pdMS_TO_TICKS(20));
                if (n > 0) {
                    fwrite(buf, 1, n, stdout);
                    fflush(stdout);
                }
            }
        }
    }
}
#endif

void debug_uart_start(void)
{
    const uart_config_t config = {
        .baud_rate = DEBUG_UART_BAUD,
        .data_bits = UART_DATA_8_BITS,
        .parity = UART_PARITY_DISABLE,
        .stop_bits = UART_STOP_BITS_1,
        .flow_ctrl = UART_HW_FLOWCTRL_DISABLE,
        .source_clk = UART_SCLK_DEFAULT,
    };
    ESP_ERROR_CHECK(uart_driver_install(DEBUG_UART, 8192, 8192, 0, NULL, 0));
    ESP_ERROR_CHECK(uart_param_config(DEBUG_UART, &config));
    ESP_ERROR_CHECK(uart_set_pin(DEBUG_UART, DEBUG_UART_TX, DEBUG_UART_RX,
                                 UART_PIN_NO_CHANGE, UART_PIN_NO_CHANGE));
#ifndef P6_BAUDSWEEP
    /* The sweep must be the sole UART reader; two readers split the byte stream. */
    xTaskCreate(bridge_task, "debug_uart", 4096, NULL, 6, NULL);
#endif

#ifdef P6_SELFTEST
    /* Diagnostic build only: transmit a known string out TX (GPIO17) once a
     * second. Jumper GPIO17->GPIO18 and it loops back through the bridge's RX
     * mirror into the USB capture, proving the ESP32 UART + mirror + capture
     * chain end-to-end with the Neato removed. NEVER ship this build to a
     * robot — it injects bytes into the AT91 RX line. */
    xTaskCreate(selftest_task, "p6_selftest", 2048, NULL, 5, NULL);
#endif

#ifdef P6_BAUDSWEEP
    xTaskCreate(baudsweep_task, "p6_baudsweep", 4096, NULL, 5, NULL);
#endif
}
