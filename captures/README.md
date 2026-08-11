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

## Files here
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
