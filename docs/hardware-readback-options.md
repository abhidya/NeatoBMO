# Hardware readback options for understanding the Neato OS

## TL;DR

The only safe, evidence-backed path left for learning more about the stock XV OS is passive or non-erasing hardware acquisition:

1. Confirm the exact board and connector markings on the live P-family robot.
2. Passively capture the P6 debug UART at 115200 8N1.
3. If and only if the board actually drops into an AT91 ROM monitor, use official SAM-BA read commands only.
4. Treat JTAG as secondary, and do not use erase/write/unlock paths.

The current repo evidence says the USB-facing stock console does not provide the installed application bytes, and the readback gate is closed on firmware `2.4.15667`. That makes hardware-level capture the remaining route for exact plaintext, flash geometry, or recovery data.

## Evidence-backed options

### 1) Passive P6 DBGU capture

This is the first and best route.

Repo evidence:

- `README.md` documents the P6 wiring used for plaintext capture: `P6.2` robot RX to ESP32 GPIO17, `P6.3` robot TX to GPIO18, `P6.4` to GND, with the debug stream available at port `3334`.
- `FIRMWARE_ARCHIVE.md` says the next recovery gate is to verify the actual P-board markings and passively capture P6 DBGU at `115200 8N1`.
- `docs/usb-os-observability.md` records the P6 bridge as a dedicated debug-capture surface isolated from the command plane.

Why it matters:

- It can reveal boot logs, memory map hints, recovery prompts, and ROM monitor behavior without modifying the robot.
- It is the safest way to decide whether the board can be read through a documented bootloader path.

Evidence quality:

- Strong for wiring, baud rate, and safety boundaries.
- Weak-to-moderate for what the logs will contain; that depends on the exact boot state and board revision.

### 2) Official SAM-BA read commands, but only if the exact board reaches ROM monitor

This is conditional, not assumed.

Repo evidence:

- `FIRMWARE_ARCHIVE.md` explicitly says: verify the actual P-board markings, passively capture P6 DBGU, and only if the AT91 ROM monitor is reached, use official SAM-BA read commands.
- The same file says J3 erase is out of scope and any erase/program/unlock/write prompt is a stop condition.

Why it matters:

- If the board exposes a documented ROM monitor, SAM-BA read commands can give a clean raw capture path without destructive flashing.

Evidence quality:

- Strong on the allowed sequence and stop conditions.
- Unproven on this exact robot until the ROM monitor is actually observed on the live board.

### 3) JTAG as secondary investigation only

Repo evidence does not make JTAG the primary route.

Repo evidence:

- `FIRMWARE_ARCHIVE.md` says `P10 JTAG is not the primary route`.

Why it matters:

- JTAG may help with identification or recovery, but the current plan does not depend on it.
- It should stay behind passive UART and official bootloader readback.

Evidence quality:

- Weak. The repo only establishes that it is not the preferred path.

## Board-confirmation prerequisites

Do these before treating any readback as meaningful:

- Photograph the mainboard and verify the actual P-board markings.
- Confirm the robot is the same live target described in the archive: XV-12, P hardware family, mainboard `7.1`, firmware `2.4.15667`.
- Verify the P6 pin mapping before connecting anything.
- Capture the debug UART passively first; do not send erase, write, unlock, or flash commands.
- If the board does not show a ROM monitor, stop treating SAM-BA as available.

## Safety boundaries

Hard stop conditions:

- Any erase prompt.
- Any write or unlock path.
- Any J3 erase workflow.
- Any assumption that USB `readflash` or `dump` is a full firmware backup.

The repo already records that stock USB readback is unavailable for the installed application and sound region on `2.4.15667`. That means a safe hardware route must stay read-only until a real recovery path is proven.

## What counts as good evidence

Best evidence:

- Boot logs from passive P6 capture.
- A ROM-monitor prompt on the exact board.
- Duplicate raw reads that match byte-for-byte.
- Raw captures that include any required OOB/ECC data if NAND is involved.

Lower-value evidence:

- USB help text.
- `noburn` updater completion.
- One-off command echo without proof of readback.
- Assumptions based on compatible board families.

## Recommended sequence

1. Photograph and identify the board.
2. Attach passive P6 capture only.
3. Record boot and power-state transitions.
4. Look for a documented ROM monitor.
5. If present, use official SAM-BA reads only.
6. Save duplicate matching raw captures.
7. Only after that, reason about flash geometry, plaintext extraction, or recovery.

## Source files

- [`README.md`](../README.md)
- [`FIRMWARE_ARCHIVE.md`](../FIRMWARE_ARCHIVE.md)
- [`FIRMWARE_SOUND_PATCH.md`](../FIRMWARE_SOUND_PATCH.md)
- [`docs/usb-os-observability.md`](usb-os-observability.md)
