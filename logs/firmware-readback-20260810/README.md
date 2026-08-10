# Firmware readback attempt — 2026-08-10

Target: XV-12 `WTD41611DD-0037829-P`, mainboard `7.1`, software `2.4.15667`.

## Fresh baseline

`pre-dump-snapshot/` is a read-only USB configuration/calibration snapshot with
its own `SHA256SUMS`. It is not an application image.

## USB application-readback probes

All commands were sent through `/dev/cu.usbmodem143301`. No payload was sent to
the robot and no erase, write, unlock, reboot, or flash command was used.

| Command | Saved capture | Result |
|---|---|---|
| `Upload code readflash` | `neato-code-readflash.raw` | 24 bytes: command echo, CRLF, `0x1a` only |
| `Upload code dump` | `neato-code-dump.raw` | 19 bytes: command echo, CRLF, `0x1a` only |
| `Upload dump` | `neato-upload-dump.raw` | 14 bytes: command echo, CRLF, `0x1a` only |
| `Upload code readflash xmodem` | no file | aborted after eight retries; robot never began XMODEM |

Conclusion: the live stock 2.4 application does not expose application bytes
through the documented USB upload/readback command forms.

## Hardware path availability

The ESP32-S3 was subsequently connected at
`/dev/cu.usbmodem5C381965721`. A clean archive of repository `HEAD` was built
outside the dirty worktree and flashed successfully. PlatformIO/esptool verified
every written block by hash and reset the board. After reboot, a TCP connection
to the P6 bridge at `10.0.0.106:3334` succeeded.

The isolated build disabled the unused ESP-IDF certificate bundle because its
generated assembly input was absent in the installed PlatformIO toolchain. The
application does not reference `esp_crt_bundle`; the P6 bridge and network
sources compiled normally. Build output hashes were:

| File | SHA-256 |
|---|---|
| `firmware.bin` | `77ca3091083ca23106a2317ba9c89bbb6bf3dd1494ace20abfbb6242f3932b73` |
| `bootloader.bin` | `f6f8df05cd247a79dec4afb084d7621bd19163c1ca7a0b0104b18c66d6bc7f47` |
| `partitions.bin` | `257b872c0b49cb3af18bd9f76b60e086c39779fcae6e12c8542e1e1905a7b906` |

The binary contains the locally generated Wi-Fi configuration and must not be
committed or published. It is retained only in the private firmware archive.

## Next physical prerequisite

Choose one:

1. With the Neato powered off, wire verified 3.3 V P6 signals: P6.2 -> GPIO17,
   P6.3 -> GPIO18, P6.4 -> GND. Do not connect P6.1 or 5 V. Start a passive TCP
   capture on port `3334` before powering on the Neato.
2. Attach an identified read-capable hardware programmer/debug probe after
   confirming the exact flash part, voltage, pinout, and raw NAND/OOB handling.

Do not use J3 and do not enter any erase, write, program, or unlock workflow.
