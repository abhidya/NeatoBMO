# neatoos Phase A

Offline Phase A artifacts for a minimal ARM926 canary and deterministic Neato
firmware probe generation.

This subtree is intentionally conservative:

- no hardware access;
- no upload, burn, flash, or erase commands;
- no proprietary firmware blobs;
- all generated probe outputs are derived from caller-supplied input files.

## Layout

- `src/startup.S` — ARM exception vector table, stack setup, and reset entry.
- `src/main.c` — DBGU canary that repeatedly emits exactly `NEATOOS RAW V0`
  plus CRLF.
- `include/at91sam9xe_min.h` — minimal AT91SAM9XE DBGU register definitions.
- `linker/linker.ld` — small RAM linker script for structural build checks.
  Its `0x20000000` origin is an offline hypothesis, not a proven Neato loader
  address.
- `tools/probe_generator.py` — deterministic `.enc` probe image generator.
- `manifests/phase-a.json` — deliverable manifest and safety constraints.
- `manifests/reference-images.json` — metadata-only external reference image
  locations and hashes; no firmware bytes are committed.
- `Makefile` — build/test helpers.

## Build

The canary build requires an ARM EABI toolchain:

```sh
make
```

By default this uses `arm-none-eabi-gcc`. Override `CROSS_COMPILE` when needed:

```sh
make CROSS_COMPILE=/opt/toolchains/gcc-arm-none-eabi/bin/arm-none-eabi-
```

The output is written under `build/` and is ignored by git:

- `build/neatoos-raw.elf`
- `build/neatoos-raw.bin`
- `build/neatoos-raw.map`
- `build/neatoos-raw.lst`

## Probe generator

The generator never decrypts, flashes, uploads, or patches firmware. It creates
two `.enc` probe containers from one raw payload:

- `neatoos-structural-probe.bin.enc` — a synthetic Neato-like header: length, format byte `0x02`,
  ASCII `neato`, zero fill, deterministic 16-byte experimental field, then the
  raw body with 512-byte page padding.
- `neatoos-reference-header-probe.bin.enc` — the exact 512-byte header from a caller-supplied,
  SHA-256-verified Cruz-P 2.5 reference image, then the same raw body with
  512-byte page padding. Its declared length deliberately remains the vendor
  original from the reference header and is reported as a mismatch when it does
  not equal the raw body size.
- `neatoos-reference-header-full-length-probe.bin.enc` — the same first 1,024
  bytes as the short reference-header probe, followed by deterministic SHA-256
  filler to the exact 805,888-byte stock-image size.

All three outputs are explicitly labeled `NOT ENCRYPTED` and `NOT AUTHENTICATED` in
their JSON manifests. They are offline probes, not installable firmware.

Example:

```sh
python3 neatoos/tools/probe_generator.py build/neatoos-raw.bin \
  --reference /Volumes/2TB/neato-firmware-archive/sources/Neato-XV-Series-Cruz-Rev-113-Update/OriginalVorwerkFirmwareFiles/Firmware25/XV11App.15893.P.bin.enc \
  --reference-sha256 e1a31ef56e2b617a4056d70c308aab26b7e2cd95e679cb982d697ff4f089c697 \
  --out-dir build/probes
```

## Verification

Run the offline test suite:

```sh
python3 -m pytest tests/test_neatoos_phase_a.py
```

## Serial compatibility slice

The first clean-room serial slice is a freestanding C parser shared with a host
simulator. It implements:

- ASCII commands terminated by either LF or CRLF;
- CRLF replies terminated by byte `0x1A`;
- `GetVersion` with a deliberately non-vendor `NEATOOS` identity;
- a truthful reduced `Help` surface;
- stateful `TestMode On` and `TestMode Off`, with no actuator side effects.

Build the simulator and send it commands on standard input:

```sh
make -C neatoos host-sim
printf 'GetVersion\nHelp\n' | neatoos/build/neato-serial-sim
```

The stock captures do not contain the response bodies for `TestMode On/Off` or
an unknown command. The current echo-plus-terminator behavior for both cases is
therefore provisional and labeled by tests; it must be checked against a future
read-only transcript capture.

## Hardware result (2026-08-12)

Four controlled representations were written once to application NAND
`0x10000` with simultaneous P6 capture:

- raw ARM: written, then `Illegal size for application.`;
- structural envelope: written, then the same size rejection;
- copied stock 2.5 header plus raw body: written, passed the explicit size gate
  and reached `Starting app`, but printed no sentinel and did not enumerate USB.
- full-length copied header plus controlled body: all 805,892 framed bytes were
  written and acknowledged, then the bootloader printed
  `Checksum error in application binary` and loaded factory NEROS 15667.

Arbitrary ARM execution is therefore **not proven**. The full-length experiment
eliminated the short-write tail confound and exposed an application checksum
gate. See `docs/neatoos-execution-probe.md` for hashes, raw captures, inference
limits, and recovery evidence. Exact stock 2.5 was restored successfully and a
fresh USB identity check confirmed software 2.5.15893.

A later single-variable experiment preserved the exact stock encrypted body
and flipped only bit 0 at clear-header offset `0x18`. The full write succeeded,
but the bootstrap printed `Checksum error in application binary` and loaded
factory software. This proves the opaque `0x10..0x1f` field participates in
application acceptance; it does not identify the field as a checksum rather
than a MAC, nonce, or other validated metadata. Exact stock 2.5 was restored
again and verified over P6 and USB.

## Clean-room v0 scope

NeatoOS v0 is a serial/API compatibility target, not a proprietary-source copy.
It is limited to behavior exposed through the documented USB serial protocol:

- exact ASCII command parsing and `0x1A` response termination;
- `GetVersion`, `Help`, `TestMode`, and read-only `Get*` commands first;
- serial-exposed LDS, motor, LED/LCD, system-mode, and sound commands after
  their stock transcripts are captured;
- actuator commands parsed but side-effect gated until simulator and safety
  tests pass.

Cloud, schedules, autonomous navigation, filesystem behavior, and unexposed
peripherals are outside v0. JTAG/P10, NAND readback, and bootloader work remain
a separate donor-board track; J3/ERASE remains forbidden.

## P10 JTAG status

The first non-destructive Cruz P10 JTAG session did not expose a stable TAP.
CherryDAP/OpenOCD worked at the Mac adapter layer, and P10 TDO was
target-power-sensitive, but installed, factory, and P6-triggered scans all
returned no TAP. This keeps NeatoOS on the clean-room serial/API track until a
separate readout path is proven. See `docs/neato-p10-jtag-result.md`.

## Version-aware USB compatibility

The 2026-08-15 stock transition matrix adds three complete read-only USB
snapshots. Stock 2.5.15893 and 2.7.16621 expose identical probed `Help`, upload,
sound, configuration, and system-mode help replies. Stock 3.1.17844 omits
`GetLifeStatLog`, `GetSysLog`, `SetDistanceCal`, `SetWallFollower`, `dump`, and
`xmodem`. NeatoOS must therefore make command availability version/capability
driven rather than hard-code a single superset.

The ESP32 NeatoBMO controller must also treat USB CDC as removable state.
Updater reboot did not reliably re-enumerate VID:PID `2108:780B`; reopening the
old serial pathname cannot recover a device absent from the bus. Rediscover by
VID:PID plus `GetVersion` identity, tolerate pathname changes, and expose a
manual reconnect or controllable-hub VBUS-cycle recovery path.
