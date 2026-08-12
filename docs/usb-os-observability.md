# Neato XV OS Observability Matrix (Stock USB-facing OS)

Scope: static, repo-local evidence only. All paths are repo files.

Live-state update (2026-08-11): the exact Cruz-P 2.5 build 15893 is installed;
BACK still selects the separate factory 2.4 build 15667. The 2.5 help surface
is byte-identical for the probed commands, `PowerCycleCDC` remains absent, and
application `dump`/`readflash` returned no firmware bytes. The accepted factory
LCD happy-face commands produced no visible change on the stripped bench setup;
the cause remains unresolved.

## Stock command surface from live XV-12 protocol evidence

| Evidence source | Command/API evidence | OS behavior observed | Use status |
|---|---|---|---|
| `/Users/abdulrehmanbhidya/Documents/neato/neato_protocol_dump.txt` | `GetVersion`, sensor getters, `GetLifeStatLog`/`GetSysLog`, `Clean`, `DiagTest`, `SetMotor`, `SetLED`, `SetLCD`, `SetLDSRotation`, `SetWallFollower`, `SetSystemMode`, `TestMode`, `Upload` | Canonical command set/argument names and help text captured from live robot. `Upload` advertises `code|sound|LDS`, `size`, `noburn`, `readflash`, `reboot`, `xmodem`. | **Observed** |
| `/Users/abdulrehmanbhidya/Documents/neato/neato-driver-python/neato_driver.py` | `SystemModes` enum and command wrappers; `TestMode`, `SetSystemMode`, `SetMotor`, `SetLCD`, `SetLDSRotation` | Encodes expected client-side command vocabulary and defaults. | **Observed (client convention + wrapper checks)** |
| `/Users/abdulrehmanbhidya/Documents/neato/FIRMWARE_SOUND_PATCH.md` | `PlaySound File Size <n>\r` + ENQ/ACK framing | Not stock path; this is the target patch contract and should be treated as pending capability. | **Not stock / planned** |

## Mode/system transition behavior

| Area | Evidence | Conclusion |
|---|---|---|
| System mode command set | `neato_protocol_dump.txt` help for `SetSystemMode`: `Shutdown | Hibernate | Standby` only | Stock firmware exposes only 3 mode targets |
| TestMode gating | `neato_protocol_dump.txt` marks several controls test-only (`SetMotor`, `SetLED`, `SetLCD`, `SetLDSRotation`, `SetSystemMode`) | Commands are only accepted once `TestMode On` is set in normal operation |
| Rejected transition attempt | `FIRMWARE_ARCHIVE.md` (2026-08-10 status) reports `TestMode On` + `SetSystemMode PowerCycleCDC` rejected as unrecognized | Bootloader-style CDC transition via system mode was unavailable on live stock firmware |
| Possible stale enum mismatch | `neato-driver-python/neato_driver.py` still exposes `PowerCycle` enum value | Potential mismatch risk with live firmware; both 2.4 and 2.5 lack `PowerCycleCDC` in live help |

## USB transport + bridge observability

| Surface | Evidence | Behavior / limits |
|---|---|---|
| Direct USB (serial) | `/Users/abdulrehmanbhidya/Documents/neato/neatobmo/transport.py` and `/Users/abdulrehmanbhidya/Documents/neato/neatobmo/robot.py` | `send()` reads until ASCII terminator `0x1A`; `send_binary()` uses updater-style binary protocol with `<cmd> Size <payload+4>\r`, waits ENQ, writes payload+uint32le checksum, expects ACK |
| USB framing details | README + transport docs | Robot replies are ASCII, terminator `0x1A` (Ctrl-Z). Binary transfer markers are ENQ/ACK/NAK. |
| ESP32 web bridge | `/Users/abdulrehmanbhidya/Documents/neato/esp32-body/src/main.c`, `/Users/abdulrehmanbhidya/Documents/neato/esp32-body/src/web.c` | On connect sends proof commands (`TestMode On`, `SetLED`, `PlaySound 1`, `GetVersion`, heartbeat). `/ws` for raw text relay, `/speak` for WAV relay. Binary PlaySound relay over USB is only direct-USB path (`/speak` uses direct-USB `send_binary` and refuses on pure bridge). |
| Debug capture | `/Users/abdulrehmanbhidya/Documents/neato/esp32-body/src/debug_uart.c` | TCP 3334 bridges P6 UART lines to capture recovery/debug output; isolated from `/ws` command plane. |
| Read-only capture safety | `/Users/abdulrehmanbhidya/Documents/neato/tools/firmware_probe.py` | Explicitly read-only: allows `Upload dump/readflash` only and blocks reboot/reburn-style commands. |

## Read-only observability and non-destructive evidence gates

| Evidence | Mechanism | What it captures | Gap |
|---|---|---|---|
| `/Users/abdulrehmanbhidya/Documents/neato/tools/backup_neato.py` | USB `GetVersion`, `Help`, `Get*` queries + transcript + checksums | Configuration/calibration and help output snapshot | Explicitly not application flash dump |
| `/Users/abdulrehmanbhidya/Documents/neato/tools/firmware_probe.py` | Raw byte capture + optional xmodem receive of `readflash` | Determines whether XMODEM readback ever starts, captures raw replies | Readback appears unavailable for stock app writes in current tests |
| `/Users/abdulrehmanbhidya/Documents/neato/FIRMWARE_ARCHIVE.md` | Status log | Read-only snapshot and readback status tracked in single source | Readback gate is closed for application probes on stock 2.4 and 2.5, and for sound readflash |

## Stock command behavior confirmed in write/read experiments

| Experiment/tool | Result | Implication |
|---|---|---|
| `/Users/abdulrehmanbhidya/Documents/neato/tools/neato_sound_noburn_matrix.py` + logs referenced by `FIRMWARE_SOUND_PATCH.md` + `captures/20260811_B02_sound_noburn_exact_p6.log` | `Upload sound noburn` reaches ENQ and ends with terminator (no ACK/NAK); P6 shows `NoWrite`, full receive, then `nandflashWrite() fail - -1`; robot remains responsive | USB terminator marks completion only and does not prove integrity, acceptance, or a successful flash operation |
| `tools/neato_sound_burn_exact.py` | 8-byte checksum framing for `Upload sound` | `Upload sound` can be sent with ACK requirement and post-write validation in destructive workflows |
| `FIRMWARE_ARCHIVE.md` + `FIRMWARE_SOUND_PATCH.md` | `Upload sound readflash`/`sound dump` return no XMODEM payload; terminator only | Read/export of application/sound region over USB is blocked at current stock layer |

## Live stock behavior deltas (important for OS model)

| Topic | Known fact | Source evidence |
|---|---|---|
| Speaker slot availability | `PlaySound 0..20` sweep yields accepted: `0-3,6-10,19`; others `out of range` | `FIRMWARE_SOUND_PATCH.md` |
| Response format | ASCII text output with `0x1A` terminator | README + transport + protocol dump |
| Backup profile restore assumptions | `docs/SOUND_BANK_UPDATE.md` and `neatobmo/tts_bank.py` validate by post-write `GetVersion` + slot-sweep | Operational gate for destructive writes |

## Known unknowns / next safe checks

1. Whether any undocumented boot/recovery command is available at USB layer other than what `Help` exposes.  
2. Exact conditions under which `Upload` subcommands (`dump/readflash`) can become valid in non-stock patched firmware.  
3. Whether a different service/developer image exposes additional system modes;
   stock 2.4 and 2.5 do not.
4. Whether the 0x1A-terminated ASCII parser is complete enough when firmware emits mixed binary text + telemetry bursts outside command mode.  
5. Full command universe beyond `Help` output; the probed 2.4/2.5 help replies
   are byte-identical, but undocumented service commands may still exist.

Recommended next acquisition pass: make two independent raw external-NAND
captures with page/OOB/ECC preservation, preferably on a donor Cruz board.
USB/P6 repetition has reached diminishing returns unless a new firmware or
service mode supplies genuinely new evidence.
