# Neato XV firmware archive and recovery record

The working archive lives outside the Git repository on the 2 TB volume:

```text
/Volumes/2TB/neato-firmware-archive/
├── sources/Neato-XV-Series-Cruz-Rev-113-Update/
├── analysis/
│   ├── SHA256SUMS.all
│   ├── catalog.json
│   └── cruz-p-images.json
└── robot-backups/
    └── WTD41611DD-0037829-P_sw-2-4-15667_20260810T001304Z/
    └── WTD41611DD-0037829-P_sw-2-4-15667_20260810T021420Z/
```

`SHA256SUMS.all` records every archived source file. The robot snapshot has its
own `SHA256SUMS` covering the JSON record and raw transcript. It preserves the
installed version identity, calibration, schedule, warranty, charger state,
sensors, and command help without changing flash. It is **not** the installed
application binary: stock firmware 2.4 does not export that region over USB.

## Current unlock/recovery status

Status as of 2026-08-10: there is no proven Cruz firmware unlock, decryptor,
repacker, plaintext application, or restorable full-flash backup.

Known state:

- Live robot: XV-12, P hardware family, mainboard `7.1`, firmware
  `2.4.15667`.
- Public compatible P-family application images start at `2.5.15893`; none is
  known to match the installed `2.4.15667` bytes.
- Public application updates are opaque `.enc` files with a 512-byte `neato`
  format-2 envelope and high-entropy, 512-byte-aligned payloads.
- `NeatoUpgrader.exe` shows the `Upload code reboot Size %d` transport path,
  but no identified host-side decrypt/repack implementation.
- `Upload code noburn` accepted a 3.1 encrypted image into the updater receive
  path, but later `dump` returned no application payload. Treat that as a
  transport observation, not cryptographic validation or readback.
- ESP32 Wi-Fi was observed at `10.0.0.106` with ports `2323` and `3333` open.
  The P6 debug-UART bridge on `3334` was closed, and the ESP32 and Neato were
  not cabled together at that observation point.
- `Upload sound noburn` accepts the public sound-module command through its
  ENQ binary-receive stage, then returns an empty terminal response rather than
  ACK/NAK—the same completion pattern as a previously captured successful
  `Upload code noburn`. The robot remained responsive with the same version
  afterward; this is not a validation of the module or any flash operation. See
  [sound-upload-noburn-20260810.md](/Volumes/2TB/neato-firmware-archive/work/logs/sound-upload-noburn-20260810.md).
- A no-write `PlaySound 0..20` sweep exactly matches the ten non-empty page
  entries in the public sound-bank header: `0–3`, `6–10`, and `19` play; all
  other IDs report out of range. See
  [live-playsound-sweep-20260810.md](/Volumes/2TB/neato-firmware-archive/work/logs/live-playsound-sweep-20260810.md).
- Read-oriented sound upload commands do not export the installed sound region
  on this firmware: raw `readflash` returns only its terminator and its XMODEM
  form never starts. See
  [sound-readback-probe-20260810.md](/Volumes/2TB/neato-firmware-archive/work/logs/sound-readback-probe-20260810.md).

Corrected assumptions:

- Do not infer plaintext from the `.enc` payload layout.
- Do not treat the public updater as an unlock tool.
- Do not treat the USB recovery snapshot as an application backup.
- Do not use J3 erase or any write/flash/unlock workflow as a recovery step.

Evidence lives outside this repo:

- [work/logs](/Volumes/2TB/neato-firmware-archive/work/logs/)
- [phase-1-envelope-inventory.md](/Volumes/2TB/neato-firmware-archive/work/logs/phase-1-envelope-inventory.md)
- [research-profile.md](/Volumes/2TB/neato-firmware-archive/work/logs/research-profile.md)
- [hardware-readback-runbook.md](/Volumes/2TB/neato-firmware-archive/work/logs/hardware-readback-runbook.md)
- [work/inputs](/Volumes/2TB/neato-firmware-archive/work/inputs/)
- [work/plaintext-candidates](/Volumes/2TB/neato-firmware-archive/work/plaintext-candidates/)
- [work/repacked](/Volumes/2TB/neato-firmware-archive/work/repacked/)

Next recovery gate: non-destructive hardware acquisition only. First photograph
the actual P-board and verify part markings, then passively capture P6 DBGU at
115200 8N1. If a ROM-monitor route is proven on this exact board, use official
SAM-BA read operations only and write duplicate matching captures to
`work/inputs/`. No Ghidra patching or flash image work starts until the raw
capture geometry, hashes, extraction map, and application plaintext state are
proved.

## Archived Cruz application images

| Release | Build | Hardware suffix | Encrypted file size | Declared plaintext |
|---|---:|---|---:|---:|
| 2.5 | 15893 | P | 805,888 | 805,156 |
| 2.7 | 16621 | P | 805,888 | 805,284 |
| 3.1 | 17844 | P | 847,872 | 847,100 |
| 3.2 | 18755 | P | 852,992 | 851,984 |

The P images are the relevant family for the current robot (`...P`, mainboard
7.1 / Cruz Rev113). Version 3.1 is the initial patch-analysis target because it
is the mature public Cruz update commonly associated with Rev113 hardware. It
must not be flashed merely because it is compatible: first unlock it, validate
the plaintext, identify all flash regions, and prove a recovery/readback path.

The upstream source archive is
[NoahJaehnert/Neato-XV-Series-Cruz-Rev-113-Update](https://github.com/NoahJaehnert/Neato-XV-Series-Cruz-Rev-113-Update).
The protocol reference is Neato's
[XV Programmer's Manual](https://help.neatorobotics.com/wp-content/uploads/2020/07/XV-ProgrammersManual-3_1.pdf).

## Reproduce the records

Create another read-only snapshot (logs are omitted because 2.4 may reset its
USB connection while reading them):

```bash
python3 backup_neato.py
```

Rebuild the archive catalog or inspect an encrypted image:

```bash
python3 neato_firmware.py catalog \
  /Volumes/2TB/neato-firmware-archive/sources/Neato-XV-Series-Cruz-Rev-113-Update \
  --output /Volumes/2TB/neato-firmware-archive/analysis/catalog.json

python3 neato_firmware.py inspect /path/to/XV11App.bin.enc
python3 neato_firmware.py validate-unlock /path/to/XV11App.bin.enc /path/to/plaintext.bin
```

`validate-unlock` exits with status 2 when the proposed plaintext does not pass
the structural checks. If a decryptor also emits a reconstructed encrypted
file, pass it with `--repacked`; exact SHA-256 equality then becomes mandatory.
