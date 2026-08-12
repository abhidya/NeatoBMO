# Hardware readback options for understanding the Neato OS

## TL;DR

The passive P6 phase and stock-2.5 experiment are complete. The remaining
evidence-backed path for learning more about the XV firmware is raw external
memory acquisition:

1. Prefer a matching donor Cruz/P board rather than the working BMO board.
2. Identify the external NAND part and exact page/OOB/ECC geometry.
3. Take two independent complete raw reads including OOB and bad-block markers.
4. Use the known sound bank written at logical region `0x400000` to validate the
   raw-to-logical mapping, then classify installed/factory application regions.
5. Treat protected internal-flash/JTAG work as a later branch only if external
   NAND still contains encrypted application data. Never use J3 ERASE.

P6 proved the boot/update paths but never exposed SAM-BA. Exact Cruz-P 2.5 was
then installed successfully from factory mode; its USB help remained identical
to 2.4, raw dump/readflash returned no application bytes, and XMODEM never
started. BACK still boots the separate factory 2.4.15667 image. Hardware-level
capture therefore remains the route to plaintext, flash geometry, or a
byte-restorable backup.

## Evidence-backed options

### 1) Passive P6 DBGU capture

This route is complete on the live board.

Repo evidence:

- `README.md` documents the passive wiring: `P6.3` robot TX to ESP32 GPIO18
  and `P6.4` to GND, with `P6.2`/GPIO17 left disconnected unless transmit
  access is explicitly needed.
- `FIRMWARE_ARCHIVE.md` records the completed 115200 8N1 P6 capture and the
  next duplicate external-NAND acquisition gate.
- `docs/usb-os-observability.md` records the P6 bridge as a dedicated debug-capture surface isolated from the command plane.

Why it matters:

- It revealed boot selection, reset causes, updater types/options, and the NAND
  write regions `0x10000` (application) and `0x400000` (sound).
- It established that normal cold boot reaches the Neato bootloader/application,
  not a ROM monitor or read-capable shell.

Evidence quality:

- Strong for wiring, baud rate, and safety boundaries.
- Strong for the observed boot and updater behavior; negative for direct
  plaintext or SAM-BA access.

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
- Confirm the robot is the same live target described in the archive: XV-12, P
  hardware family, mainboard `7.1`, installed `2.5.15893`, factory `2.4.15667`.
- Verify the P6 pin mapping before connecting anything.
- Capture the debug UART passively first; do not send erase, write, unlock, or flash commands.
- If the board does not show a ROM monitor, stop treating SAM-BA as available.

## Safety boundaries

Hard stop conditions:

- Any erase prompt.
- Any write or unlock path.
- Any J3 erase workflow.
- Any assumption that USB `readflash` or `dump` is a full firmware backup.

The repo records that stock USB readback is unavailable from both the former
installed 2.4 application and installed 2.5. That means a hardware acquisition
route must stay read-only until geometry and restoration are proven.

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

1. Obtain or identify a matching donor Cruz/P board.
2. Photograph and identify the external NAND part and nearby bus/test points.
3. Select a reader that preserves raw pages, OOB, ECC, and bad-block markers.
4. Save two independent complete captures and compare stable regions.
5. Anchor the geometry using the exact known sound bank at logical `0x400000`.
6. Classify the application regions as plaintext, encrypted, compressed, or
   transformed before choosing a patch or internal-flash attack path.

## Source files

- [`README.md`](../README.md)
- [`FIRMWARE_ARCHIVE.md`](../FIRMWARE_ARCHIVE.md)
- [`FIRMWARE_SOUND_PATCH.md`](../FIRMWARE_SOUND_PATCH.md)
- [`docs/usb-os-observability.md`](usb-os-observability.md)
