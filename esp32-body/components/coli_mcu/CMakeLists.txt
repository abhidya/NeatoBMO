idf_component_register(
    SRCS "model_format.c" "q4_matvec.c" "store_file.c" "usb_store.c"
    INCLUDE_DIRS "include"
    REQUIRES usb_host_msc fatfs esp_timer
)
