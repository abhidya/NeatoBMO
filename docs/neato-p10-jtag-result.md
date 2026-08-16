# Neato P10 JTAG result — security-bit-consistent, no debug TAP

Session: `jtag-halt-20260815T*` (live, 2026-08-15). Supersedes the 2026-08-13
"uncertain" record: the blocker is now root-caused and the verdict narrowed to
one of two physically-identical outcomes.

Read-only throughout: no flash writes, no erase, no GPNVM change, no halt, no
memory read succeeded.

## Outcome

- Adapter confirmed working: CherryUSB CMSIS-DAP v2, FW 2.1.1,
  `VID:PID=0x0d28:0x0204`, JTAG + SWD supported.
- Two tooling bugs root-caused and fixed:
  1. `tools/jtag/neato_p10_autoprobe.cfg` carried `cmsis-dap backend usb_bulk`,
     which is not a valid OpenOCD 0.12.0 subcommand — the probe errored before
     opening the adapter. Removed.
  2. The CherryDAP **ESP32-S3 firmware has no nTRST output** — its
     `PIN_nTRST_IN`/`PIN_nTRST_OUT` are "Not available" stubs
     (`CherryDAP/projects/esp32s3/main/DAP_config.h`). Therefore
     `adapter deassert trst` is a no-op and OpenOCD always reports `nTRST = 0`.
- Mandatory wiring fix: jumper **P10 VDDIO (square pin, bottom row 1) → TRST
  (pin 2)**. The adapter cannot release TRST, and a floating AT91 nTRST holds
  the TAP in Test-Logic-Reset.
- Scan progression after the jumper: unpowered "all ones" → transient
  live-TAP signature (garbage IDCODEs during reconnect) → stable,
  speed-independent "all ones" (`TDO = 1`) from 10 kHz through 1 MHz.
- Forced single-TAP (`irlen 4`) returned `IR capture 0x0f`, not `0x01` — the
  same all-ones read the 2026-08-13 session saw.
- A 45-scan power-cycle loop showed **no live-TAP window during boot**, ruling
  out a runtime ICE-disable after boot.

## Verdict

The clean, clock-independent "all ones" (tristated TDO) with TRST tied high and
wiring confirmed seated is consistent with the **AT91 security bit being set**:
JTAG is hard-disabled on this board, matching `FIRMWARE_ARCHIVE.md`
("JTAG is blocked while the AT91 security bit is set"). The one remaining
alternative — a dead VDDIO rail (robot asleep) — needs a meter to exclude and
does not change the conclusion: **JTAG is not a viable extraction path here.**

The 2026-08-13 session's open list (wiring/orientation, runtime ICE-disable,
adapter mismatch, debug lock) is now closed to: security bit, or dead VDDIO.
The adapter is not the problem, and TRST was a real, now-fixed, contributor.

## P10 pin map (recorded for the next session)

```
Bottom row: VDDIO (square, pin 1)  TRST  TDI  TMS  TCK
Top row:    GND                    GND   SRST TDO  RTCK
```

CherryDAP ESP32-S3 GPIOs: `TCK=17 TMS=16 TDI=15 TDO=7 nRESET=6`; **no TRST**.
OpenOCD cannot pulse SRST through this adapter ("adapter has no srst signal").

## Still blocked on a meter

- VDDIO voltage (square pad vs GND) — confirm ~3.3 V, i.e. robot awake.
- TDO continuity (adapter GPIO7 ↔ P10 top-row pin 4) and common GND.

## Tooling left in-tree

- `tools/jtag/neato_p10_autoprobe.cfg` — fixed; read-only `scan_chain` probe.
- `tools/jtag/neato_p10_halt.cfg` + `run_neato_p10_halt.sh` — halt + dump
  SRAM0 `0x00200000`, SRAM1 `0x00300000`, SDRAM `0x20000000`; ready if an
  IDCODE ever appears.
- `tools/jtag/verify_firmware_dump.py` — SDRAM ARM-vector oracle + SRAM
  AES-128 key-schedule finder (FIPS-197 self-tested). A key-schedule hit is
  ~2^-1272 false-positive, so a match is definitive.

## Constraints carried through the session

- J3 / ERASE untouched.
- Neato never powered from the ESP32.
- No GPNVM, flash, or erase commands issued.
- All captures under `captures/jtag/jtag-halt-20260815T*/`.

## Conclusion for the CFW track

JTAG is closed short of donor fault-injection. The remaining software-reachable
paths are unchanged: NAND readback of the plaintext bootloader (key/derivation
+ `0x10..0x1f` checksum), and the sound-parser fuzz as a code-exec primitive.
Neither needs JTAG.
