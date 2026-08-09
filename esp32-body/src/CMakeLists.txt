idf_component_register(SRCS "main.c" "wifi_log.c"
                       INCLUDE_DIRS "."
                       PRIV_REQUIRES esp_wifi esp_netif esp_event nvs_flash lwip)
