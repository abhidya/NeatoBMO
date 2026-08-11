# P6 capture — handoff (baud sweep SUPERSEDED by the 2026-08-11 review)

> **GOAL ACHIEVED (2026-08-11 14:01 PDT):** a clean AT91 cold-boot log was
> captured over P6 at 115200 8N1 in `../captures/p6_1786482063.log`. It contains
> `Neato Robotics XV-11/XEB V10:45:23`, `NEROS Build 15667 Oct 28 2011
> 11:25:50`, and LDS runtime `V2.6.15295`. The successful passive wiring was
> P6.4→ESP32 GND and P6.3→GPIO18, with P6.2/GPIO17 disconnected. No additional
> sweep or capture-rig change is needed.

> **Recovery-path finding (2026-08-11 14:47 PDT):** holding BACK through a
> cold power-on makes the bootloader print `Loading factory application` rather
> than `Loading installed application`; raw evidence is
> `../captures/20260811_A02_hold_back_cold_boot.log`. Holding START instead
> loaded the installed application and then caused repeated software resets;
> see `../captures/20260811_A01_hold_start_cold_boot.log`. Neither entered
> `RomBOOT>` or SAM-BA.

> **Combined-button finding:** holding START+BACK also selected the factory
> application. `../captures/20260811_A03_hold_start_back_cold_boot.log`
> contains four boot banners (two `PowerUp`, two `Software`) and one intervening
> undecoded high-bit interval. The operator reported likely releasing and
> retrying the buttons near that interval. Do not interpret it as firmware data
> without a controlled reproduction or independent electrical validation.

> **Controlled repeat:**
> `../captures/20260811_A03R1_hold_start_back_repeat.log` showed three
> factory-application boots while START+BACK remained held, followed by an
> installed-application software boot after button release. Only a small
> undecoded interval at initial power transition remained, supporting
> startup/contact noise rather than firmware content.

> **Runtime UI finding:** controlled capture
> `../captures/20260811_A04R1_normal_back_start.log` showed no P6 text for LCD
> idle timeout, a BACK click, or START waking the LCD and playing a sound. On the
> stripped motherboard/LCD/button-panel bench setup, P6 appears boot/reset
> focused rather than a live UI-event log. Repeat on an assembled robot before
> generalizing this negative result.

> **USB observability finding:** Neato enumerated separately as USB CDC
> `/dev/cu.usbmodem1431201` (`2108:780B`). P6 printed `USB Connected` on cable
> attachment, but did not mirror `GetVersion` or the guarded read-only snapshot
> commands. The snapshot confirmed XV-12 `WTD41611DD-0037829-P`, software
> `2.4.15667`, mainboard `7.1`, and live help advertising the `noburn` upload
> option. See `../captures/20260811_B00_neato_usb_attach.log`,
> `../captures/20260811_B01_usb_readonly_snapshot_p6.log`, and
> `../captures/usb-snapshots/`.

> **Upload-path finding:** exact vendor-bank `Upload sound noburn` produced P6
> diagnostics `SOUND`, `BLAST`, `Size 770052`, and `NoWrite`, followed by
> `Upload complete - 770052 bytes received` and `Upload fail -
> nandflashWrite() fail - -1`. USB exposed only a `0x1A` terminator and the
> post-transfer identity remained unchanged. See
> `../captures/20260811_B02_sound_noburn_exact_p6.log`. Do not treat a no-burn
> USB terminator as validation or success.

> **Corruption control:** a one-bit mutation at sound-bank byte 4108 produced
> the same USB `0x1A` and the same P6 `NoWrite`/receive-complete/NAND-failure
> sequence; post-transfer identity remained healthy. Evidence:
> `../captures/20260811_B03_sound_noburn_onebit_p6.log`. Thus `noburn` does not
> validate content integrity or compatibility.

> **Application no-burn finding (2.5):** verified Cruz-P build 15893 produced
> `CODE`, `BLAST`, `Size 805892`, `NoWrite`, full receive, then the same
> `nandflashWrite() fail - -1`. USB returned `0x1A`; installed identity remained
> 2.4.15667. Evidence:
> `../captures/20260811_B04R1_code_2.5_15893P_noburn_p6.log`. This path does not
> establish decryption or compatibility validation.

> **Status update (2026-08-11):** the baud-sweep approach this doc originally
> described is **abandoned**. Three adversarial reviews (`../captures/analysis/`)
> concluded the P6 baud is **115200** (not unknown), and that our reproducible
> silence is a **channel/wiring/capture-rig fault, not a baud mismatch.** This
> doc now reflects those conclusions. Do not resume sweeping.

> **Field update (2026-08-11 13:50 PDT):** GPIO17→GPIO18 loopback passed.
> `../captures/p6_1786481459.log` contains clean `P6-SELFTEST 47` through `70`,
> proving UART1 RX/TX and the host capture path. Noise immediately before the
> correct jumper was attached occurred with GPIO18 floating. The active Mac tty
> is also a WCH USB-to-UART bridge (`1A86:55D3`) carrying primary UART0, not the
> USB-JTAG secondary console. Next: remove loopback, flash the normal 115200
> bridge, wire P6.4→GND and P6.3→GPIO18, arm capture, and power-cycle Neato once.

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

## Completed procedure (loopback and cold boot captured)

The successful passive fixed-115200 procedure was:

1. Remove the GPIO17→GPIO18 loopback jumper.
2. Flash the normal bridge build (no diagnostic build flag).
3. With Neato power disconnected, wire P6.4→ESP32 GND and
   P6.3→ESP32 GPIO18. Leave P6.2/GPIO17 disconnected for this passive test.
4. Start one exclusive `p6_capture.py` reader, then power-cycle the Neato once.
5. Preserve `../captures/p6_1786482063.log`; the expected Neato application
   banner was captured cleanly.

The original discriminator was to **prove a signal physically exists on P6.3
(AT91_TXD) vs P6.4 GND during a power-cycle.**
- **Best:** scope / logic analyzer / DMM on P6.3 vs GND. Live toggling → signal
  exists, read the bit period directly (expect ~8.68 µs/bit = 115200). Static
  idle-high → the AT91 isn't transmitting. Contact-dependent noise → loose wire.
- **No scope? Run the loopback self-test (never actually completed):** flash
  `-DP6_SELFTEST`, jumper `GPIO17→GPIO18`, confirm `P6-SELFTEST N` appears. That
  finally proves the ESP32 RX path works at all — which we have never verified.

## Console-path correction

The active Mac tty is a WCH USB-to-UART bridge (VID:PID `1A86:55D3`) connected
to primary UART0 GPIO43/44. Its `cu.usbmodem...` name was misleading. Making
USB-Serial-JTAG primary is not required for this physical connector. Guarding
the USB host stack out of diagnostic builds remains useful cleanup, but it is
not a prerequisite for the P6 capture.

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
