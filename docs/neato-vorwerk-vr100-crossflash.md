# Vorwerk VR100 cross-flash path (Cruz Rev113) — archived reference

**Status:** unverified community procedure, archived 2026-08-10. Not yet
attempted on our robot. This documents a flashing path we had overlooked;
read the **Why we missed it** and **Project risk** sections before acting.

Source: myneatoxv.com, "The Forbidden Upgrade: Neato Firmware Update to
Vorwerk VR100 (Offline Guide)" (simon, 2026-01-11). The site is an
affiliate/SEO blog — treat capability claims skeptically and verify every
download independently before use. Marketing claims ("increased suction —
rumored") are omitted here as unverified.

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

## Tools / resources to archive

From myneatoxv.com "The Vault: Neato XV & Vorwerk VR100 Software
Repository". **Not downloaded or hash-verified yet** — archive the pointers,
verify before trusting.

- **Mini-USB cable** (the thick PS3-controller type, NOT micro-USB). The
  port is under a rubber flap near the dust bin.
- **Neato USB driver (x64)** — legacy Windows driver. Win10/11 may require
  "Disable Driver Signature Enforcement" to install.
- **NeatoControl** (classic) — the console tool that sends `SetConfig`.
- **Neato/Vorwerk Offline Updater Tool v1.2.0** — the Rev113 flasher (the
  original phone-home Neato Updater no longer works; servers down since
  2019).
- **Vorwerk VR100 v3.2 application image** — this is build 18755 in our
  `FIRMWARE_ARCHIVE.md` P-image catalog (852,992 enc / 851,984 plaintext),
  so we may already hold the payload the updater ships.

## Procedure (as published — clear steps, brick risk is real)

1. **Driver:** install the legacy Neato USB driver; confirm a COM port
   appears in Device Manager.
2. **Prepare robot:** off the dock, battery ≥50%, connect Mini-USB. LCD
   shows "firmware update mode" or blanks.
3. **Mask as VR100:** in NeatoControl, Connect → Commands tab →
   `SetConfig ModelID VR100`. Disconnect, reconnect — it should now
   enumerate as a VR100. Disconnect, exit NeatoControl.
4. **Flash:** run the Offline Updater Tool v1.2.0 **as Administrator**,
   select the COM port, click Update. **Do not touch anything** — the bar
   crawls, the robot may beep/twitch. Wait for "Update Complete", then
   disconnect USB.
5. **Restore model name:** run NeatoControl again →
   `SetConfig ModelID XV12` (our model; the guide's generic form is
   `XVXX` for 11/14/15/21).
6. **Reboot:** the robot restarts on the new firmware.

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

- Download the four resources, record sizes + SHA-256, add to
  `FIRMWARE_ARCHIVE.md` alongside the P-image catalog.
- Confirm whether the updater's VR100 3.2 payload == our archived build
  18755 image (compare hashes).
- Add `SetConfig ModelID` to the NeatoControl/command reference.
