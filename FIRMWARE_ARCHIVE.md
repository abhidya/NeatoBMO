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

Status as of 2026-08-16: there is no proven Cruz firmware unlock, decryptor,
repacker, plaintext application, or byte-restorable full-flash backup. Exact
public Cruz-P 2.5, 2.7, and 3.1 applications were each installed and verified,
then 2.5.15893 was restored; none opened a readback or custom-firmware route.

Known state:

- Live robot: XV-12, P hardware family, mainboard `7.1`, installed firmware
  `2.5.15893`; the separate BACK-selected factory application remains
  `2.4.15667`. **Oldest Rev113 Cruz sub-variant: side charging jack present
  (confirmed 2026-08-10) — hard-capped at firmware v3.1; v3.2+ uses a
  different CPU and bricks this board.**
- Public compatible P-family application images start at `2.5.15893`, now the
  installed image. No public image is known to match the former installed
  `2.4.15667` bytes; the surviving factory 2.4 image is not a raw backup.
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
- **The whole image is one AES-CBC stream with a FIXED key and FIXED IV**
  (block-fingerprint deep-dive, 2026-08-10). Layout: `0–511` plaintext
  header (magic + per-image 16 B field + zero pad); **encryption starts at
  offset 512** and runs to EOF as a single CBC stream. Proof: every
  cross-build first-divergence is exactly 16-byte-aligned and avalanches to
  EOF (textbook CBC), with no intra-image 16 B block repeats (not ECB). The
  leading blocks are identical across builds because the firmware's head
  (bootloader/vector table/config) is identical plaintext, and a constant
  key+IV encrypt identical plaintext to identical ciphertext. Exact
  divergence offsets: 2.5-vs-3.1 = 624, 3.1-vs-3.2 = 2032, 2.5-vs-2.7 =
  2080; the all-four-identical prefix is **512–624** (112 B = 7 CBC
  blocks). This **kills the earlier "signature / key-wrap" reading of the
  512–2080 region** — a per-era-identical block cannot be a signature over
  a per-image-varying payload. Ruled out by tells: no RSA (`0x010001`
  appears 0× in any file; no `30 82` DER; `openssl asn1parse` fails), no
  AES-KW (no `A6A6A6A6A6A6A6A6`), no cert/PEM/OID/strings anywhere.
  **Fixed-IV CBC is a real misstep but does not reduce the key search** —
  it only leaks shared plaintext prefixes (exactly what we see); the AES
  key is still 2^128. The header `off 16–31` field is **not** the CBC IV
  (block 0 @512 is identical across all images, so the IV is constant); it
  is a per-image nonce/MAC of unresolved purpose.
- **CPU / key location (OSINT, corrected 2026-08-11).** Cruz Rev113 main MCU =
  **Atmel AT91SAM9XE128-QU** (ARM926, per the RECESSIM XV-11 teardown);
  Binky Rev64 = NXP LPC3143 + STM32F100. Decryption happens on the robot and
  the inspected host updaters only relay `.enc` bytes. The key's physical
  storage is **unknown**: fused storage, protected internal flash, and
  bootloader derivation have not been distinguished. JTAG is blocked while
  the AT91 security bit is set; ordinary clearing uses ERASE and destroys
  internal flash. No public Cruz flash dump is known.
- **No public break exists** (high confidence). No published key,
  decryptor, repacker, or plaintext XV/VR100 image. Firmware is RSA-SHA256
  signed (`Signing.crt`; confirmed on the sibling Botvac line, where the
  robot notably does **not** validate the cert chain — self-signed images
  are accepted); the signing private key never leaked. The only documented
  full-memory extraction of any Neato is **CVE-2018-20785** (Classen,
  Botvac Connected) — a **different CPU (TI AM335x)** serial-bootloader
  bypass that does **not** transfer to the Atmel Cruz. Aside: the Vorwerk
  update ZIPs' password is `VORVR100!%` — a wrapper over the already-`.enc`
  files, not the firmware key (matches the 2012 Mikrocontroller.net crack).
- **Next realistic route = duplicate external-NAND acquisition.** P6 has now
  been proven as the 115200 8N1 Neato boot log, not a healthy-board SAM-BA
  entry. Acquire raw NAND pages twice with OOB, bad-block markers, and ECC
  convention preserved, preferably on a donor board. The live 2.5 write
  identifies logical application region `0x10000`; existing sound evidence
  identifies logical region `0x400000`, but physical geometry still must be
  mapped. On-chip fault injection remains a higher-risk donor-only research
  path: it would need to bypass security transiently without the ordinary
  destructive ERASE clear. **Avoid J3/ERASE — documented as an unrecoverable
  brick.** See
  [neato-envelope-crypto.md](docs/neato-envelope-crypto.md) for the full
  analysis and source list.
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
- A simultaneous P6 capture of verified Cruz-P 2.5 build 15893 now shows what
  the USB terminator hides: `CODE`, `BLAST`, `NoWrite`, full 805892-byte receive,
  then `nandflashWrite() fail - -1`. Installed identity stayed 2.4.15667. This
  remains transport/dispatch evidence, not decryption or compatibility proof.
  See `captures/20260811_B04R1_code_2.5_15893P_noburn_p6.log`.
- The guarded Cruz-P 2.7 build 16621 no-burn test produced the identical P6
  sequence and unchanged 2.4 identity. No version-dependent decrypt or
  compatibility stage was exposed. See
  `captures/20260811_B05_code_2.7_16621P_noburn_p6.log`.
- The guarded ceiling-version Cruz-P 3.1 build 17844 test differed only in its
  framed size, 847876 bytes; it ended in the same `NoWrite` NAND-failure status
  with unchanged installed identity. Across 2.5/2.7/3.1, no-burn exposed no
  decryption, signature, or compatibility decision. See
  `captures/20260811_B06_code_3.1_17844P_noburn_p6.log`.
- Incompatible 3.2 build 18755 was not transmitted. The checked-in no-burn
  harness explicitly rejects its archived SHA-256 and every unknown image
  before opening the serial port.
- Compatible Cruz-P application images and the vendor default sound bank are
  now also recorded as metadata-only references in
  `neatoos/manifests/reference-images.json` and the P10 session manifest. Do
  not commit the proprietary `.enc` images, sound-bank bytes, ESP backups, raw
  dumps, or recovered secrets.
- During the P10 software-transition observation, the exact stock Cruz-P 2.7
  build 16621 image was written once with `Upload code reboot`; the result JSON
  records a healthy identity change from software 2.5.15893 to 2.7.16621. This
  was an operator-requested stock transition, not a JTAG unlock.
- A subsequent exact stock Cruz-P 3.1 build 17844 write returned ACK. Automatic
  post-reboot USB discovery timed out, but a physical Neato USB reconnect
  exposed healthy `Software,3,1,17844` on the same serial/mainboard. A complete
  read-only USB snapshot preserves the 3.1 command surface. The target was then
  moved through 2.7 again for a comparable snapshot and restored to exact stock
  2.5.15893.
- USB help comparison found 2.5 and 2.7 byte-identical for the probed help
  replies. 3.1 omits `GetLifeStatLog`, `GetSysLog`, `SetDistanceCal`, and
  `SetWallFollower` from `Help`, and omits `dump` and `xmodem` from
  `Help Upload`. Updater reboot did not reliably recreate macOS USB CDC after
  the 3.1 and final 2.5 writes; the NeatoBMO controller must rediscover by USB
  identity and expose a physical or controllable-hub VBUS-cycle fallback.
- Exact Cruz-P 2.5 build 15893 was subsequently installed from the confirmed
  factory application with `Upload code reboot`. USB returned ACK; P6 showed a
  successful 805892-byte write to NAND region `0x10000`; software reboot and
  true cold boot both produced installed NEROS build 15893. BACK still boots
  the separate factory NEROS build 15667. Post-upgrade help was unchanged, raw
  dump/readflash returned only echoes, and XMODEM never started, so the stock
  upgrade did not unlock firmware acquisition. See
  `captures/20260811_D01_factory_code_25_burn_p6.log` and adjacent records.
- Holding UI BACK at cold power directly selected the factory application. Its
  USB `GetVersion`, `Help`, `Help Upload`, and `Help SetSystemMode` replies were
  byte-identical to the installed application's responses. This is a plausible
  fallback updater path. A subsequent exact vendor sound-bank `noburn` in the
  same factory session received all 770052 framed bytes and produced the same
  P6 `NoWrite`/NAND-failure result as the installed application, with a healthy
  USB result afterward. This verifies the fallback receiver without programming
  NAND; a real factory-mode write remains untested. See
  `captures/20260811_U02_factory_app_usb_readonly_p6.log`,
  `captures/20260811_U03_factory_sound_noburn_p6.log`, and adjacent JSON files.
- ESP32 Wi-Fi was observed at `10.0.0.106` with ports `2323` and `3333` open.
  The P6 debug-UART bridge on `3334` was closed, and the ESP32 and Neato were
  not cabled together at that observation point.
- `Upload sound noburn` accepts the public sound-module command through its
  ENQ binary-receive stage, then returns an empty terminal response rather than
  ACK/NAK—the same completion pattern as a previously captured successful
  `Upload code noburn`. A simultaneous P6 capture now shows the hidden status:
  `NoWrite`, receive complete, then `nandflashWrite() fail - -1`. The robot
  remained responsive with the same version afterward; the USB terminator is
  not validation or a success verdict. See
  [sound-upload-noburn-20260810.md](/Volumes/2TB/neato-firmware-archive/work/logs/sound-upload-noburn-20260810.md)
  and `captures/20260811_B02_sound_noburn_exact_p6.log`.
- A controlled one-bit sound-bank mutation at byte 4108 produced the identical
  USB terminator and P6 `NoWrite`/receive-complete/NAND-failure sequence. This
  directly confirms that `noburn` is not a content-integrity validator. See
  `captures/20260811_B03_sound_noburn_onebit_p6.log`.
- A no-write `PlaySound 0..20` sweep exactly matches the ten non-empty page
  entries in the public sound-bank header: `0–3`, `6–10`, and `19` play; all
  other IDs report out of range. See
  [live-playsound-sweep-20260810.md](/Volumes/2TB/neato-firmware-archive/work/logs/live-playsound-sweep-20260810.md).
- A separate 2026-08-16 serial campaign repeated the exact stock transition
  sequence 2.5 → 2.7 → 3.1 → 2.5 with one ACKed, zero-retry write per image,
  then wrote the exact 770,048-byte vendor default sound bank (SHA-256
  `d3969779a6a195812d72b6859454de004ea45beefdee6f1b5c50a2632564b64a`).
  Final USB identity was 2.5.15893; the known accepted sound IDs remained
  `0–3`, `6–10`, and `19`. Complete metadata-only evidence is under
  `captures/serial-upload/serial-upload-20260816T045102Z/`.
- Across that campaign, no dump/readflash grammar returned firmware, NAND,
  filesystem, sound-region, or volatile sentinel bytes, and XMODEM never
  emitted SOH/STX. Stock 3.1 did select the host upload receiver for two
  `region + dump + Size` forms; no payload followed those unexpected ENQs.
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

Next recovery gate: duplicate external-NAND acquisition with raw page/OOB/ECC
preservation, preferably on a donor board. Board identity and passive P6 DBGU
at 115200 8N1 are already proven; repeating USB/P6 probes is not the priority.
No Ghidra patching or flash image work starts until capture geometry, hashes,
extraction map, and application plaintext state are proved.

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
