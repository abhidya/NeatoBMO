# P10 JTAG on the Cruz Rev113 (AT91SAM9XE128) — prompt & instructions

Right-to-repair investigation of the user's own Neato XV-12. This is the
**JTAG companion** to the serial/hardware notes in
[neato-hardware-access.md](neato-hardware-access.md), the crypto/key picture in
[neato-envelope-crypto.md](neato-envelope-crypto.md), and the acquisition
ranking in [hardware-readback-options.md](hardware-readback-options.md). Read
those first; this file does not repeat their evidence, it operationalizes the
one route they leave open for JTAG.

## The one thing to internalize before touching P10

JTAG is **not an unbrick tool on this board**, and "it's easy to unbrick" is
only true for the brick JTAG does **not** cause:

- **Soft-brick (recoverable):** a bad *application* image at NAND `0x10000`.
  The factory `2.4.15667` app still boots via **BACK**, and the factory
  updater can rewrite `0x10000`. This is the real safety net for CFW work — it
  has nothing to do with JTAG.
- **Hard-brick (NOT recoverable):** clearing the AT91 **security bit**. JTAG is
  fused off while that bit is set, and the only documented way to clear it is
  the **ERASE** operation, which wipes the CPU's internal flash
  (`captures/analysis/at91-baud-research.md`,
  `neato-hardware-access.md#J3`). If the on-chip **AES key** lives in internal
  flash (fused / protected-flash / bootloader-derived — **unknown**, see
  `neato-envelope-crypto.md`), ERASE destroys it permanently. There is no
  public Cruz plaintext image or key to restore from, so this is a one-way
  paperweight and it also kills the CFW project outright.

Therefore, on the **working BMO robot**, this procedure is **read-only TAP
reconnaissance only**. Anything that clears security, drives GPNVM, or asserts
ERASE is **donor-board fault-injection research**, not an unbrick step, and is
out of scope for the live unit.

## Session prompt (copy/paste to drive a JTAG session)

> **Task:** Confirm whether the AT91SAM9XE128 (silkscreen `U29`) on this exact
> Cruz Rev113 board exposes a usable JTAG TAP over the `P10` header, **without
> performing any erase/program/unlock/GPNVM/ERASE operation.**
>
> **Target facts:** ARM926EJ-S core, JTAG-only (no SWD). Security bit is
> assumed **set** (JTAG fused off) until an IDCODE proves otherwise. J3/ERASE
> and any security-bit clear are hard stop conditions on the live board.
>
> **Do:** battery-safe power-up → attach a 3.3 V JTAG adapter to `P10` per the
> pinout below → run OpenOCD `scan_chain` / read IDCODE only. Record the raw
> OpenOCD output verbatim.
>
> **Decide from the result:**
> - Valid ARM926 TAP + IDCODE returned → security bit is *not* enforced on this
>   unit; a `halt` + read-only external-NAND/RAM dump becomes possible. **Stop
>   and hand back** the IDCODE before any halt/read so the read plan can be
>   reviewed.
> - No TAP / all-ones / all-zeros IDCODE on a correctly wired, powered board →
>   security bit is set; **the JTAG route is closed on this board.** Do not
>   attempt to clear it. Fall back to external-NAND acquisition
>   (`hardware-readback-options.md`), preferably on a donor Rev113.
>
> **Never:** short J3, clear the security bit, reprogram GPNVM3, or send any
> erase/program/write to internal flash on the working robot. Capture, then
> stop.

## Hardware

| Item | Requirement |
|---|---|
| Core | ARM926EJ-S → **JTAG only.** SWD adapters do **not** work; you need a real JTAG dongle. |
| Adapter | 3.3 V JTAG: e.g. Olimex ARM-USB-OCD-H, SEGGER J-Link, or an FT2232H-based OpenOCD interface. |
| Levels | **3.3 V logic. Never apply 5 V to P10.** Common ground with the board. |
| Power | Board powered from its own pack for the session; **disconnect the pack whenever you are wiring/re-wiring** (`neato-hardware-access.md#safety`). |

### P10 pinout (verbatim from the board, `neato-hardware-access.md:73-77`)

```
Bottom row: VDDIO (square pin)   TRST   TDI   TMS   TCK
Top row:    GND     GND          SRST   TDO   RTCK
```

Wire the standard 20-pin ARM JTAG signals to these: `TCK`, `TMS`, `TDI`, `TDO`,
`TRST`, `SRST` (optional — prefer leaving `SRST` unasserted first), and a solid
`GND`. Use `VDDIO` only as the adapter's **reference voltage** input (Vtref) if
your adapter needs one; do **not** back-power the board through it. `RTCK` is
adaptive-clocking feedback — connect it if your adapter supports RTCK, otherwise
start with a slow fixed TCK.

## Software (OpenOCD)

The AT91SAM9XE128 is a SAM9260-class part with embedded flash; start from the
shipped SAM9260 target and confirm the IDCODE empirically rather than trusting a
hard-coded value.

`neato-p10.cfg`:

```tcl
# --- adapter: pick ONE interface block ---
# Olimex ARM-USB-OCD-H (FT2232H):
#   source [find interface/ftdi/olimex-arm-usb-ocd-h.cfg]
# SEGGER J-Link:
#   source [find interface/jlink.cfg]
transport select jtag

adapter speed 100      ;# start SLOW (100 kHz). Raise only after a clean scan.
reset_config trst_only ;# try trst-only first; avoid driving SRST blindly

# AT91SAM9XE128 ~= SAM9260-class ARM926EJ-S TAP.
source [find target/at91sam9260.cfg]

init
scan_chain              ;# <-- the whole experiment. Records TAPs + IDCODEs.
# Do NOT proceed past here on the live board without handing back the result.
```

Run:

```sh
openocd -f neato-p10.cfg 2>&1 | tee captures/$(date +%Y-%m-%d)_p10_jtag_scan.log
```

(Stamp the filename by hand if your capture harness pins the date; the repo
convention is `captures/YYYY-MM-DD_...`.)

## Procedure (read-only, live board)

1. **Power off, undock, disconnect the pack, ESD-ground yourself**
   (`neato-hardware-access.md#do-this-first`). Keep all tools clear of **J3**.
2. Open the case and locate `U29` (the AT91) and the `P10` header. Verify the
   square-pad orientation marker matches the pinout above **before** connecting.
3. With the adapter **unpowered/detached from the board**, wire `TCK/TMS/TDI/
   TDO/TRST/GND` (and `RTCK`/Vtref if used). Double-check no 5 V line touches
   P10.
4. Reconnect the pack (or bench 3.3 V per the board's design) and power the
   board.
5. Run the OpenOCD `scan_chain` above. **Capture the full output verbatim.**
6. Interpret:
   - **Valid ARM926 TAP + a plausible IDCODE** → the debug port answers. Stop
     here and hand back the IDCODE. A follow-up read-only session can `halt` the
     core and dump external NAND / RAM through it — but that plan gets reviewed
     first, and still writes nothing.
   - **No device found / IDCODE `0x00000000` or `0xFFFFFFFF`** on a
     correctly-wired, powered board → consistent with the **security bit set**
     (JTAG fused off). This is the *expected* result per the repo's assumption.
     **The JTAG route is closed on this unit. Stop.**
7. Power down, disconnect the pack, remove the adapter, reassemble or move to
   the donor-board track.

## Hard stop conditions (live board)

- **Do not short J3 / assert ERASE.** Documented unrecoverable brick.
- **Do not clear the security bit** by any means (ERASE, GPNVM, glitch) on the
  working robot.
- **Do not `flash write` / `program` / `erase`** anything.
- If OpenOCD or any tool offers to "unlock", "unsecure", "mass erase", or
  "clear GPNVM to enable debug" — **that is the brick.** Decline and stop.

## What JTAG can and cannot buy here

- **If (and only if) the TAP answers:** halt the ARM926, read external NAND and
  RAM through the core non-destructively → a real path to a byte-exact image and
  a restorable backup, which the USB console never gave us
  (`FIRMWARE_SOUND_PATCH.md`, readback gate closed).
- **It still may not hand you the key.** If the AES key is in fuses or protected
  internal flash the core can't freely read, a working TAP alone does not
  extract it; that remains the separate, higher-risk key-extraction research in
  `neato-envelope-crypto.md#attack-surface`.
- **None of this is required to run BMO.** The sound library is unencrypted and
  the speech pipeline never touches the app envelope
  (`neato-envelope-crypto.md#for-the-bmo-project-specifically`).

## Donor-board note

Any experiment that *intends* to clear security, glitch GPNVM, or otherwise
defeat the fuse belongs on a **scavenged second Rev113 board**, not the BMO
robot, and would follow the fault-injection precedent referenced in
`neato-envelope-crypto.md` (ATSAM4C32 GPNVM glitch). No such non-destructive
bypass has been demonstrated on the Cruz AT91SAM9XE; until one is, P10 on the
live board is a read-only TAP-presence check and nothing more.

## Cross-references

- [neato-hardware-access.md](neato-hardware-access.md) — case opening, `U29`,
  `P6`/`P10`/`J3`, safety.
- [neato-envelope-crypto.md](neato-envelope-crypto.md) — cipher, key location,
  attack ranking.
- [hardware-readback-options.md](hardware-readback-options.md) — why external
  NAND acquisition outranks JTAG.
- [../captures/analysis/at91-baud-research.md](../captures/analysis/at91-baud-research.md)
  — security bit / ERASE / SAM-BA findings.
