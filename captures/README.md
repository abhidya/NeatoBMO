# P6 capture log — data, timeline, and the confounds

Raw captures from tapping the Neato XV-12 **P6 debug UART** with the ESP32-S3.
See `../docs/P6_SWEEP_HANDOFF.md` for the live procedure and `../docs/neato-hardware-access.md`
for the P6 pinout. **Capture rule: never truncate. `tools/p6_capture.py` now
appends to timestamped files** — an earlier reused-filename truncation destroyed
our only robot sample (below).

## Field update — GPIO17→GPIO18 loopback PASSED (2026-08-11 13:50 PDT)

- Flashed `-DP6_SELFTEST` after removing an accidental jumper between the
  board pins labelled `TX` and `RX` (UART0/programming pins, not GPIO17/18).
- With GPIO18 floating, the capture produced a large noisy/high-bit burst.
  Connecting the numeric header pins **GPIO17→GPIO18** made the same live
  capture immediately become clean consecutive ASCII: `P6-SELFTEST 47` through
  `P6-SELFTEST 70`.
- Raw evidence: `p6_1786481459.log` (8016 bytes, append-only).
- **Conclusion:** ESP32 UART1 TX GPIO17, RX GPIO18, the firmware echo path, the
  host serial reader, and file persistence all work. The prior lost 8377-byte
  "gibberish" sample is now strongly explained by a floating GPIO18/contact
  state rather than Neato output.
- **Console correction:** `pio device list` identifies
  `/dev/cu.usbmodem5C381965721` as WCH `VID:PID=1A86:55D3`, `USB Single Serial`.
  The captured boot log confirms console UART0 on GPIO44/43. This connector
  therefore carries the primary UART0 console despite its misleading pathname;
  it is not the USB-Serial-JTAG secondary console assumed by the methodology
  review.

## Goal achieved — clean P6 cold-boot capture (2026-08-11 14:01 PDT)

- Raw evidence: `p6_1786482063.log` (7247 bytes, append-only).
- Passive wiring: P6.4→ESP32 GND and P6.3 AT91_TXD→GPIO18; P6.2/GPIO17 was
  deliberately left disconnected.
- Normal fixed-115200 bridge, one exclusive host reader, capture armed before
  Neato power-on.
- Clean decoded identifiers include:
  - `Neato Robotics XV-11/XEB V10:45:23`
  - `NEROS Build 15667 Oct 28 2011 11:25:50`
  - `Power On reset: 0 :PowerUp`
  - LDS `Loader V2.5.14010`, serial `WTD41411AA-0061795`, and
    `Runtime V2.6.15295`
- This proves the P6.3 signal, shared ground, GPIO18 receive path, 115200 8N1
  decode, ESP32 bridge, primary UART0 USB capture, and cold-boot timing end to
  end. The captured stream is the expected Neato bootloader/application log,
  not a SAM-BA `RomBOOT>` monitor.

## Button-held cold boots (2026-08-11 14:42–14:47 PDT)

- `20260811_A01_hold_start_cold_boot.log`: holding START during power-on first
  loaded the installed application, then produced three observed
  `Power On reset: 8 :Software` reboot cycles before capture was stopped.
- `20260811_A02_hold_back_cold_boot.log`: holding BACK during power-on made the
  bootloader print **`Loading factory application`** instead of `Loading
  installed application`. It then started the same reported NEROS build 15667.
- `20260811_A03_hold_start_back_cold_boot.log`: holding START+BACK also selected
  the factory application. Four clean boot banners were observed: two reported
  `PowerUp`, then two reported `Software`. One high-bit/undecoded interval
  appeared between clean factory boots. The operator later reported likely
  releasing the buttons accidentally and retrying at that point. This makes a
  transition/contact artifact more plausible; the raw bytes remain preserved
  and uninterpreted. The controlled repeat below tests the surrounding behavior.
- `20260811_A03R1_hold_start_back_repeat.log`: controlled repeat. It produced
  three factory-application boots (`PowerUp`, then two `Software`) while the
  buttons were held. After the timed release, the next `Software` boot selected
  the installed application. A much smaller high-bit interval occurred only at
  initial power transition, supporting startup/contact noise rather than an
  exposed firmware payload.
- Neither path entered `RomBOOT>` or SAM-BA.
- The BACK result establishes a non-destructive, button-selected factory-image
  boot path that may be useful for recovery after a bad installed-application
  update. It does not prove the factory image can repair the installed image.

## Runtime buttons and display standby (2026-08-11 14:57–15:06 PDT)

- Bench configuration contained the motherboard, power, LCD, and button panel;
  most robot peripherals were absent. Results therefore describe this stripped
  setup and should not be generalized to a fully assembled robot without a
  repeat.
- `20260811_A04_normal_menu_buttons.log`: initial run had one `PowerUp` boot and
  three installed-application `Software` boots. Individual BACK/START clicks
  emitted no additional P6 text.
- `20260811_A04R1_normal_back_start.log`: controlled run remained stable after
  one installed-application `PowerUp` boot. The LCD timed out/off with no P6
  message. A BACK click caused no observed display or UART change. A START click
  woke the LCD and played a sound; the LCD remained on after 30 seconds, but P6
  emitted no corresponding runtime message.
- **Conclusion:** on this build/setup, P6 is valuable for boot/reset selection
  and early initialization but does not trace ordinary UI button, sound, or LCD
  standby/wake events.

## Neato USB attachment and read-only commands (2026-08-11 15:12–15:16 PDT)

- Mac device mapping: ESP/P6 recorder `/dev/cu.usbmodem5C381965721`, WCH
  `1A86:55D3`; Neato USB CDC `/dev/cu.usbmodem1431201`, `2108:780B`.
- `20260811_B00_neato_usb_attach.log`: attaching Neato USB produced the P6 line
  `USB Connected`. The operator observed no attachment-related sound or LCD
  transition; earlier sound/idle behavior was corrected as ordinary inactivity.
- `20260811_B01_usb_readonly_snapshot_p6.log`: a single `GetVersion` followed
  by the guarded read-only snapshot woke the LCD without sound. P6 emitted no
  command-level trace.
- Snapshot identity: XV-12 `WTD41611DD-0037829-P`, software `2.4.15667`,
  mainboard `7.1`; LDS `V2.6.15295`, serial `WTD41411AA-0061795`.
- Snapshot files and checksums:
  `usb-snapshots/WTD41611DD-0037829-P_sw-2-4-15667_20260811T221515Z/`.
  Both recorded SHA-256 checks pass. Live `Help Upload` explicitly advertises
  `noburn - test option -- do NOT burn the flash after the upload.`
- **Conclusion:** P6 exposes USB link state but does not mirror ordinary USB CDC
  commands or responses.

## Factory-application USB surface (2026-08-11 16:31 PDT)

- Raw P6 evidence: `20260811_U02_factory_app_usb_readonly_p6.log`; structured
  comparison: `20260811_U02_factory_app_usb_readonly_result.json`.
- Holding the UI BACK button during cold power-on produced `Loading factory
  application`. Connecting Neato USB then produced `USB Connected` on P6.
- Factory-mode `GetVersion`, `Help`, `Help Upload`, and `Help SetSystemMode`
  responses were byte-for-byte identical to the earlier installed-application
  snapshot; the result JSON records each response SHA-256.
- No upload, test mode, reset, or write command was sent.
- **Conclusion:** the factory image does not expose an additional documented USB
  command surface, but it does expose the same normal updater help. This makes
  it a plausible fallback updater if the installed application stops booting;
  an actual programming operation from factory mode remains untested.

## Factory-application sound `noburn` (2026-08-11 16:48 PDT)

- Factory mode was established immediately beforehand by the BACK-selected boot
  recorded in U02, and the robot remained powered in that mode for this test.
- Raw P6 evidence: `20260811_U03_factory_sound_noburn_p6.log`; structured result:
  `20260811_U03_factory_sound_noburn_result.json`.
- Payload was the exact 770,048-byte vendor bank with SHA-256
  `d3969779a6a195812d72b6859454de004ea45beefdee6f1b5c50a2632564b64a`.
- USB sent exactly `Upload sound noburn Size 770052`, returned terminal byte
  `0x1A`, and the post-transfer health check succeeded.
- P6 reported `SOUND`, `BLAST`, `Size : 770052`, `Options : NoWrite`, full
  receive, then `Upload fail - nandflashWrite() fail - -1`—the same result as
  the installed-application B02 test.
- **Conclusion:** the factory application is not merely USB-enumerable; its
  fallback updater receives and dispatches a complete upload through the same
  no-write path. This strengthens the recovery case without programming NAND.
  It does not prove that a real factory-mode application or sound write would
  succeed.

## Exact vendor sound-bank `noburn` (2026-08-11 15:17 PDT)

- Raw P6 evidence: `20260811_B02_sound_noburn_exact_p6.log`.
- Target identity was checked immediately before transfer: XV-12
  `WTD41611DD-0037829-P`, software `2.4.15667`.
- Payload was the exact 770,048-byte vendor bank with SHA-256
  `d3969779a6a195812d72b6859454de004ea45beefdee6f1b5c50a2632564b64a`.
- USB command was exactly `Upload sound noburn Size 770052`; USB returned only
  terminator `0x1A`, and post-transfer `GetVersion` was unchanged/healthy.
- P6 exposed the hidden updater path: `Type : SOUND`, `Protocol : BLAST`,
  `Size : 770052`, `Options : NoWrite`, `Upload complete - 770052 bytes
  received`, then **`Upload fail - nandflashWrite() fail - -1`**. The operator
  heard one sound at completion.
- **Conclusion:** `noburn` definitely sets the internal `NoWrite` option and
  receives the entire payload, but the empty USB terminator is not a success or
  compatibility verdict. The internal terminal status is a NAND-write failure;
  without source or a flash readback, do not infer whether the write routine was
  called and deliberately blocked or rejected for another reason.

## Accepted/rejected `PlaySound` comparison (2026-08-11 15:43 PDT)

- Raw P6 evidence: `20260811_B01_playsound_accept_reject_p6.log`.
- Direct USB accepted `PlaySound 1`; the corresponding stock sound played.
- Direct USB rejected `PlaySound 4` with `SoundID '4' is out of range.`
- P6 emitted no runtime text for either result; post-command identity remained
  healthy on software `2.4.15667`.
- **Conclusion:** USB command responses, not P6, expose sound-slot validation.

## One-bit-corrupted sound-bank `noburn` control (2026-08-11 15:20 PDT)

- Raw P6 evidence: `20260811_B03_sound_noburn_onebit_p6.log`.
- Starting from the exact vendor bank, one bit was flipped at byte offset 4108
  inside the first sound record. Length remained 770,048 bytes; mutated SHA-256
  was `befe3a3832b221050fef0192877991929e1d26135cd48195bb3405d0db703de1`.
- The outer transfer checksum was recomputed. USB again returned only `0x1A`;
  post-transfer identity remained software `2.4.15667`.
- P6 produced the same diagnostic sequence as the exact bank: `NoWrite`, full
  770052-byte receive, then `nandflashWrite() fail - -1`.
- **Conclusion:** neither USB completion nor the captured internal no-write path
  distinguishes this one-bit content change. `noburn` is a transport exercise,
  not a sound-bank integrity or compatibility validator.

## Original sound-bank destructive write (2026-08-11 16:09 PDT)

- User selected the exact original/vendor-default bank and explicitly requested
  the destructive test. Raw P6 evidence:
  `20260811_C01_original_sound_burn_p6.log`; structured USB/P6 result:
  `20260811_C01_original_sound_burn_result.json`.
- Image: 770,048 bytes, SHA-256
  `d3969779a6a195812d72b6859454de004ea45beefdee6f1b5c50a2632564b64a`.
- USB: ENQ received; payload+checksum transferred in 3.017 seconds; closing
  response was ACK+TERM (`061a`); USB recovered on the same port; firmware
  identity remained `2.4.15667`.
- P6 exposed the physical write: `Type : SOUND`, `Protocol : BLAST`, no
  `NoWrite` option, `Upload complete - 770052 bytes received`, then
  `nandflashWrite() - region=0x400000 offset=0 bytes=770052` and
  `Upload - nandFlashWrite() OK`.
- Post-write sweep passed the exact expected original slot map: accepted
  `0–3,6–10,19`; all other IDs from 0–20 returned out of range.
- **Conclusion:** the sound-bank region begins at NAND region address
  `0x400000`, and the updater writes the entire 770052-byte framed blob at
  offset zero. Unlike `noburn`, a successful write returns ACK and P6 `OK`.

## Cruz-P 2.5 application `noburn` (2026-08-11 15:28–15:31 PDT)

- An initial P6 recorder file,
  `20260811_B04_code_2.5_15893P_noburn_p6.log`, was stopped after the application
  transfer was blocked pending explicit risk approval. No image bytes were sent.
- After approval, raw evidence was captured in
  `20260811_B04R1_code_2.5_15893P_noburn_p6.log`.
- Payload: Cruz-P 2.5 build 15893, 805,888 bytes, SHA-256
  `e1a31ef56e2b617a4056d70c308aab26b7e2cd95e679cb982d697ff4f089c697`.
- USB command: `Upload code noburn Size 805892`; USB returned `0x1A` and the
  robot remained software `2.4.15667` afterward.
- P6: `Type : CODE`, `Protocol : BLAST`, `Options : NoWrite`, full 805892-byte
  receive, then `Upload fail - nandflashWrite() fail - -1`.
- Operator observation: LCD turned on; no sound or visible message.
- **Conclusion:** the application no-burn path behaves like sound no-burn at
  this layer. It exercises transport and updater dispatch but does not validate
  compatibility, decryptability, or a successful application write.

## Cruz-P 2.7 application `noburn` (2026-08-11 15:39 PDT)

- Raw P6 evidence: `20260811_B05_code_2.7_16621P_noburn_p6.log`.
- Payload: Cruz-P 2.7 build 16621, 805,888 bytes, SHA-256
  `2e6033b1ef5440bed949de20e89563d7cb3dda41e0eb5e371c9d86dceeb1633f`.
- The fail-closed harness accepted the exact hash and target identity, then sent
  only `Upload code noburn Size 805892`. USB returned `0x1A`; installed software
  remained `2.4.15667`.
- P6 matched the 2.5 result exactly: `CODE`, `BLAST`, `NoWrite`, full receive,
  then `nandflashWrite() fail - -1`.
- **Conclusion:** this path reveals no version-dependent decrypt, signature, or
  compatibility decision between the 2.5 and 2.7 envelopes.

## Cruz-P 3.1 application `noburn` (2026-08-11 15:41 PDT)

- Raw P6 evidence: `20260811_B06_code_3.1_17844P_noburn_p6.log`.
- Payload: Cruz-P 3.1 build 17844, 847,872 bytes, SHA-256
  `03396329a1a47a7358d09bd414d01eddaa5806a50a18f4d9ce2f96edc2d5fab7`.
- The fail-closed harness accepted this exact ceiling-version hash and sent only
  `Upload code noburn Size 847876`. USB returned `0x1A`; installed software
  remained `2.4.15667`.
- P6 again showed `CODE`, `BLAST`, `NoWrite`, full receive, then
  `nandflashWrite() fail - -1`. The larger framed size was the only visible
  difference from 2.5/2.7.
- **Conclusion:** no-burn P6 does not expose decryption, signature, board-family,
  or version-compatibility decisions for any of the three allowed images. It is
  not a firmware-unlock oracle.
- Version 3.2 build 18755 is incompatible with this oldest side-jack Cruz
  Rev113 and was **not transmitted**. `tools/neato_code_noburn.py` rejects its
  exact archived hash and every unknown image before opening the serial port.

## Cruz-P 2.5 application burn and recovery proof (2026-08-11 17:49 PDT)

- The user explicitly authorized the exact destructive confirmation phrase
  recorded in `20260811_D01_code_25_burn_verification.json`. Two read-only
  preflights first pinned robot identity, current 2.4.15667 software, live
  updater help, image size, and SHA-256.
- Image: Cruz-P 2.5 build 15893, 805,888 bytes, SHA-256
  `e1a31ef56e2b617a4056d70c308aab26b7e2cd95e679cb982d697ff4f089c697`.
- Factory-mode USB command: `Upload code reboot Size 805892`. USB returned ACK
  (`0x06`) after 3.028 seconds and re-enumerated on the same port.
- P6 exposed the write: `CODE`, `BLAST`, `Options : Reboot`, full receive,
  `nandflashWrite() - region=0x10000 offset=0 bytes=805892`, then
  `Upload - nandFlashWrite() OK` and `Reboot in 1 sec ...`.
- The software reboot and a separate true cold boot both selected the installed
  application and reported NEROS build 15893. USB reports
  `Software,2,5,15893`; the archive's filename build 15893, not Config.ini's
  15894 token, is the live version.
- Post-upgrade `Help`, `Help Upload`, `Help SetSystemMode`, and `Help SetConfig`
  remain byte-identical to 2.4. No `PowerCycleCDC` appeared. `Upload dump`
  returned only its USB echo/terminator while P6 printed a 256-byte all-zero
  upload-save-area dump. `Upload code readflash` returned only echo/terminator,
  and its XMODEM form never started. Firmware 2.5 therefore does not unlock
  application readback.
- A BACK-held cold boot after the write selected the separate factory
  application, which still reports NEROS build 15667 and USB
  `Software,2,4,15667`. This proves the 2.5 application write did not overwrite
  the factory image and establishes a working factory updater fallback. It is
  not a byte-restorable copy of the former installed 2.4 application.

## Files here
- `jtag/jtag-p10-20260813T061756Z/` — ESP32-S3 CherryDAP Cruz P10 JTAG
  availability experiment. OpenOCD consistently opened the CMSIS-DAP/JTAG
  adapter, TDO controls changed with target power, but installed/factory and
  P6-triggered scans produced no stable TAP/IDCODE/IR length. No ARM halt,
  memory read, GPNVM action, reset-assisted attach, or J3/ERASE operation was
  performed. The same folder also preserves the later operator-requested exact
  vendor sound-bank restore window (`sound-vendor-write-result.json` plus
  `openocd-sound-vendor-*` scans) and exact stock Cruz-P 2.7 application
  transition window (`firmware-27-write-result.json` plus
  `openocd-firmware-27-*` scans); proprietary image bytes are not committed.
- `20260811_D01_factory_code_25_burn_p6.log` — factory boot, exact 2.5 NAND
  application write, software reboot, installed 2.5 cold boot, readback-probe
  diagnostics, and post-upgrade factory 2.4 boot.
- `20260811_D01_code_25_burn_result.json` — guarded USB transfer and verified
  post-reboot 2.5 identity.
- `20260811_D01_code_25_burn_verification.json` — consolidated image, USB, P6,
  cold-boot, readback, snapshot, and factory-fallback results.
- `20260811_D01_code_25_burn_preflight*.json` — two pre-write no-write safety
  records, including the repeat after P6-confirmed factory selection.
- `20260811_D01_sw25_upload_dump.raw` and
  `20260811_D01_sw25_code_readflash.raw` — echo-plus-terminator USB results;
  neither contains application bytes.
- `20260811_D01_factory_lcd_happy_result.json` — two accepted, transient
  factory-application happy-face draw attempts; the operator saw no visible
  change, and the white-LCD cause remains undetermined.
- `usb-snapshots/WTD41611DD-0037829-P_sw-2-5-15893_20260812T0048Z/` — read-only
  post-upgrade identity/configuration/calibration/help snapshot.
- `20260811_U03_factory_sound_noburn_p6.log` — exact vendor sound bank through
  the BACK-selected factory application's no-write upload path.
- `20260811_U03_factory_sound_noburn_result.json` — payload identity, USB/P6
  terminal results, and explicit no-write record for U03.
- `20260811_U02_factory_app_usb_readonly_p6.log` — BACK-selected factory boot,
  USB attach, and read-only command comparison.
- `20260811_U02_factory_app_usb_readonly_result.json` — exact response hashes
  versus the installed-application snapshot.
- `20260811_C01_original_sound_burn_p6.log` — successful original sound-bank
  destructive write; NAND region/address/length and OK status.
- `20260811_C01_original_sound_burn_result.json` — structured USB, P6, identity,
  and slot-sweep result.
- `20260811_B01_playsound_accept_reject_p6.log` — accepted/rejected PlaySound
  comparison; no runtime P6 output.
- `20260811_B06_code_3.1_17844P_noburn_p6.log` — guarded ceiling-version
  Cruz-P 3.1 code no-burn transfer.
- `20260811_B05_code_2.7_16621P_noburn_p6.log` — guarded Cruz-P 2.7 code
  no-burn transfer and internal updater diagnostics.
- `20260811_B04R1_code_2.5_15893P_noburn_p6.log` — approved Cruz-P 2.5 code
  no-burn transfer and internal updater diagnostics.
- `20260811_B04_code_2.5_15893P_noburn_p6.log` — pre-authorization recorder;
  transfer blocked before any image bytes were sent.
- `20260811_B03_sound_noburn_onebit_p6.log` — one-bit-corrupted sound-bank
  `noburn`; internal result matches exact vendor bank.
- `20260811_B02_sound_noburn_exact_p6.log` — exact vendor sound-bank `noburn`;
  internal NoWrite/receive-complete/NAND-write-failure diagnostics.
- `20260811_B01_usb_readonly_snapshot_p6.log` — P6 during USB GetVersion and
  read-only recovery snapshot.
- `20260811_B00_neato_usb_attach.log` — Neato USB attachment; `USB Connected`.
- `usb-snapshots/WTD41611DD-0037829-P_sw-2-4-15667_20260811T221515Z/` —
  read-only robot configuration/calibration/help snapshot with SHA-256 manifest.
- `20260811_A04R1_normal_back_start.log` — controlled normal boot, idle LCD
  timeout, BACK click, and START wake/sound; no runtime UART events.
- `20260811_A04_normal_menu_buttons.log` — initial normal button session with
  three software-reset boots.
- `20260811_A03R1_hold_start_back_repeat.log` — controlled START+BACK repeat;
  factory selected while held, installed selected after release.
- `20260811_A03_hold_start_back_cold_boot.log` — START+BACK-held cold boot;
  repeated factory-application boots and one undecoded high-bit interval.
- `20260811_A02_hold_back_cold_boot.log` — BACK-held cold boot selecting
  `Loading factory application`.
- `20260811_A01_hold_start_cold_boot.log` — START-held cold boot showing
  repeated software-reset cycles.
- `p6_1786482063.log` — **successful clean Neato P6 cold-boot capture** at
  115200 8N1 through the normal ESP32 bridge.
- `p6_1786481459.log` — successful GPIO17→GPIO18 loopback proof; also contains
  the preceding floating-GPIO18 noise interval.
- `2026-08-11T1229_p6_sweep_bannersonly.txt` — output of the `-DP6_BAUDSWEEP`
  build. **All `==== BAUD N ====` banners, ZERO robot bytes** (0 high-bit bytes).
- `2026-08-11_esp32_bootlog_flash_sweep.log` — esptool/PlatformIO log from the
  successful sweep flash (device boot/verify evidence, not P6 data).

## Timeline of evidence (read critically)
1. Normal bridge flashed, boot log OK over USB (`debug_uart: P6 debug bridge ...`).
2. Wired P6→ESP32. **First and ONLY robot data: ~8377 bytes of high-bit
   "gibberish."** BUT captured under TWO confounds simultaneously:
   - **TWO `p6_capture.py` processes were reading the same serial port** (a stale
     one from a closed terminal + the new one) → a single byte stream split
     between two readers.
   - the **`-DP6_SELFTEST` build was actively transmitting** on GPIO17 into the
     AT91 RX line at the same time.
   - the wiring had just been "swapped" per a (later-retracted) suggestion.
3. Killed the stale reader, restarted a SINGLE clean capture → **zero robot bytes.**
4. Flashed `-DP6_BAUDSWEEP` (single reader, receive-only, no injection).
   Power-cycled the Neato twice → **still zero robot bytes** (banners only).
5. The 8377-byte sample was **overwritten by `rm -f` and is unrecoverable.**

## The open question the reviewers must attack
Current working theory: "correct wiring, wrong baud" (115200 in the docs is an
unverified assumption). But the evidence is thin and confounded: the *only*
robot bytes appeared under a dual-reader + active-injection condition, and every
clean single-reader capture since has been **silent, not garbled**. Competing
explanations that are NOT yet ruled out:
- dual-reader byte-splitting produced pseudo-garbage from otherwise-valid bytes;
- the self-test injection (or its collision with the AT91 output) generated it;
- a floating/loose RX or lifted ground (silence now vs garbage then = a wiring
  change between the two states, not necessarily baud);
- inverted/idle-state or level issue; wrong header; or P6 isn't the live DBGU
  at that phase.

Do not treat "baud mismatch" as established. It is a hypothesis with one lost,
contaminated data point.

## Review outcome (2026-08-11) — see `analysis/`
Three adversarial reviewers converged: **baud IS 115200** (RECESSIM captured
readable text off this exact header; reproducible silence is evidence *against*
a baud mismatch), the blocker is a **channel fault** (loose/floating RX or lifted
GND, the AT91 not printing, or the rig reading the **lossy USB-JTAG secondary
console** while UART0 is the unrecorded primary), and the lone 8377-byte sample
was likely a **self-test TX-injection artifact**, not robot output. Strategically,
cold-boot P6 yields the **app/bootloader banner, not the SAM-BA ROM monitor**, so
P6 alone is not a key-extraction route on a healthy board. **Next step: prove a
signal exists on P6.3 (scope/DMM, or the GPIO17→GPIO18 loopback self-test) before
any reflash or baud change.** Details: `analysis/{at91-baud-research,hypothesis-review,methodology-review}.md`.

## NeatoOS full-length checksum experiment (2026-08-12 03:27–03:34 PDT)

- E09 no-burn: the 805,888-byte controlled image
  `cb9d7cc2de782f626ad8e8c8002ff52fefaa93976b8780c6784bcdfad3734e7f`
  completed transport with `NoWrite`; identity remained software 2.5.15893.
- E10 application write: USB returned ACK and P6 recorded a successful
  805,892-byte NAND write at region `0x10000`, followed by
  `Checksum error in application binary` and automatic factory 2.4.15667
  fallback. No `NEATOOS RAW V0` sentinel appeared.
- E11 recovery: exact stock Cruz-P 2.5
  `e1a31ef56e2b617a4056d70c308aab26b7e2cd95e679cb982d697ff4f089c697`
  was written and acknowledged; P6 started NEROS build 15893 and USB confirmed
  software 2.5.15893.
- Evidence: `20260812_E09_full_length_noburn_{p6.log,usb.json}`,
  `20260812_E10_full_length_burn_{p6.log,usb.json}`, and
  `20260812_E11_stock_25_restore_{p6.log,usb.json}`.

## Cruz-P opaque-header bit experiment (2026-08-12 03:49-03:57 PDT)

- E12 changed only bit 0 at file offset `0x18` in exact stock Cruz-P 2.5;
  bytes `0x200..EOF` remained byte-identical authentic ciphertext. The no-burn
  control completed and software 2.5.15893 remained healthy.
- E13 wrote that image once. USB returned ACK, P6 recorded the complete NAND
  write, then the bootstrap printed `Checksum error in application binary` and
  loaded factory 2.4.15667.
- Conclusion: the clear `0x10..0x1f` field is integrity-relevant or feeds the
  validated transform. The test does not reveal whether it is a checksum, MAC,
  nonce, or another form of covered metadata.
- E14 immediately restored exact stock
  `e1a31ef56e2b617a4056d70c308aab26b7e2cd95e679cb982d697ff4f089c697`.
  P6 started NEROS build 15893 and USB confirmed software 2.5.15893 on
  mainboard 7.1.
- Evidence: `20260812_E12_header_field_bitflip_noburn_{p6.log,usb.json}`,
  `20260812_E13_header_field_bitflip_burn_{p6.log,usb.json}`, and
  `20260812_E14_stock_25_restore_{p6.log,usb.json}`.

## P10 plus stock-software transition matrix (2026-08-13/15)

- Session evidence: `jtag/jtag-p10-20260813T061756Z/`.
- CherryDAP/OpenOCD worked as a Mac-visible JTAG adapter, but 149 completed
  powered scans—including boot-triggered, factory, vendor sound-write, and
  stock 2.5/2.7/3.1 transition windows—returned all-ones/no TAP. Eight early or
  target-off controls returned all-zeroes. No stable IDCODE or IR length was
  observed, so no ARM target, halt, register, SRAM, or flash read was attempted.
- The exact vendor default sound bank was written once and ACKed. Exact stock
  Cruz-P 2.7 and 3.1 were each written and verified, followed by a final exact
  stock 2.5.15893 restore. Proprietary image bytes remain outside Git.
- Complete USB snapshots show 2.5 and 2.7 have identical probed help replies.
  3.1 omits four top-level commands and the `dump`/`xmodem` upload options; see
  `usb-surface-comparison.json` for exact hashes.
- After updater reboot, macOS did not reliably recreate the Neato CDC device.
  Physical Neato USB reconnect restored VID:PID `2108:780B` and the expected
  application. Controller software must rediscover by USB and robot identity
  and offer a manual or controllable-hub VBUS-cycle fallback.
- Application-transition P6 files are header-only: the CherryDAP CDC path did
  not preserve UART bytes while CMSIS-DAP bulk scans were repeated. USB result
  JSON and snapshots, not those P6 files, establish the application versions.

## Serial upload-save-area matrix (2026-08-16)

- Plan and gated harness: `docs/neato-serial-upload-readback-plan.md` and
  `tools/neato_upload_save_area_probe.py`.
- First stock-2.5 row: `serial-upload/serial-upload-20260816T045102Z/`.
- A real `Upload code noburn Size 260` transaction accepted a 256-byte
  project-owned sentinel plus checksum. Fourteen unqualified dump/readflash
  permutations returned only echoes/terminators; six XMODEM receive-start
  probes produced no SOH/STX and returned to the command parser.
- `GetLifeStatLog` yielded a 498,744-byte partial textual diagnostics capture;
  `GetSysLog` was empty. No firmware, NAND, flash, filesystem, or upload-buffer
  bytes were returned. The P6/CherryDAP CDC path was silent in this row.
- A second 2.5 pass showed that ten `Size 260`-qualified dump/readflash forms
  also return no payload or ENQ. The exact stock Cruz-P 2.7 image was then sent
  once and ACKed; post-reboot identity was 2.7.16621. Its full parser matrix
  reproduced the 2.5 result: no dump/readflash payload, XMODEM start, or P6
  device bytes. Large lifetime-stat streams must be drained through their
  terminator before the next command or firmware-write identity preflight.
- Result artifacts: `stock-25-size-result.json`,
  `firmware-27-write-result.json`, and `stock-27-result.json`. The remaining
  planned transitions are exact stock 3.1, exact 2.5 restore, and an exact
  vendor default sound-bank write.
- Exact stock 3.1 build 17844 was subsequently written once and ACKed. Fresh
  matrix preflights verified 3.1.17844 after the updater's automatic USB
  rediscovery window expired. Unlike 2.5/2.7, 3.1 treated both
  `Upload code dump Size 260` and `Upload sound dump Size 260` as host upload
  starts: each returned ENQ, no payload was sent, CAN/CAN did not confirm
  cancellation, and the harness failed closed pending a normal power cycle.
  This is receiver selection, not dump/readback evidence. See
  `firmware-31-write-result.json`, `stock-31-result.json`, and
  `stock-31-completion-result.json`.
