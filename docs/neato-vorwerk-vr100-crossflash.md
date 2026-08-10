# Vorwerk VR100 cross-flash path (Cruz Rev113) — archived reference

**Status:** unverified community procedure, archived 2026-08-10. Not yet
attempted on our robot. This documents a flashing path we had overlooked;
read the **Why we missed it** and **Project risk** sections before acting.

Source of the write-up: myneatoxv.com, "The Forbidden Upgrade" (simon,
2026-01-11) — an affiliate/SEO blog; capability claims ("increased suction
— rumored") omitted as unverified. Source of the actual files: the
NoahJaehnert Cruz Rev113 GitHub repo we already archive locally (the blog
just repackages these). We use the GitHub copy, not the blog's downloads.

## The path in one sentence

Mask the robot as a Vorwerk VR100 with `SetConfig ModelID VR100`, run the
Rev113 offline updater to flash the **Vorwerk VR100 v3.2** application
image, then set the ModelID back — no decryption or RE required.

## Why we missed it

Our firmware docs (`FIRMWARE_ARCHIVE.md`, `FIRMWARE_SOUND_PATCH.md`) framed
firmware work as an **unlock/decrypt/repack** problem: read the flash back,
break the 512-byte `neato` envelope, prove a plaintext image. That path is
genuinely blocked (no readback, no host-side decryptor).

This community path sidesteps decryption entirely. It uses the **vendor's
own offline updater**, which transfers the *already-encrypted* application
image; the MCU decrypts it internally at flash time — exactly the mechanism
our own notes describe (memory: "XV firmware modules are ENCRYPTED,
decrypted inside the MCU"). We noted the Windows updater exists
(`FIRMWARE_SOUND_PATCH.md:145`) but never captured:

1. the `SetConfig ModelID VR100` **masking trick** that makes the stock
   updater accept a cross-model (Vorwerk) image, and
2. VR100 3.2 as a *capability* target rather than a decrypt-analysis target.

We conflated "can't read/decrypt the image" with "can't flash a new image."
They are different problems; only the first is blocked.

Our robot matches the hackable profile precisely: XV-12, serial
`...-P`, mainboard **7.1 / Cruz Rev113**, stock `2.4.15667`.

## Board check first (do not skip)

On the robot LCD: **Menu → Support → Show Revision**, read **Board Rev**.

- **113 = Cruz board** (older XV-11/XV-12/early XV-21). Hackable; takes
  Vorwerk 3.2. ← ours
- **64 = Binky board** (newer XV-21 / XV Signature, ships Neato 3.4). Do
  NOT downgrade to Vorwerk 3.2 — reported to cause errors.

## Tools / resources — already archived locally

We do **not** need to download anything from the SEO blog. Everything it
resells comes from the legitimate upstream we already cite,
[NoahJaehnert/Neato-XV-Series-Cruz-Rev-113-Update](https://github.com/NoahJaehnert/Neato-XV-Series-Cruz-Rev-113-Update),
and it is already cloned at
`/Volumes/2TB/neato-firmware-archive/sources/Neato-XV-Series-Cruz-Rev-113-Update/`.
The self-contained VR100 3.2 flasher lives in `XV-21_rev113_UpdateOfflineto32/`:

- `NeatoUpgrader.exe` (231 KB) — the offline updater (the blog's "v1.2.0"
  tool; runs a script, no phone-home).
- `XV11App.18755.P.bin.enc` (833 KB) — **the VR100 3.2 P image**, encrypted;
  the MCU decrypts it at flash time.
- `neatousb.inf` + `neatousb.cat` — the legacy Windows USB driver.
- `up.txt` — the flash script; `run.bat` = `NeatoUpgrader.exe /NoServer up.txt`.
- `DfltSoundLib.Rev1.0.bin` (under `OriginalVorwerkFirmwareFiles/Firmware_3.2/`)
  — the matching sound library.

Sibling dirs hold the 2.7 (16621) and 3.1 (17844) images the same way.

Still needed only if you use the masking variant below:

- **NeatoControl** (heXor's classic console tool, GitHub) — not in this
  archive; only required to send `SetConfig ModelID`.
- A **Mini-USB cable** (thick PS3-controller type, NOT micro-USB); the port
  is under a rubber flap near the dust bin.

I did not download or run any of this — the archive already has it, and the
`.exe`/driver are Windows-only (this is a Mac). Flash from a Windows box.

## The actual flash script (upstream, no masking needed)

The NoahJaehnert updater flashes the image directly — it does **not** need
the blog's `SetConfig ModelID` masking. `up.txt` for VR100 3.2 is:

```
send SetLanguage None
getlocal XV11App.18755.P.bin.enc
wait 5000
send testmode on
send-nowait setsystemmode PowerCycleCDC
wait 22000
upload code reboot
wait 22000
send getversion
send SetLanguage None
```

Run on Windows with the driver installed: `NeatoUpgrader.exe /NoServer up.txt`
(from the `...UpdateOfflineto32` dir). `upload code reboot` is the flash
primitive; the 22 s waits cover the power-cycle and the write.

## Blog variant (NeatoControl masking) — alternative, not required

The myneatoxv.com guide uses a different updater and masks the model first.
Recorded for completeness; the scripted `NeatoUpgrader` above is simpler.

1. Install the legacy Neato USB driver; confirm a COM port appears.
2. Robot off the dock, battery ≥50%, Mini-USB connected.
3. NeatoControl → Connect → `SetConfig ModelID VR100`; reconnect (now a
   VR100); exit.
4. Run the offline updater as Administrator, pick the COM port, Update.
   **Do not touch anything.** Wait for "Update Complete", disconnect.
5. NeatoControl → `SetConfig ModelID XV12` to restore the model name.
6. Reboot.

### Recovery if it fails to boot

- **Hard reset:** hold Power ~15 s until fully off.
- **Safe/recovery mode:** hold the **left bumper** while pressing Power —
  forces a recovery mode to retry the flash.

## Project risk — read before flashing our robot

Cross-flashing to VR100 3.2 would **break the BMO speech stack we just
built**, and possibly more:

- The sound-bank burn gates pin robot identity to `WTD41611DD` +
  `Software,2,4,15667` (`tts_bank.VERSION_REQUIRED_SUBSTRINGS`). VR100 3.2
  changes the version string, so every burn/verify/restore path would
  refuse until re-derived, and the byte-exact bank invariants
  (`SOUND_BANK_WRITE_GATES.md`) are unproven on 3.2's flash layout.
- The live 10-slot `PlaySound` map, the `SetLCD` full-span quirks, and the
  10 Hz tick were all characterized against 2.4.15667. None are guaranteed
  to hold on VR100 3.2.
- Our stock 2.4.15667 image bytes are archived
  (`WTD41611DD-...-P_sw-2-4-15667_*`), but **no proven readback/restore
  path exists** — a bad flash may not be reversible to our exact baseline.

Recommendation: if we pursue this, do it on a **second scavenged Rev113
robot** (see the "$20 broken Neato" guide), not our working BMO body, until
the whole pipeline is re-characterized on VR100 3.2. The navigation/corner
gains are for the *vacuum*, which BMO doesn't use — for the BMO project the
upside is low and the blast radius is the entire speech system.

## Follow-ups if we act

- The payload question is settled: the updater ships
  `XV11App.18755.P.bin.enc`, which **is** our archived build-18755 P image
  (same file, same repo). No separate download needed.
- Only NeatoControl (heXor, GitHub) is not yet archived, and only the
  masking variant needs it.
- Before flashing anything: re-derive the sound-bank identity gates and
  slot map against VR100 3.2, on a second robot (see Project risk).
