# P6 capture — handoff (baud sweep SUPERSEDED by the 2026-08-11 review)

> **Status update (2026-08-11):** the baud-sweep approach this doc originally
> described is **abandoned**. Three adversarial reviews (`../captures/analysis/`)
> concluded the P6 baud is **115200** (not unknown), and that our reproducible
> silence is a **channel/wiring/capture-rig fault, not a baud mismatch.** This
> doc now reflects those conclusions. Do not resume sweeping.

## Goal
Tap the Neato XV-12 **P6 debug UART** (AT91SAM9XE, Cruz Rev113) with an
**ESP32-S3** and capture the cold-boot log over USB.

## What the review established (don't relitigate without new evidence)
- **Baud is 115200 8N1, 3.3 V — confirmed, not assumed.** RECESSIM captured
  human-readable boot text (`Neato Robotics XV-11 … NEROS Build …`) off this
  exact 4-pin P6 header at 115200. The exotic rates the old sweep tried
  (250000/460800/921600/74880) had zero supporting evidence.
- **Reproducible silence at every baud is evidence AGAINST a baud mismatch.**
  A live, correctly-wired line at the wrong baud gives garbage at every wrong
  rate and text at the right one — never total silence. The fault is the
  **channel**, ranked: loose/floating RX or lifted GND; the AT91 not printing;
  or a capture-rig defect (below).
- **Capture-rig defect (verified):** `esp32-body/sdkconfig.esp32s3` sets the
  **primary** console to **UART0 (GPIO43/44)** and the `/dev/cu.usbmodem` port we
  capture is only the **USB-Serial-JTAG secondary** console — a lossy 64-byte
  FIFO that drops fast bursts. The authoritative stream on UART0 is unrecorded.
- **The lost 8377-byte "gibberish" was almost certainly a self-test TX-injection
  artifact, not robot output** (the dual-reader bug can only drop whole bytes,
  and the self-test only ever sent plain ASCII).
- **STRATEGIC:** cold-boot P6 shows the **Neato app/bootloader banner, NOT the
  SAM-BA ROM monitor.** RomBOOT finds a valid image and jumps to it; `RomBOOT>`
  only appears on boot *failure* (which here means J3 ERASE = permanent brick).
  So **P6 alone is not a key-extraction route on a healthy board** — it confirms
  the tap and identifies the board, nothing more.

## THE NEXT STEP (before any reflash or baud change)
**Prove a signal physically exists on P6.3 (AT91_TXD) vs P6.4 GND during a
power-cycle.**
- **Best:** scope / logic analyzer / DMM on P6.3 vs GND. Live toggling → signal
  exists, read the bit period directly (expect ~8.68 µs/bit = 115200). Static
  idle-high → the AT91 isn't transmitting. Contact-dependent noise → loose wire.
- **No scope? Run the loopback self-test (never actually completed):** flash
  `-DP6_SELFTEST`, jumper `GPIO17→GPIO18`, confirm `P6-SELFTEST N` appears. That
  finally proves the ESP32 RX path works at all — which we have never verified.

## Recommended firmware change (pair with the signal check, needs a reflash)
Make USB-Serial-JTAG the **primary** console (`CONFIG_ESP_CONSOLE_USB_SERIAL_JTAG=y`)
or capture UART0 (GPIO43/44) — so we stop reading the lossy secondary FIFO. Also
consider guarding the USB host stack (`neato_usb_install`/`coli_mcu_start` in
`main.c`) out of the diagnostic build; it shares the USB PHY with the capture port.

## Wiring (proven correct — do NOT swap)
```
P6.4 GND      -> ESP32 GND         (essential; lifted GND = no data)
P6.3 AT91_TXD -> ESP32 GPIO18 (RX) (THE critical wire — robot output in)
P6.2 AT91_RXD -> ESP32 GPIO17 (TX) (only needed to send TO the robot; optional)
P6.1          -> unused
```
No `GPIO17↔GPIO18` loopback jumper except during the self-test. **Never touch J3
(ERASE = permanent brick).**

## Firmware build variants (`esp32-body`, env `esp32s3`, `debug_uart.c`)
- **(no flag)** normal bridge: mirrors P6 RX → USB console **and** TCP 3334.
- **`-DP6_SELFTEST`** transmits `P6-SELFTEST N` out GPIO17 (loopback proof).
- **`-DP6_BAUDSWEEP`** receive-only baud cycler — **now known unnecessary.**

## Capture tooling (USB-only)
`tools/p6_capture.py [PORT] [outfile]` run with
`/Library/Frameworks/Python.framework/Versions/3.14/bin/python3`. It appends to
timestamped `captures/` files (never truncates), opens **without** resetting the
ESP32, and takes exclusive port access. ESP32 = `/dev/cu.usbmodem*` (name changes
on replug). One reader per port. `pio device monitor` FAILS when backgrounded.
The boot burst is one-time → **power-cycle the Neato during capture.**

## Flashing (native-USB is flaky)
esptool errors (`0xa`, no-sync, invalid-format) recover with:
unplug ESP32 → hold BOOT → replug while holding BOOT → ~2 s → release → flash:
```
cd ~/Documents/neato/esp32-body
PLATFORMIO_BUILD_FLAGS=-DP6_SELFTEST pio run -e esp32s3 -t upload --upload-port <PORT>
```

## Evidence & analysis
- `../captures/README.md` — evidence timeline and the confounds.
- `../captures/analysis/at91-baud-research.md` — baud + SAM-BA/RomBOOT findings.
- `../captures/analysis/hypothesis-review.md` — refutation of the baud theory.
- `../captures/analysis/methodology-review.md` — capture-pipeline defects.

## Safety
Battery-disconnect while probing; ESD-safe; **never touch J3**; do this on the
scavenged Rev113 robot, not the working BMO body.
