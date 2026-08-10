/* TCP port 3334 <-> Neato P6 debug UART bridge.
 *
 * This is intentionally a raw byte tunnel. It is used to capture the AT91
 * boot log and interact with recovery/debug code while leaving the normal
 * Neato USB command bridge on port 3333 untouched.
 */
#include <errno.h>
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
    int server = make_server();
    if (server < 0) {
        ESP_LOGE(TAG, "TCP server failed: errno %d", errno);
        vTaskDelete(NULL);
        return;
    }
    ESP_LOGI(TAG, "P6 debug bridge on TCP %d (TX GPIO%d, RX GPIO%d)",
             DEBUG_TCP_PORT, DEBUG_UART_TX, DEBUG_UART_RX);

    uint8_t buf[1024];
    while (true) {
        int client = accept(server, NULL, NULL);
        if (client < 0) continue;
        ESP_LOGI(TAG, "debug client connected");

        while (true) {
            fd_set readfds;
            FD_ZERO(&readfds);
            FD_SET(client, &readfds);
            struct timeval wait = { .tv_sec = 0, .tv_usec = 10000 };
            int selected = select(client + 1, &readfds, NULL, NULL, &wait);
            if (selected < 0) break;
            if (selected > 0 && FD_ISSET(client, &readfds)) {
                int n = recv(client, buf, sizeof(buf), 0);
                if (n <= 0) break;
                if (uart_write_bytes(DEBUG_UART, buf, n) < 0) break;
            }

            int n = uart_read_bytes(DEBUG_UART, buf, sizeof(buf), 0);
            if (n > 0 && send(client, buf, n, 0) < 0) break;
        }
        close(client);
        ESP_LOGI(TAG, "debug client disconnected");
    }
}

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
    xTaskCreate(bridge_task, "debug_uart", 4096, NULL, 6, NULL);
}
