# AT91SAM9XE / Neato P6 debug-UART baud research

Grounding the "P6 baud" question (see `../README.md`) in Atmel/Microchip
datasheet reality and Neato community evidence, instead of the repo's
unverified "115200 is an assumption" guess.

## TL;DR recommendation

- **Try 115200 8N1, 3.3 V, FIRST.** It is the correct answer with high
  confidence, backed by three independent lines of evidence (Atmel ROM default,
  Neato community serial console, and RECESSIM's *actual* verbatim P6 boot-log
  capture). It is very likely the sweep has already been passing over the right
  baud.
- **The "wrong baud" theory is probably wrong.** RECESSIM captured a clean,
  human-readable boot log off the same 4-pin serial port. If our single-reader
  captures are *silent* (not garbled) at 115200, the fault is far more likely
  **wiring / ground / RX-pin / missed the one-time power-on burst**, not baud.
- **At cold boot P6 shows the Neato bootloader + app (NEROS), not the SAM-BA
  ROM monitor.** The AT91 RomBOOT finds a valid application and jumps straight
  to it. There is **no non-destructive trigger** that makes the `RomBOOT >`
  prompt appear on a healthy board — reaching it requires erasing/invalidating
  the boot image (J3 ERASE = permanent brick, explicitly out of scope).

---

## Q1. AT91SAM9XE RomBOOT / SAM-BA monitor: output, UART, baud, trigger, clock dependency

**What runs at power-on.** The AT91SAM9XE on-chip ROM (RomBOOT) runs first. Per
the Microchip/Atmel boot description, at power-up it checks the flash Security
Bit and the boot-selection state (GPNVM3 plus the PA0/PA1 boot pins) to decide
whether to run the internal-Flash application, a downloaded application, or fall
through to the **SAM-BA Boot** monitor. If a valid boot program is present, it
jumps to it; the SAM-BA monitor only runs when **no valid boot program is
found** (or when boot-from-ROM/SAM-BA is explicitly selected).
(Sources: AT91SAM boot-strategy docs; developerhelp SAM-BA monitor page.)

**Which UART, and does it auto-print?** The SAM-BA Boot monitor exposes itself
on **two** interfaces simultaneously: the **DBGU serial** and the **USB device
port**. Behaviour, quoted from Microchip Developer Help (SAM-BA monitor):

> "The UART will be initialized for 115,200 baud, eight bits of data, no parity,
> and one stop bit."

and on how it decides which link to talk on:

> the monitor "will check if the USB Device Port has been enumerated, and if
> not, it will check if characters are received on the Target Console. If
> characters are received, the SAM-BA Monitor will continue communicating
> through the UART."

So the monitor is **largely passive**: it does not spew a banner on a loop. The
AT91SAM9260/9XE-class ROM does print `RomBOOT` and a `>` prompt when the monitor
executes, but community notes confirm the `>` is only emitted once the Monitor
actually runs (i.e. when boot fell through). To *drive* it you send a character;
it then accepts commands (`V#`=version, `N#`=non-interactive, etc.).

**Default baud = 115200 8N1.** SAM-BA and all AT91 demo binaries are configured
for **115200 8-N-1**, the standard AT91 serial parameters (Linux4SAM /
Developer Help). For the AT91SAM9XE specifically, the ROM monitor initialises
the DBGU to 115200 8N1.

**Does the ROM DBGU baud depend on the clock? Yes — with a caveat.** The DBGU
baud generator divides the master clock, so the *effective* ROM-monitor baud
depends on the main-oscillator/crystal the ROM assumes. On the AT91SAM9260/9XE
reference design that crystal is **18.432 MHz**, chosen precisely because it
divides cleanly to standard UART rates; the ROM computes its PLL/baud on that
assumption. If a board fits a *different* crystal, the ROM's DBGU output is
scaled and you get the classic **"garbage on DBGU"** at 115200. This is a real,
documented failure mode for AT91SAM9 boards — **but it only affects the ROM
monitor**, which the Neato does not reach at normal cold boot (see Q3), so it is
not the practical risk here. (Sources: at91.com "SAM9260-EK garbage on DBGU";
18.432 MHz UART-crystal references.)

## Q2. Is "P6" really the CPU DBGU, and what baud does the community report?

**P6 = the AT91SAM9XE DBGU, confirmed.** The RECESSIM XV-11 wiki documents the
early-PCB (Cruz/Rev113) 4-pin header wired directly to the AT91SAM9XE128:
`P6:1` unused (square pad), `P6:2` AT91_RXD, `P6:3` AT91_TXD, `P6:4` GND — the
same pinout in `../../docs/neato-hardware-access.md`.

**Community-reported baud = 115200 8N1 at 3.3 V.** The Neato XV serial console
(the robot CLI: `testmode on`, `testlds …`, responses ending in `^Z`) is widely
reported at **115200 8N1, 3.3 V logic** across robotreviews.com, the RECESSIM
wiki, and multiple GitHub serial libraries (jeroenterheerdt/neato-serial,
ssloy/neato-xv11-lidar). Note the community *usually* reaches this console
through the robot's USB/FTDI path, but that console rides the **same CPU DBGU**;
the P6 header is just the pre-USB tap of it.

**The boot chain — and it's not RomBOOT/U-Boot at the visible layer.** RECESSIM
captured *verbatim* boot logs off the 4-pin serial port:

```
Neato Robotics XV-11/XEB V11:16:01
Copyright (c) 2006-2010 Neato Robotics, Inc

Loading installed application
Starting app
NEROS Build 12882:12959 Jul 26 2010 22:38:28   (factory app, Start+Back)
```

That is the **Neato bootloader banner + NEROS app**, all human-readable — i.e.
capturable at a standard baud. RECESSIM also notes "The Neato bootloader console
does not support any of the standard U-boot commands," so what you see on P6 at
boot is Neato's own bootloader/app output, not AT91Bootstrap/U-Boot prompts and
not the AT91 ROM monitor. All of this sits at 115200. There is no community
report of a *different* baud for a separate RomBOOT phase, because on a healthy
board that phase never reaches the wire (Q3).

## Q3. If it boots straight into a valid app, is P6 silent at cold boot?

**No — P6 is not silent; it carries the Neato bootloader + app log** (the
RECESSIM capture above), which is the whole point: the AT91 RomBOOT finds a
valid application and jumps to it, so the **SAM-BA `RomBOOT >` prompt never
appears**. P6 is "silent of the ROM monitor," but it *does* emit the Neato
boot banner as a **one-time burst at power-on**, then whatever the app logs.

**What makes the ROM monitor appear:** only a *failed* application boot — the
ROM has to find **no valid boot program**. The documented ways to force that are
all destructive or risky on this board:
- Erase the boot image (the **J3 ERASE** line) — permanent brick, explicitly
  forbidden in `../../docs/neato-hardware-access.md`.
- Reprogram GPNVM3 / drive the boot pins to select SAM-BA Boot — requires
  working JTAG/programmer, which is fused off until a GPNVM glitch.

There is **no clean, non-destructive way to summon the SAM-BA prompt** on a
Neato that boots normally. Practically, P6 at cold boot = Neato bootloader/app
console at 115200, and that is the realistic capture target.

## Q4. Concrete baud(s) to try first, and any trigger sequence

**Baud order to try (highest-confidence first):**
1. **115200 8N1** — overwhelming primary + community + direct-capture evidence.
   This should be treated as the answer, not a guess.
2. 57600 8N1 — only as a long-shot second. (There *is* a documented "view the
   RomBOOT message at 57600, then switch to 115200" trick, but that is specific
   to **SAMA5D2 rev A**, a different SoC — do **not** assume it for the
   AT91SAM9XE. Listed only for completeness.)
3. 9600 / 38400 — generic fallbacks; no evidence supports them here.

Higher, exotic rates (250000, 460800, 921600, 74880) in the current sweep have
**no supporting evidence** for this CPU and are almost certainly noise-hunting.

**Before blaming baud, fix the capture rig** (the RECESSIM clean capture proves
115200 works):
- **Solid common ground** P6:4 ↔ adapter GND (a lifted GND alone produces both
  "garbage then silence" symptoms the repo saw).
- **RX pin**: robot **P6:3 (AT91_TXD) → ESP32 RX (GPIO18)** is the load-bearing
  wire. Verify continuity.
- **Exactly one reader** on the port, **receive-only, no injection** (the lost
  8377-byte "gibberish" sample was taken under *two* readers + active GPIO17
  self-test transmit — a textbook way to manufacture pseudo-garbage; it is not
  reliable evidence of a baud mismatch).
- **Power-cycle the Neato while capturing** — the banner is a one-time
  power-on burst; a running sweep that isn't power-cycled will only ever show
  its own `==== BAUD N ====` banners (exactly the current
  `2026-08-11T1229_p6_sweep_bannersonly.txt` result).
- Confirm 3.3 V, non-inverted idle-high TTL (never 5 V, never RS-232 levels).

**"Trigger" sequence for the monitor (only if a `>`/`RomBOOT` ever shows):**
there is no non-destructive trigger to *create* the monitor, but if one ever
appears, drive it by sending a character then `V#\r` (query version) or `N#\r`
(enter non-interactive mode). Do **not** send erase/write/unlock commands.
On a normally-booting Neato you will instead see the `Neato Robotics XV-11/XEB`
bootloader — that is expected, and is your 115200 confirmation.

---

## Bottom line for the reviewers

The repo's "correct wiring, wrong baud" framing is **not supported**. The single
contaminated data point (dual-reader + active injection) is a poor basis, and
RECESSIM's clean 115200-class boot-log capture off the same header is strong
counter-evidence. Lock the baud at **115200 8N1 3.3 V**, harden the wiring/ground,
power-cycle during a single receive-only capture, and expect the **Neato
bootloader banner**, not a SAM-BA ROM prompt.

## Sources (URLs)

- Microchip Developer Help — SAM-BA In-System Programmer Monitor (UART 115200
  8N1; USB-enum-then-wait-for-char behaviour):
  https://developerhelp.microchip.com/xwiki/bin/view/software-tools/programmers-and-debuggers/32-bit-isp/monitor/
- Linux4SAM Legacy Getting Started (AT91 demo binaries at 115200 8-N-1; DBGU
  console; the 57600→115200 RomBOOT note is SAMA5D2-rev-A specific):
  https://www.linux4sam.org/bin/view/Linux4SAM/LegacyGettingStarted?skin=print.myskin
- AT91SAM9260-EK "garbage on DBGU" thread (crystal/clock → baud mismatch fault):
  https://www.at91.com/viewtopic.php?t=28864
- AT91 ISP / SAM-BA User Guide (monitor commands, ROM boot behaviour):
  http://www.janus-rc.com/Documentation/6421B.pdf
- AT91SAM9260-EK SAM-BA Recovery app note (doc6281):
  https://www.microchip.com/content/dam/mchp/documents/MPU32/ApplicationNotes/ApplicationNotes/doc6281.pdf
- AT91SAM9XE datasheet (boot program / SAM-BA boot, GPNVM3, boot pins):
  https://www.keil.com/dd/docs/datashts/atmel/at91sam9xe_ds.pdf
- RECESSIM Neato XV-11 wiki (P6 pinout = AT91SAM9XE128 DBGU; verbatim 4-pin
  boot logs: Neato bootloader + NEROS; "no standard U-boot commands"):
  https://wiki.recessim.com/view/Neato_XV-11
- Neato serial console @ 115200 8N1 3.3 V (community): robotreviews.com forums;
  https://github.com/jeroenterheerdt/neato-serial ;
  https://github.com/ssloy/neato-xv11-lidar
- SparkFun XV-11 teardown (AT91SAM9XE identification):
  https://news.sparkfun.com/490

*Analysis file only; no capture files were modified. Written 2026-08-11.*
