#pragma once

#include <stdbool.h>

#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

/**
 * Install the MSC class client and start the low-priority M0 storage probe.
 *
 * The application must install the singleton USB Host Library and service
 * usb_host_lib_handle_events() before calling this function.
 */
esp_err_t coli_mcu_start(void);

/** True only while a FAT USB MSC volume is mounted and usable. */
bool coli_mcu_storage_ready(void);

#ifdef __cplusplus
}
#endif
