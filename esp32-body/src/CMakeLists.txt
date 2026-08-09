idf_component_register(SRCS "main.c" "wifi_log.c" "wifi_mgr.c" "web.c" "audio.c"
                       INCLUDE_DIRS "."
                       EMBED_TXTFILES "index.html" "wifi.html"
                       PRIV_REQUIRES esp_wifi esp_netif esp_event nvs_flash lwip
                                     esp_http_server app_update dns_server
                                     esp_driver_sdm esp_driver_gptimer)
