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

> **CONFIRMED for our robot — do NOT flash 3.2.** Our XV-12 **has the side
> charging jack** (verified 2026-08-10), which per roboter-forum.com +
> iXBT.com (glnc222 / HerzNovy / Medtech) marks it as the **oldest Rev113
> Cruz** variant. That CPU **cannot take anything above v3.1** — 3.2+ is a
> different CPU and flashing it is **destructive (bricks the board)**.
> Multiple modders report bricked Cruz boards from exactly this. So the
> `XV-21_rev113_UpdateOfflineto32` (VR100 3.2) path is **off the table for
> this robot**; the hard ceiling is **3.1 (build 17844)**. The SEO blog's
> "Rev113 loves 3.2" is simply wrong for our board. And the community
> consensus is that nothing above 3.1 is worth it anyway (3.4 only trims
> cleaning time for NiMH), so for the BMO project the upside is zero and
> the only outcome on offer is a brick.

This boundary is enforced by `tools/neato_code_noburn.py`. The harness accepts
only the exact verified hashes for Cruz-P 2.5 build 15893, 2.7 build 16621, and
3.1 build 17844; hardcodes `Upload code noburn`; requires the target robot's
2.4 identity; explicitly rejects the archived 3.2 build 18755 hash; and rejects
every unknown image before opening the serial port. This guard applies to
no-burn diagnostics only and does not authorize an application flash write.

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

- **113 = Cruz board** (older XV-11/XV-12/early XV-21). ← ours, and it is
  the **oldest sub-variant: side charging jack present (confirmed
  2026-08-10)**, so it is **hard-capped at v3.1** — 3.2+ is a different CPU
  and bricks. Ships `2.4.15667`. The 3.1 ceiling is settled, not a guess.
- **64 = Binky board** (newer XV-21 / XV Signature, ships Neato 3.4). Do
  NOT flash Rev113 images to it — different firmware structure (below);
  cross-revision flashing bricks.

### Firmware structure differs by board revision (brick trap)

From the iXBT thread (HerzNovy, Medtech):

- **Rev113 (Cruz) firmware = three parts**: the app image
  (`XV11App.*.enc`), the LDS/lidar image (`LDS_15295.enc`), and the sound
  library (`DfltSoundLib.Rev1.0.bin`), tied together by a plaintext
  `Config.ini` manifest. The updater flashes them per the manifest; if the
  three files sit beside the updater they flash automatically, else one at
  a time.
- **Rev64 (Binky) firmware = a single file** of an exact size.
- The two are **not compatible**. Flashing a Rev113 part onto a Rev64 (or
  vice-versa) bricks the board — modders in-thread report "two bricked
  Cruz boards after client-side flashing." A too-small app-only file on
  the wrong revision is the classic symptom (`XV11App.18755.P.bin.enc` is
  ~852 KB and is *only* the app part, not a whole-image).

This is exactly the three-file bundle we already hold under
`OriginalVorwerkFirmwareFiles/*/` and mapped in `FIRMWARE_ARCHIVE.md`.

### Our 18755 image == Vorwerk's 3.2 web-update file

Confirmed in-thread (ilgiz, Medtech): the Vorwerk VR100 3.2 web updater
ships the app part as `xv11app.webupdate.box.enc`, which is the same image
as `XV11App.18755.P.bin.enc` — "any method and file 18755.enc will do."
So our archived 18755 P image *is* Vorwerk VR100 3.2. Vorwerk's own service
page historically hosted it: `kobold.vorwerk.de/service/...software-updates-vr100`.

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

- **Directly verified on this board:** hold the UI **BACK** button during cold
  power-on to select `Loading factory application`. The factory image enumerates
  over USB and its `GetVersion`, `Help`, `Help Upload`, and `Help SetSystemMode`
  responses are byte-identical to the installed application. An exact vendor
  sound-bank `noburn` then completed its full receive/dispatch path in factory
  mode: P6 showed `NoWrite`, 770052 bytes received, and the expected NAND
  failure while USB/health remained responsive. This verifies a functioning
  fallback receiver without programming NAND; an actual factory-mode write is
  still untested. See `../captures/20260811_U02_factory_app_usb_readonly_p6.log`
  and `../captures/20260811_U03_factory_sound_noburn_p6.log`.
- **Hard reset:** hold Power ~15 s until fully off.
- **Safe/recovery mode:** hold the **left bumper** while pressing Power —
  forces a recovery mode to retry the flash. **Caveat (glnc222):** the
  key-press "switch to backup" recovery was **removed in v3.4**, so this
  escape hatch may not exist on newer images — another reason not to climb
  past 3.1.

### Downgrade / cross-version chain (reference)

An XV25 owner (odoll) walked 3.4 → 3.0 (`XV11App.Prod.Box.17235.enc`) →
3.1 (`XV11App.Prod.Box.17844.enc`) → 3.4 (`XV11App.Prod.Box.24079.enc`).
Note the `.Prod.Box.` naming variant (Neato-Robotics-branded) vs the `.P.`
Vorwerk-branded images we hold; both are Rev113 app parts. Builds: 17235 =
3.0, 17844 = 3.1, 18755 = 3.2, 24079 = 3.4.

### More resource pointers (unverified)

- **robotreviews.com** "offline files captured in Russia" threads
  (glnc222): `viewtopic.php?f=20&t=19005`, and posts p=127302 / p=134169 /
  p=134925 — the community's Rev113 offline bundles, explicitly "to v3.1
  only" for the oldest Cruz boards.
- Vorwerk's historical VR100 software page:
  `kobold.vorwerk.de/service/.../software-updates-vr100`.
- **heXor** (NeatoControl author) is active in the iXBT thread — same
  attribution we already cite.

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

And the sharper brick risk is now confirmed for this exact robot: our
board has the side charging jack, so **3.2 will brick it** — the hard
ceiling is 3.1. Cross-revision or wrong-part flashing bricks outright.

Recommendation: **don't flash the working BMO body — and never above 3.1
even if we did.** If we ever pursue a firmware change, use a **second
scavenged robot** and stay at **≤3.1**. The navigation/corner gains are for
the *vacuum*, which BMO doesn't use, and the community says >3.1 changes
nothing worthwhile — so for the BMO project the upside is ~zero and the
blast radius is a bricked board plus the entire speech system.

## Follow-ups if we act

- The payload question is settled: the updater ships
  `XV11App.18755.P.bin.enc`, which **is** our archived build-18755 P image
  (same file, same repo). No separate download needed.
- Only NeatoControl (heXor, GitHub) is not yet archived, and only the
  masking variant needs it.
- Before flashing anything: re-derive the sound-bank identity gates and
  slot map against VR100 3.2, on a second robot (see Project risk).
