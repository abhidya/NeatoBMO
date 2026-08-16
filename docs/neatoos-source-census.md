# NeatoOS source census

Research date: 2026-08-12

## Outcome

Thirty-four relevant GitHub repositories were triaged across Cruz firmware,
XV serial control, LDS protocol/drivers, custom controllers, connected Botvac
firmware, and cloud integrations. Public source provides enough information to
build a clean-room serial/API compatibility layer and LDS support. It does not
provide the Cruz firmware AES key, the `0x10..0x1f` integrity-field algorithm,
the robot bootstrap source, a Cruz-specific JTAG recipe, or a verified custom
application repacker.

Research clones and large artifacts belong under
`/Volumes/2TB/neato-github-research/`, outside this repository.

## Highest-value Cruz/XV sources

| Repository | Use | Boundary |
|---|---|---|
| [NoahJaehnert/Neato-XV-Series-Cruz-Rev-113-Update](https://github.com/NoahJaehnert/Neato-XV-Series-Cruz-Rev-113-Update) | Exact Cruz updater packages, scripts, and authentic `XV11App.*.P.bin.enc` images | Proprietary updater and encrypted images; no decryptor or boot checksum source |
| [jeroenterheerdt/neato-serial](https://github.com/jeroenterheerdt/neato-serial) | Direct XV serial behavior and host command examples | Client implementation, not robot firmware |
| [Philip2809/neato-connected](https://github.com/Philip2809/neato-connected) | Generation map, command experiments, `0x1A`-terminated parsing | Gen1/XV support remains incomplete |
| [Xevel/NXV11](https://github.com/Xevel/NXV11) | LDS electrical/protocol reverse engineering and spoofing | LDS subsystem only |
| [ssloy/neato-xv11-lidar](https://github.com/ssloy/neato-xv11-lidar) | LDS packet/checksum details and Linux implementation | LDS subsystem only |
| [bmegli/xv11lidar-arduino](https://github.com/bmegli/xv11lidar-arduino) | Non-blocking embedded LDS parser | External MCU library, not Cruz BSP |
| [TKJElectronics/XV11Lidar_STM32F429](https://github.com/TKJElectronics/XV11Lidar_STM32F429) | Embedded scan-driver reference | Different MCU |
| [simondlevy/NeatoPylot](https://github.com/simondlevy/NeatoPylot) | Practical XV serial control/autopilot client | Host-side control, not replacement firmware |

## Important false-family references

- [RobertSundling/neato-botvac](https://github.com/RobertSundling/neato-botvac)
  targets Botvac D3-D7 on TI AM335x/QNX. Its firmware package is a `.bin` plus
  detached RSA-SHA256 `.signed` file and `Signing.crt`. Self-signing that
  package does not encrypt or authenticate a Cruz `XV11App.*.P.bin.enc` image.
- Botvac/pybotvac/openHAB `secret_key` values are per-robot Nucleo cloud HMAC
  credentials. They sign `serial + date + JSON body` for web requests and are
  unrelated to the Cruz firmware AES key or application-integrity field.
- Jiska Classen's thesis documents an AM335x/QNX hidden-IPL boot bypass and
  memory extraction on connected Botvac/VR300 hardware. It does not document
  Cruz Rev113 header fields, checksum logic, or AT91SAM9XE boot access.

## Clean-room v0 inputs

Use local captured stock transcripts as the normative contract, with public
client implementations as corroboration:

1. ASCII command parser and exact `0x1A` termination.
2. `GetVersion`, `Help`, and `TestMode` transcript compatibility.
3. Read-only `GetAnalogSensors`, `GetButtons`, `GetCharger`,
   `GetDigitalSensors`, `GetMotors`, and LDS commands.
4. Parse serial-exposed actuator commands but keep side effects gated until
   simulator fixtures and hardware safety limits pass.
5. Keep binary `Upload` framing in lab tooling, not the v0 runtime surface.

## Remaining acquisition boundary

Public GitHub search found no Cruz-specific SAM-BA/JTAG/NAND readback or
bootloader-dump implementation. P6/P10 details commonly repeated online are
often for the older Rev64/Binky board and must not be assumed for Cruz Rev113.
Future hardware work requires board-specific photos, continuity mapping, part
identification, and a recoverable donor-board readout. J3/ERASE remains out of
scope.

## P10 JTAG field result

The 2026-08-15 Cruz P10 experiment did not find a usable TAP. CherryDAP on an
ESP32-S3 enumerated and OpenOCD reached JTAG scan-chain interrogation, but every
full scan in installed 2.5, factory 2.4, and P6-triggered boot windows returned
all-ones/no TAP. The follow-up jtag-halt session narrowed the cause to one of
two physically-identical outcomes — the AT91 security bit is set, or the VDDIO
rail is dead (robot asleep) — ruling out wiring/orientation, runtime debug
disablement, and adapter mismatch. See
[neato-p10-jtag-result.md](neato-p10-jtag-result.md).

## Versioned USB contract update

Complete read-only USB snapshots collected on the same robot show that stock
2.5.15893 and 2.7.16621 have byte-identical probed help surfaces. Stock
3.1.17844 is smaller: its top-level help omits `GetLifeStatLog`, `GetSysLog`,
`SetDistanceCal`, and `SetWallFollower`, while `Help Upload` omits `dump` and
`xmodem`. The clean-room rewrite must model capabilities from the live version
instead of assuming one universal XV command table. Exact hashes and snapshots
are under `captures/jtag/jtag-p10-20260813T061756Z/`.

## Serial upload/readback result

The 2026-08-16 fixed-matrix experiment confirms a receive-capable updater but
no stock serial export path. Stock 2.5 and 2.7 accepted a complete 256-byte
project sentinel with `noburn`; all tested dump/readflash permutations returned
only echoes/terminators, and XMODEM never emitted SOH/STX. Exact stock writes
verified 2.7 and 3.1 before a final exact 2.5 restore. An exact vendor default
sound write also ACKed and preserved the known slot map.

On 3.1, `code + dump + Size` and `sound + dump + Size` selected the
host-to-robot receiver (ENQ) despite `dump` being absent from help. No payload
was sent after those unexpected ENQs. This is a parser precedence observation,
not firmware/NAND/filesystem readback. See
`captures/serial-upload/serial-upload-20260816T045102Z/`.
