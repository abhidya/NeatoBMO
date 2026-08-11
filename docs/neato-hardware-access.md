# Neato XV-12 — reaching the mainboard and the CPU

How to open our robot and get to the **Cruz Rev113** board and its **Atmel
AT91SAM9XE128-QU** CPU, for the SAM-BA serial / GPNVM-glitch key-extraction
route in [neato-envelope-crypto.md](neato-envelope-crypto.md). Chip-level
details are verbatim from the RECESSIM XV-11 teardown
(`https://wiki.recessim.com/view/Neato_XV-11`); the case-opening steps are
the standard XV-11 procedure — **verify screw positions on the actual unit**,
they vary slightly by revision. The authoritative pictorial teardown is the
Homebrew Robotics Club wiki (linked from RECESSIM).

## Do this first (safety — not optional)

1. **Power off, undock, and let it sit** so the LDS motor and caps discharge.
2. **Disconnect the battery before probing anything.** The NiMH pack feeds
   the board directly; a slipped probe across a live rail can kill the CPU
   or start the pack venting. Open the battery door underneath, unplug the
   pack connector. Reconnect only when you need the board powered for a
   serial session, and keep the pack away from shorts.
3. **ESD**: wrist strap or touch a ground first. The AT91 is static-sensitive.
4. **Never short J3 (ERASE).** See below — it is a one-way brick.

## Opening the case (standard XV-11/XV-12)

The main PCB sits on **top** of the chassis, under the top shell, behind the
LCD/button panel. To reach it:

1. **Remove the dustbin** and any filter.
2. **Pull off the front bumper.** It clips on across the front; it also hides
   several of the top-shell screws. Work it off gently — the clips are brittle
   with age.
3. **Remove the carry handle** if fitted (clips/screws at its pivots).
4. **Remove the top-shell Phillips screws.** Roughly 6–10 around the
   perimeter; **some are hidden under the bumper, under rubber side pads, or
   under warranty labels** — check those spots before prying. Count them out
   and keep them sorted; lengths differ.
5. **Lift the top shell off.** The main PCB is now exposed, mounted to the
   chassis; the LCD/UI panel ribbon connects to it. You can probe the board
   in place — you do **not** need to fully unmount it for serial/JTAG access.

## On the board

- **Main CPU:** silkscreen **`U29`** = **AT91SAM9XE128-QU** (ARM926, the
  Cruz/"early PCB" family — this is our board; the Rev64 Binky board uses a
  different NXP/ST CPU and is not this).

- **`P6` — serial/UART header** (the SAM-BA probe target). 3.3 V logic:
  ```
  P6:1  Unused (square pad — orientation marker)
  P6:2  AT91_RXD   → your adapter's TX
  P6:3  AT91_TXD   → your adapter's RX
  P6:4  Ground     → your adapter's GND
  ```
  Use a **3.3 V USB-UART** (FTDI/CP2102 set to 3.3 V). Cross RX↔TX, common
  ground, **do not apply 5 V** to these pins. This taps the CPU UART directly
  — the path to the on-chip **SAM-BA ROM monitor** if there's a pre-lock
  window at reset. (The robot's normal USB port reaches the *application*
  console, which is a different, later mode.)

  > **Correction (2026-08-11, adversarial review — see
  > `../captures/analysis/at91-baud-research.md`):** on a *healthy* board there
  > is **no such pre-lock window at cold boot.** AT91 RomBOOT finds a valid boot
  > image and jumps straight into the Neato bootloader → app; the `RomBOOT>`
  > SAM-BA prompt appears **only if boot fails**, which on this board means
  > erasing flash via **J3 (permanent brick)**. RECESSIM's capture off this exact
  > header shows the **app boot log** (NEROS), not a ROM prompt. So passive P6
  > capture (**115200 8N1**, confirmed) identifies the board and proves the tap,
  > but is **not** itself the key-extraction route on a working board. SAM-BA
  > read access requires the destructive GPNVM-glitch path, not a plain reset.

- **`P10` — JTAG header.** Verbatim layout:
  ```
  Bottom row: VDDIO (square pin), TRST, TDI, TMS, TCK
  Top row:    GND, GND, SRST, TDO, RTCK
  ```
  **JTAG is software-disabled** — it will not respond until the GPNVM
  security bit is cleared (the voltage-glitch route). Useless as-is; it's the
  target *after* a successful glitch.

- **`J3` — ERASE jumper. DO NOT TOUCH.** Direct connection to the AT91 ERASE
  line. **Shorting it and rebooting wipes the CPU's programming with no known
  recovery — permanent brick.** Not a reset. Keep tools and probes clear of it.

## Which header for which attack

- **Passive P6 capture → `P6` serial (115200 8N1).** Cheap, non-destructive, do
  first: attach the 3.3 V UART, power-cycle, capture the boot log. **Expect the
  Neato bootloader/app banner, NOT a SAM-BA `RomBOOT>` prompt** — see the
  correction above; a healthy board never drops to the ROM monitor at reset.
  Value here is confirming the tap and reading the app boot log, not key readout.
- **JTAG dump → `P10`,** but only *after* glitching the GPNVM security bit to
  re-enable it. Hard: Atmel debounced the ERASE line specifically to resist
  glitching (ATSAM4C32 is the nearest public precedent).

## Reminder

This is only worth doing to extract the fused AES key — the sole route left
(no public break exists; see [neato-envelope-crypto.md](neato-envelope-crypto.md)).
It has **nothing to do with running BMO**: the sound bank is unencrypted and
the speech pipeline never needs the app firmware. And do not do any of this
to the working BMO body — use a second scavenged Rev113 robot.
