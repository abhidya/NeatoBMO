# Cruz P10 JTAG session analysis — 2026-08-13/15

## Outcome

P10 did not expose a usable JTAG TAP in this session.  CherryDAP enumerated and
OpenOCD repeatedly reached JTAG interrogation, but every completed probe ended
as stuck TDO: early target-off/incorrect-state scans were all-zeroes, while the
validated full-wiring, boot-triggered, factory-triggered, and sound-write-window
scans were all-ones.  No stable IDCODE, IR length, ARM926 target, halt,
register read, SRAM read, or internal-flash read was observed.

This supports the conservative classification:

- ESP32-S3 + CherryDAP was a functional Mac-visible CMSIS-DAP JTAG adapter.
- P10 TDO/power-state behavior changed with target state, so the footprint is
  electrically active at least as a signal/pad path.
- P10 did not yield a valid TAP under installed NEROS 2.5.15893, factory NEROS
  2.4.15667, cold-boot P6-trigger timing, or concurrent vendor sound-bank
  write/restore observation, or stock Cruz-P 2.5, 2.7, and 3.1 application
  transition observations.
- The evidence does not distinguish AT91 security-bit blocking from runtime ICE
  disablement, reset/TRST gating, or remaining wiring/signal-integrity issues.

## Evidence counts

| Observation | Repeated? | Interpretation | Alternatives | Confidence |
|---|---:|---|---|---|
| Adapter enumerates as CherryUSB CMSIS-DAP and OpenOCD opens it | 157 of 158 OpenOCD logs | ESP32-S3 adapter path works from macOS/OpenOCD | One short failed CLI invocation only printed `cmsis-dap` help | High |
| Stable IDCODE | 0 | No TAP IDCODE measured | TAP disabled, not routed, reset-gated, wiring/SI issue | High for “not observed” |
| Stable IR length | 0 | No IR length measured | Same as above | High for “not observed” |
| All-zero scans | 8 | TDO low in early/target-off state | target unpowered, wrong state, disconnected TDO | Medium |
| All-one scans | 149 | TDO high/pulled high during validated scan conditions | disabled TAP, missing drive, reset/TRST issue, wiring/SI | High for observation; low for cause |
| Core identified | 0 | ARM926 debug not reached | No TAP gate passed | High |
| Halt/register read | 0 | Not attempted because no stable TAP | Required gate absent | High |
| SRAM/internal flash read | 0 | Not attempted because no halt/register gate | Required gate absent | High |
| Reset-assisted attach | 0 | Not tested | SRST/TRST behavior not re-reviewed for this wiring | High |
| Vendor sound-bank write window | 22 scans | Same all-ones result before/during/after an authorized vendor sound restore | Upload window may not affect ICE/JTAG | Medium |
| Stock Cruz-P 2.7 write window | 42 scans | Same all-ones result before/during/after an authorized exact stock 2.7 application transition | Updater/write window may not affect ICE/JTAG | Medium |
| Stock Cruz-P 3.1 write window | 18 scans | Same all-ones result before/during/after a successful 3.1 write | Automatic USB return failed; physical cable reconnect exposed healthy 3.1.17844 | High for scan result; medium for reconnect cause |
| Stock Cruz-P 2.5 write windows | 32 scans | Same all-ones result before/during/after two successful stock 2.5 restores | Upload window may not affect ICE/JTAG | Medium |
| Repeated stock Cruz-P 2.7 window | 14 scans | Same all-ones result during the repeat used for USB-surface capture | Upload window may not affect ICE/JTAG | Medium |

## Software/window collection

The later operator-requested compatible-software collection has two parts.

First, it restored the exact vendor default sound bank.  The result JSON records:

- command `Upload sound`;
- image SHA-256 `d3969779a6a195812d72b6859454de004ea45beefdee6f1b5c50a2632564b64a`;
- USB close `061a`;
- healthy `GetVersion` before and after;
- matching `PlaySound 0..20` slot map after restore.

This is a sound-region restoration/health observation.  It is not evidence that
P10 JTAG became available, and it does not publish proprietary sound bytes.

Second, it performed one exact allowlisted stock Cruz-P 2.7 application write.
`firmware-27-write-result.json` records command `Upload code reboot`, image
SHA-256 `2e6033b1ef5440bed949de20e89563d7cb3dda41e0eb5e371c9d86dceeb1633f`,
USB close `06`, and a post-write identity change from `Software,2,5,15893` to
`Software,2,7,16621` on the same `WTD41611DD,0037829,P` mainboard 7.1.
`openocd-firmware-27-*` scans around that transition remained all-ones/no TAP.
The proprietary application bytes remain outside Git.

A following exact stock Cruz-P 3.1 write returned ACK. The bounded automatic
re-enumeration check in `firmware-31-write-result.json` timed out, but this was
not an application failure: physically reconnecting the Neato USB cable exposed
the same robot running `Software,3,1,17844`, mainboard 7.1. The complete
read-only snapshot under `usb-snapshot-31/` independently preserves that
identity and command surface. The associated `openocd-firmware-31-*` logs
remained all-ones/no TAP.

The robot was then restored to exact stock 2.5, moved once more to 2.7 to
capture a comparable USB snapshot, and finally restored to 2.5.15893.
`firmware-27b-write-result.json` and `firmware-25b-write-result.json` preserve
the final two one-shot ACKed transitions. The first 3.1-to-2.5 result could not
be written because the Mac data volume reached zero free space after the robot
had already verified as 2.5; no automatic retry was performed. A later explicit
2.7-to-2.5 run produced a complete result file.

The USB snapshots show that 2.5 and 2.7 have byte-identical `Help` and
`Help Upload` replies. 3.1 differs: its `Help` reply omits `GetLifeStatLog`,
`GetSysLog`, `SetDistanceCal`, and `SetWallFollower`; its `Help Upload` omits
`dump` and `xmodem`. `PlaySound`, `SetConfig`, and `SetSystemMode` help remained
byte-identical across all three versions. `usb-surface-comparison.json` records
the exact reply hashes.

For the ESP32 NeatoBMO controller, updater reboot is not sufficient as the only
USB recovery mechanism. Both 3.1 and the final 2.5 transition required a
physical Neato USB disconnect/reconnect before macOS recreated VID:PID
`2108:780B`. Controller code must tolerate disappearance, rediscover by USB
identity rather than a fixed `/dev/cu.usbmodem*` name, and expose a manual or
hub-controlled VBUS-cycle recovery path. Restarting a serial reader alone
cannot revive a device absent from the USB bus.

The P6 recorder files for application-transition windows contain only their
session headers (74 bytes each). Concurrent CMSIS-DAP bulk scans and CherryDAP
CDC capture therefore did not preserve transition UART bytes; USB result files
and read-only snapshots, not those P6 files, prove the application identities.

## Product implications

1. **Clean-room rewrite:** the versioned USB snapshots and exact reply hashes
   are usable behavioral evidence without publishing proprietary firmware.
   They prove that command discovery must be capability-driven: 2.5 and 2.7
   match for the probed surface, while 3.1 removes specific diagnostics and
   upload verbs.
2. **Debug and filesystem/read access:** this session did not obtain a TAP,
   halt the ARM926, read registers, or expose SRAM, internal flash, NAND, or a
   filesystem. P6 remains a passive diagnostic-log source, and USB help remains
   the strongest reproducible read-only interface collected here.
3. **Patching the installed robot:** exact-hash stock transitions through
   2.5, 2.7, and 3.1 are now empirically verified on this unit. That establishes
   a guarded recovery/update transport, not arbitrary patched-image acceptance.
   Image-integrity generation, signing/envelope constraints, and safe custom
   payload execution remain separate unsolved gates; 3.2 remains forbidden.

## Gaps and limits

- VDDIO was not measured; the operator waived the meter step.
- Series resistors were not installed; the operator waived them.
- No new photograph was preserved in the repo.
- P10 VDDIO, TRST, SRST, and RTCK remained disconnected for the committed JTAG
  result.
- No TAP declaration was trusted because no auto-probe IDCODE/IR recommendation
  appeared.
- No halt, resume, register, SRAM, internal-flash, ERASE, GPNVM, flash, or NAND
  OpenOCD command was run.
- Factory fallback identity was not re-verified after the final 2.5 restore.

## Decision

The strongest supported conclusion is: **P10 is not presently available as a
usable read-only ARM926 debug interface with the tested ESP32-S3/CherryDAP
wiring and command set.**

The strongest rejected interpretation is: **“AT91 security bit is proven set.”**
The observed all-ones/all-zeroes scans are compatible with security, but also
with reset gating, TRST/RTCK assumptions, signal integrity, pinout error, or
runtime ICE disablement.
