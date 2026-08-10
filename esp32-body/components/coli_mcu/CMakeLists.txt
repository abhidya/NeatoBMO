idf_component_register(
    SRCS "model_format.c" "moe.c" "ops.c" "q4_matvec.c" "store_file.c" "tokenizer.c" "usb_store.c"
    INCLUDE_DIRS "include"
    REQUIRES usb_host_msc fatfs esp_timer
)
