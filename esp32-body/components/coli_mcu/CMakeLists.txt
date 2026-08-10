idf_component_register(
    SRCS "gemma.c" "gemma_generate.c" "generate.c" "model_format.c" "moe.c" "olmoe.c" "ops.c" "q4_matvec.c" "spm.c" "store_file.c" "tokenizer.c" "usb_store.c"
    INCLUDE_DIRS "include"
    REQUIRES usb_host_msc fatfs esp_timer
)
