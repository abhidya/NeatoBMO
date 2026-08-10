idf_component_register(SRCS "main.c" "wifi_log.c" "wifi_mgr.c" "web.c" "debug_uart.c"
                            "neato_audio.c" "faces.c"
                       INCLUDE_DIRS "."
                       EMBED_TXTFILES "index.html" "wifi.html"
                       PRIV_REQUIRES esp_wifi esp_netif esp_event nvs_flash lwip
                                     esp_http_server app_update dns_server
                                     esp_driver_uart)
