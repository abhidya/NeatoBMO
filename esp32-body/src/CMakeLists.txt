idf_component_register(SRCS "main.c" "wifi_log.c" "web.c"
                       INCLUDE_DIRS "."
                       EMBED_TXTFILES "index.html"
                       PRIV_REQUIRES esp_wifi esp_netif esp_event nvs_flash lwip
                                     esp_http_server app_update)
