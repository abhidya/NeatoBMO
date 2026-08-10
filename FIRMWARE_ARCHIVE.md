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
  `2.4.15667`. **Oldest Rev113 Cruz sub-variant: side charging jack present
  (confirmed 2026-08-10) — hard-capped at firmware v3.1; v3.2+ uses a
  different CPU and bricks this board.**
- Public compatible P-family application images start at `2.5.15893`; none is
  known to match the installed `2.4.15667` bytes.
- Public application updates are opaque `.enc` files with a 512-byte `neato`
  format-2 envelope and high-entropy, 512-byte-aligned payloads.
- **Envelope structure (measured 2026-08-10 across builds 15893/16621/17844/
  18755 P):** plaintext header — off 0-3 payload length (LE u32), off 4
  `0x02` format version, off 5-9 `"neato"` magic, off 10-15 zero, **off
  16-31 a 16-byte high-entropy field that is unique per image** (an
  IV/nonce). Body: entropy **7.9998 bits/byte**, flat byte histogram, and
  **zero repeated 16-byte blocks** in 53k (not ECB), zero repeated 512-byte
  pages. Read off the wire this is a **128-bit block cipher (AES-class) in
  CBC/CTR with a per-image IV and an on-chip key** — the MCU decrypts
  internally.
- **Brute force is not viable:** a 128-bit key is 2^128 ≈ 3.4e38 — ~1e13
  years even at 1e18 keys/s. No ECB structure, no keystream reuse
  (cross-hardware byte-differencing matched random), and known-plaintext
  does not weaken AES. The only realistic route is **key extraction** from
  the bootloader (glitch/side-channel or a leaked key), not cryptanalysis —
  and that is gated by the no-readback / encrypted-bootloader state above.
- `LDS_15295.enc` (16512 B lidar image) is **byte-identical across every
  build 2.5–3.2**, carries a different (non-`neato`) 32-byte header, and is
  also full-entropy — no leverage.
- **Weakness sweep (2026-08-10), all negative on the payload cipher:** no
  ECB (0 repeats in 53k×16 B and 100k×8 B blocks) → rules out ECB and any
  64-bit block cipher (DES/3DES/Blowfish/TEA); no stream keystream reuse
  (XOR of the two same-size 805888 B images 2.5⊕2.7 across the payload is
  7.999 bits/byte); no weak KDF (94 era-typical candidates — raw/MD5/SHA1/
  SHA256 of `neato`/`vorwerk`/`VR100`/`XV11`/`kobold`/magic/IV, AES-128
  CBC+CTR with the header IV — every decryption stayed full-entropy). The
  per-image payload (off 2080→end, entropy 7.95) is encrypted correctly;
  there is no search-space shortcut in it.
- **But the image has a fixed, IV-independent crypto block worth chasing.**
  Layout: `0–31` header, `32–511` zero, **`512–~2080` (~1568 B) a block
  that is identical across builds within an era** — 2.5 == 2.7 (100 %),
  3.1 ≈ 3.2 (97 %), cross-era only 7 % (random) — with a sub-block near
  off 544 identical across **all** builds. It is independent of the
  per-image IV, so it is not payload; its size/epoch-versioning fit a
  **key-wrap or RSA signature**. If key-wrap, the per-image content key
  sits here under a device master key (~2 master keys span all four
  builds → recovering one unlocks a whole era); if a signature, firmware
  is signed and a decrypt alone would not permit code injection. Either
  way the lead is **on-chip master/signing-key extraction**, not
  cryptanalysis — still gated by the no-readback state — and a next step
  is fingerprinting the `512–2080` block against known RSA moduli / leaked
  2011–2013 Neato/Vorwerk keys.
- Pairwise comparison of the same 2.5 build for B/D/M/P hardware matches the
  random 1/256 byte-equality baseline, ruling out a reused aligned keystream
  that could be attacked by simple ciphertext differencing.
- `NeatoUpgrader.exe` shows the `Upload code reboot Size %d` transport path,
  but no identified host-side decrypt/repack implementation. Static
  disassembly confirms that its upload path sends the caller-provided image
  buffer unchanged, followed by the transport checksum.
- The installer packaging is a **separate, already-defeated layer** — not
  the envelope. A 2012 Mikrocontroller.net thread cracked the password on
  the ZIP inside `VorwerkVR100Setup.msi`: admin-extract with
  `msiexec /a setup.msi /qn TARGETDIR=...`, a nested archive holds the
  renamed payloads, and the .exe uses Artpol `CZipArchive` (ASCII-only
  password, recoverable by logging `strlen` right before the zip opens,
  e.g. rohitab API Monitor). **This buys us nothing:** the files it
  unlocks are the same `XV11App.*.P.bin.enc`, `LDS_15295.enc`,
  `Config.ini`, and unencrypted `DfltSoundLib.Rev1.0.bin` we already hold
  fully unpacked under `OriginalVorwerkFirmwareFiles/`. Confirmed
  double-wrapping: the app image inside the (now-open) zip still carries
  the `neato` format-2 envelope (`XV11App.18755.P.bin.enc` begins
  `10 00 0D 00 02 "neato"`). `LDS_15295.enc` (16 KB lidar firmware) is
  separately encrypted with no `neato` magic. The zip password was a
  shipping wrapper; the envelope decrypt remains the real, unsolved
  blocker. Do not chase the zip password as if it were the decryptor.
- The `.enc` envelope was **also unsolved in the community**: an iXBT
  XV-11 thread has a direct "tell me how to decrypt this data" with no
  answer, alongside modders who could flash but never decrypt. Rev113
  firmware there is confirmed as **three parts** (app `XV11App.*.enc`,
  lidar `LDS_15295.enc`, sound `DfltSoundLib.Rev1.0.bin`) bound by a
  plaintext `Config.ini`, flashed as a bundle — matching our unpacked
  `OriginalVorwerkFirmwareFiles/`. Rev64 (Binky) firmware is a single file
  and is not cross-compatible: wrong-revision or wrong-part flashing
  bricks (multiple reported bricked Cruz boards). Our build-18755 P image
  == Vorwerk VR100 3.2's `xv11app.webupdate.box.enc`. See
  [neato-vorwerk-vr100-crossflash.md](docs/neato-vorwerk-vr100-crossflash.md)
  for the flashing procedure, the v3.1 ceiling on oldest Cruz boards, and
  the brick traps.
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
- The proposed USB bootloader transition was directly tested on 2026-08-10:
  after `TestMode On`, `SetSystemMode PowerCycleCDC` is rejected as an
  unrecognized option. This stock 2.4.15667 console exposes only Shutdown,
  Hibernate, and Standby, so that updater-era entry route is unavailable from
  the current application.

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
python3 tools/backup_neato.py
```

Rebuild the archive catalog or inspect an encrypted image:

```bash
python3 tools/neato_firmware.py catalog \
  /Volumes/2TB/neato-firmware-archive/sources/Neato-XV-Series-Cruz-Rev-113-Update \
  --output /Volumes/2TB/neato-firmware-archive/analysis/catalog.json

python3 tools/neato_firmware.py inspect /path/to/XV11App.bin.enc
python3 tools/neato_firmware.py validate-unlock /path/to/XV11App.bin.enc /path/to/plaintext.bin
```

`validate-unlock` exits with status 2 when the proposed plaintext does not pass
the structural checks. If a decryptor also emits a reconstructed encrypted
file, pass it with `--repacked`; exact SHA-256 equality then becomes mandatory.
