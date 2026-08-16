/* Serialized USB command and binary-transfer access to the Neato. */
#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include "esp_err.h"

#define NEATO_MAX_WAV_BYTES (512U * 1024U)
/* One full sound-bank image (770048 B) plus headroom; the hard cap for any
 * binary transfer relayed to the robot. */
#define NEATO_MAX_BINARY_BYTES (896U * 1024U)
#define NEATO_SOUND_BANK_BYTES 770048U
#define NEATO_USB_VID 0x2108U
#define NEATO_USB_PID 0x780BU

/* ---- lifecycle ----------------------------------------------------------
 * install: create transport state, install the USB host + CDC-ACM drivers
 * and spawn the USB library task.  Call once; peer USB class clients
 * (USB MSC) may register after this.
 * start: spawn the connection-manager task that waits for the robot,
 * opens only the observed Neato VID/PID, sets line coding, and invokes
 * on_connect after each (re)connect. Updater reboot may leave the device absent
 * until USB VBUS is physically or externally power-cycled; this task keeps
 * waiting but cannot manufacture enumeration from a missing device. */
esp_err_t neato_usb_install(void);
void neato_usb_start(void (*on_connect)(void));
bool neato_usb_connected(void);

/* ---- rx fan-out ---------------------------------------------------------
 * Every raw byte chunk received from the robot is passed to each
 * subscriber (from the USB rx task; keep handlers quick and non-blocking).
 * Subscribe during startup, before neato_usb_start(). */
typedef void (*neato_rx_fn)(const uint8_t *data, size_t len);
esp_err_t neato_rx_subscribe(neato_rx_fn fn);

/* ---- ASCII commands ----------------------------------------------------- */
esp_err_t neato_send(const char *cmd);

/* ---- binary transfers --------------------------------------------------- */
esp_err_t neato_send_binary(const char *cmd, const uint8_t *payload,
                            size_t payload_len, uint32_t timeout_ms);

/* Streaming binary transfer for payloads too large to stage in RAM
 * (no-PSRAM boards).  begin -> N x write -> end.  begin hands out a
 * transaction handle and holds the transport mutex until end; only the
 * handle holder can write.  The robot validates the additive transport
 * checksum; end(poison=true) sends a deliberately wrong checksum so the
 * receiver rejects a transfer we discovered to be bad (short HTTP body,
 * SHA mismatch) instead of committing it. */
typedef struct neato_txn neato_txn_t;
esp_err_t neato_binary_begin(const char *cmd, size_t payload_len,
                             uint32_t timeout_ms, neato_txn_t **out);
esp_err_t neato_binary_write(neato_txn_t *txn, const uint8_t *data,
                             size_t len, uint32_t timeout_ms);
esp_err_t neato_binary_end(neato_txn_t *txn, bool poison_checksum,
                           uint32_t timeout_ms);
