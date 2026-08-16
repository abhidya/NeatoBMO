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
  > read access is not available from a plain reset. Normal security-bit
  > clearing uses ERASE and destroys internal flash; any non-erasing transient
  > bypass would be separate donor-board fault-injection research.

- **`P10` — JTAG header.** Verbatim layout:
  ```
  Bottom row: VDDIO (square pin), TRST, TDI, TMS, TCK
  Top row:    GND, GND, SRST, TDO, RTCK
  ```
  **JTAG is blocked while the AT91 security bit is set.** Ordinary clearing
  invokes ERASE and destroys internal flash. Treat P10 as a donor-board research
  target only after a genuinely non-erasing bypass has been demonstrated.

  > **Field result (2026-08-15):** an ESP32-S3 running CherryDAP opened
  > successfully as a CMSIS-DAP/JTAG adapter from macOS, and P10 TDO showed a
  > target-power-dependent electrical state, but repeated P10 scans found no
  > stable TAP/IDCODE/IR length in installed 2.5, factory 2.4, or P6-triggered
  > cold/factory boot windows. No ARM target, halt, register read, memory read,
  > reset-assisted attach, flash write, GPNVM operation, or J3/ERASE action was
  > performed. See [neato-p10-jtag-result.md](neato-p10-jtag-result.md) and
  > `../captures/jtag/jtag-p10-20260813T061756Z/`.
  > Later operator-authorized stock sound and application writes were observed
  > with scan-only P10 commands before/during/after. Exact stock 2.5, 2.7, and
  > 3.1 all booted on this robot; every transition scan remained all-ones/no
  > TAP. These NAND/sound writes used the Neato USB updater, not JTAG.

- **`J3` — ERASE jumper. DO NOT TOUCH.** Direct connection to the AT91 ERASE
  line. **Shorting it and rebooting wipes the CPU's programming with no known
  recovery — permanent brick.** Not a reset. Keep tools and probes clear of it.

## Which header for which attack

- **Passive P6 capture → `P6` serial (115200 8N1).** Cheap, non-destructive, do
  first: attach the 3.3 V UART, power-cycle, capture the boot log. **Expect the
  Neato bootloader/app banner, NOT a SAM-BA `RomBOOT>` prompt** — see the
  correction above; a healthy board never drops to the ROM monitor at reset.
  Value here is confirming the tap and reading the app boot log, not key readout.
- **External NAND acquisition → flash package/test pads, not P6/P10.** This is
  the next practical acquisition route; preserve page data plus OOB/ECC and
  make duplicate reads, preferably first on a donor Cruz board.
- **Protected internal-flash/JTAG research → `P10`, donor only.** A useful
  fault attack must transiently bypass protection without the documented ERASE
  operation. No such Cruz result has been demonstrated.

## Reminder

**Field result (2026-08-11):** passive P6 capture succeeded at 115200 8N1 using
P6.4→ESP32 GND and P6.3→ESP32 GPIO18, with P6.2 left disconnected. The preserved
log is `../captures/p6_1786482063.log` and identifies NEROS Build 15667. As
expected, it is a Neato boot/application log and contains no SAM-BA prompt.

P6 is now complete as a boot-observability experiment; it is not an unlock.
The 2026-08-16 application-state USB upload/readback matrix also produced zero
target bytes on the CherryDAP CDC/P6 path during every 2.5, 2.7, and 3.1 row.
That silence does not contradict the proven boot-log wiring: it only means this
adapter/capture path provided no parser or NAND corroboration for those rows.
The next OS-acquisition experiment is duplicate external-NAND capture, ideally
on a second scavenged Rev113 robot. On-chip key storage remains unknown. None
of this is required to run BMO: the sound bank is unencrypted and the speech
pipeline does not require application-firmware decryption.

## P10 JTAG bring-up note (2026-08-13)

The first non-destructive P10 session on the Cruz Rev113 board was a read-only
adapter/TAP characterization, not a halt or readback attempt. The experiment
used the ESP32-S3 CMSIS-DAP adapter from `CherryDAP` and the Mac-visible
OpenOCD 0.12.0 command path.

What was learned:

- the adapter enumerated normally on macOS;
- P10 TDO was electrically responsive — low with the target off, high with the
  target powered when only GND/TDO were attached;
- repeated scans at 5, 10, 50, and 100 kHz did not produce a stable TAP,
  IDCODE, or IR length;
- a forced TAP declaration on the factory-boot path still failed with an IR
  capture mismatch;
- no halt, register read, or memory read was attempted.

What was not done:

- no VDDIO measurement, because the meter step was waived for this session;
- no series resistors were installed, because that step was also waived;
- J3 / ERASE remained untouched;
- the Neato was never powered from the ESP32.

The detailed evidence record is
[neato-p10-jtag-result.md](neato-p10-jtag-result.md), and the raw
session files live under `captures/jtag/jtag-p10-20260813T061756Z/`.
