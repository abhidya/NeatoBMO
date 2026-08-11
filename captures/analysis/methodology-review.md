# Methodology review — bugs & confounds in the P6 capture pipeline

> **Post-review field correction (2026-08-11 13:50 PDT):** finding 1's device
> identification was disproven by direct enumeration. `pio device list` reports
> `/dev/cu.usbmodem5C381965721` as WCH `VID:PID=1A86:55D3`, `USB Single Serial`,
> and the captured boot log confirms primary console UART0 on GPIO44/43. The
> pathname alone did not identify USB-JTAG; this connector is not subject to the
> secondary-console FIFO theory. The GPIO17→GPIO18 self-test also passed in
> `../p6_1786481459.log` (counters 47–70), proving the ESP32 RX/capture chain.
> Noise with GPIO18 floating became clean ASCII immediately after loopback,
> strongly supporting a floating-input explanation for the old gibberish.

**Scope:** firmware (`esp32-body/src/debug_uart.c`, `main.c`, `neato_usb.c`),
host (`tools/p6_capture.py`), build/console config (`sdkconfig.esp32s3`,
`platformio.ini`), and the documented procedure (`docs/P6_SWEEP_HANDOFF.md`,
`captures/README.md`). Stance: adversarial — assume the pipeline is subtly broken.

This complements the existing `hypothesis-review.md` (which attacks the *signal-layer*
hypotheses H1–H9). This file attacks the **plumbing**: every place where the
firmware, the host reader, or the procedure can independently produce **false
gibberish** or **false silence** regardless of what the robot is actually doing.

**Key config facts established this review (from `sdkconfig.esp32s3`):**
- `CONFIG_ESP_CONSOLE_UART_DEFAULT=y`, `CONFIG_ESP_CONSOLE_UART_NUM=0` → the
  **primary** console (where `fwrite(stdout)`/`fflush` land) is **UART0 (GPIO43/44)**.
- `CONFIG_ESP_CONSOLE_SECONDARY_USB_SERIAL_JTAG=y` → a **secondary** console over
  the native **USB‑Serial‑JTAG** peripheral.
- The port everyone captures — `/dev/cu.usbmodem5C381965721` (see the flash log
  and the fact that it enumerates as `cu.usbmodem*`, not `cu.usbserial*`) — is the
  **native USB‑Serial‑JTAG = the SECONDARY console.** Nobody is capturing the
  primary (UART0) console at all.
- `main.c` unconditionally starts a **USB‑OTG host stack** (`neato_usb_install()`
  + `coli_mcu_start()` MSC) in *every* build, including the sweep build.

---

## Ranked findings (most-likely culprit first)

### 1. The capture reads the *lossy secondary* (USB‑Serial‑JTAG) console; the full stream goes to UART0, which nobody records — FALSE SILENCE / partial loss of bursts
**Type:** firmware/console-config + methodology. **Produces:** false silence, especially of a fast one-shot burst.

- **Evidence:** `sdkconfig.esp32s3:1329` `CONFIG_ESP_CONSOLE_UART_DEFAULT=y`,
  `:1335` `CONFIG_ESP_CONSOLE_SECONDARY_USB_SERIAL_JTAG=y`,
  `:1338` `CONFIG_ESP_CONSOLE_UART_NUM=0`. The captured port is `cu.usbmodem*`
  (native USB, i.e. the USB‑Serial‑JTAG secondary), confirmed by the flash log
  (`captures/2026-08-11_esp32_bootlog_flash_sweep.log`: `/dev/cu.usbmodem5C381965721`,
  "Hard resetting via RTS pin"). All robot/banner output is written with
  `fwrite(buf,1,n,stdout); fflush(stdout)` (`debug_uart.c:98‑99, 140, 146`).
- **Failure it causes:** The USB‑Serial‑JTAG controller has only a **64‑byte
  on-chip FIFO**, and the secondary-console write path is **non-blocking with
  drop-on-full** when the host is not draining fast enough. Small, infrequent
  writes (the 24‑byte `==== BAUD N ====` banners, one per 1.2 s) always fit and
  always appear. A **real AT91 cold-boot burst is exactly the opposite traffic**
  — hundreds/thousands of bytes back-to-back — and is precisely what overflows a
  64‑byte FIFO and gets silently dropped on this path, *while the identical bytes
  are delivered intact to the un-captured primary UART0 console.*
- **Why this is the top suspect:** it explains the *exact* observed signature —
  **banners present, robot bytes absent** — as a pipeline artifact. Periodic
  small writes survive; bursty writes are dropped. (This does require a burst to
  have actually reached RX; if RX was dead, see `hypothesis-review.md` H2/H4.)
- **Fix / discriminating test:** Capture the **UART0** path instead — either plug
  the DevKitC‑1 **"UART"/COM** connector into the Mac and read that tty, or set
  `CONFIG_ESP_CONSOLE_USB_SERIAL_JTAG=y` (make USJ the *primary*, blocking
  console) and rebuild. **Decisive test:** during ONE Neato power-cycle, capture
  **both** the UART0 port and the USJ port simultaneously; if UART0 shows robot
  bytes and USJ shows only banners, this defect is confirmed and every prior
  "silent" capture is invalid.

### 2. Sweep window (1.2 s) vs. one-shot boot burst + `uart_flush_input` per window — the sweep can miss the correct-baud decode even when a live signal exists
**Type:** firmware + procedure. **Produces:** failure-to-identify baud / apparent silence at the *right* rate.

- **Evidence:** `debug_uart.c:129‑132` — 11 bauds; `:135‑148` — each held for
  `60 × pdMS_TO_TICKS(20)` ≈ **1.2 s**; `:137` `uart_flush_input(DEBUG_UART)` is
  called at the **start of every window**, discarding the RX FIFO + ring buffer.
  Full sweep cycle = 11 × 1.2 s ≈ **13.2 s**.
- **Failure it causes:** The handoff states the boot log is a **one-time burst at
  power-on** (`P6_SWEEP_HANDOFF.md:58‑59`). For the burst to be decoded *at the
  real rate*, it must land inside the single ~1.2 s window that happens to be
  parked on the correct baud — a ~1/11 (≈9%) alignment per power-cycle. Two
  power-cycles ≈ 17% chance. Any burst that arrives at a *wrong*-baud window is
  either recorded as garbage or, if it straddles a window boundary, thrown away
  by the very next `uart_flush_input`. So **failing to see readable text is the
  statistically expected outcome even with perfect wiring and the correct baud in
  the list.** The banner-only file therefore *cannot* be used to reject the
  correct-baud/correct-wiring case.
- **Important honesty caveat:** this defect explains *failure to catch the right
  rate*; it does **not** explain the observed **zero bytes at *every* baud**. A
  live-but-wrong-baud line still delivers *some* (garbage) bytes to
  `uart_read_bytes`. Total silence across ~27 cycles points upstream to *no
  signal on RX* (see `hypothesis-review.md` H2/H4), not to this timing gap.
- **Fix / test:** Don't sweep against a one-shot event. Arm a **fixed-baud**
  bridge *before* power-on so there is no window to miss, and repeat per candidate
  rate; or in sweep mode, drop `uart_flush_input` and lengthen windows so a burst
  can't be flushed away. Better: put a scope on P6.3 to read the bit period
  directly (see `hypothesis-review.md` H-Scope) and skip the sweep entirely.

### 3. USB‑OTG host stack runs on the shared internal USB PHY that also backs the capture port — re-enumeration / "port gone" mid-capture
**Type:** firmware architecture. **Produces:** false silence, capture death, port renames.

- **Evidence:** `main.c:44‑46` calls `neato_usb_install()` (→ `usb_host_install()`,
  `neato_usb.c:100`) and `coli_mcu_start()` (a second USB‑MSC client;
  `main.c:45‑46`, dep `espressif/usb_host_msc` in `src/idf_component.yml`)
  **unconditionally** — the sweep `-DP6_BAUDSWEEP` flag only guards
  `debug_uart.c`, so `main.c` still brings up the full USB host in the "receive-only"
  diagnostic build. The captured console port is the native USB‑Serial‑JTAG
  (finding #1), which shares the ESP32‑S3 internal USB PHY with the USB‑OTG
  controller.
- **Failure it causes:** Installing/servicing the USB‑OTG host reconfigures the
  shared USB PHY and can knock the USB‑Serial‑JTAG CDC connection to the Mac
  offline, renaming or dropping `/dev/cu.usbmodem*` — matching the handoff's
  regression *"the ESP32 got unplugged (port gone, capture died)"*
  (`P6_SWEEP_HANDOFF.md:66`). Even if banners get through most of the time, this
  makes the capture fragile and can blank out arbitrary stretches.
- **Caveat:** banners *did* survive on this port, so the PHY handoff is evidently
  not permanently fatal on this board (OTG likely idles until a device attaches).
  This is a **stability/enumeration hazard**, not the sole cause of the banner-only
  file — hence rank 3, below the two defects that directly shape the result.
- **Fix / test:** Build the diagnostic firmware with **no USB host** — guard
  `neato_usb_install()`/`coli_mcu_start()` behind `#ifndef P6_BAUDSWEEP` (and for
  selftest) so the sweep image touches only UART1 + console. Or capture over
  UART0 (finding #1), which is independent of the USB PHY entirely. Test: `dmesg`
  / `ls /dev/cu.usbmodem*` before and during a capture watching for the node
  disappearing or its suffix changing.

### 4. `p6_capture.py` asserts DTR/RTS on open → resets the ESP32‑S3; the docstring's "does NOT pulse the ESP32 reset" is unverified and likely false
**Type:** host. **Produces:** lost early bytes, sweep-phase reset, false silence at capture start.

- **Evidence:** `tools/p6_capture.py:38` `p = serial.Serial(PORT, 115200,
  timeout=0.2)`. The script never deasserts control lines (`ser.dtr=False`,
  `ser.rts=False`) and never opens exclusively. pyserial **asserts DTR and RTS by
  default on open**; on a USB‑Serial‑JTAG (and on any DTR/RTS→EN/GPIO0 auto-reset
  wiring) that edge can pulse the ESP32 into reset. The docstring
  (`p6_capture.py:6‑8`) explicitly claims *"does NOT pulse the ESP32 reset, so the
  bridge stays up while you power-cycle the Neato"* — but the code takes **none**
  of the steps required to guarantee that.
- **Failure it causes:** Each `p6_capture.py` start may reboot the ESP32 (~1–2 s of
  UART1 down + a fresh sweep starting at index 0). If the operator power-cycles the
  Neato too soon after starting the capture (steps 2→3 of the procedure,
  `P6_SWEEP_HANDOFF.md:71‑72`), the robot's one-shot burst can arrive while the
  ESP32 is still rebooting → captured as silence. (Corroborating detail: the
  surviving file's first banner is `BAUD 460800`, mid-array, i.e. the reader
  attached to an already-running sweep — but a reset on a *later* reader restart
  would silently re-zero the sweep and the reboot window.)
- **Fix / test:** Open with control lines deasserted, e.g.
  `s = serial.Serial(); s.port=PORT; s.baudrate=115200; s.timeout=0.2;
  s.dtr=False; s.rts=False; s.open()` (and set `exclusive=True`). Verify by
  scoping/monitoring EN: opening the capture must not produce a reset pulse. If it
  does, the docstring claim is false and must be corrected.

### 5. No exclusive lock on the tty → the dual-reader condition can silently recur; and the README mischaracterizes what dual-reader actually does
**Type:** host + procedure/analysis. **Produces:** byte *dropouts* (not high-bit garbage); an incorrect confound narrative.

- **Evidence:** `p6_capture.py` opens the port with plain `serial.Serial(...)`
  (`:38`) — **no `exclusive=True`, no `TIOCEXCL`**. Nothing prevents a second
  `p6_capture.py` (or a stray `pio device monitor`) from attaching to the same
  `/dev/cu.usbmodem*`, which is exactly the confound that produced the only-ever
  data (`README.md:18‑23`). The procedure's only guard is a manual
  `pkill -f p6_capture.py` (`P6_SWEEP_HANDOFF.md:44, 71`), which is racy.
- **What dual-reader really does (correcting the record):** On macOS, two
  processes `read()`ing one tty each receive a **nondeterministic disjoint subset
  of whole bytes** — the kernel hands each ready chunk to whichever reader it
  wakes. Byte **values are preserved**; you get **gaps/dropouts**, never bit
  corruption. Since ASCII/text is all `< 0x80`, **dual-reader splitting cannot
  manufacture the high-bit (`>= 0x80`) bytes** that defined the 8377-byte sample.
  The README lists "dual-reader byte-splitting produced pseudo-garbage"
  (`README.md:36`) as a live explanation for the high-bit gibberish — that
  mechanism is **physically impossible**; suspicion for the high bits belongs to
  the wire (contention/inversion/wrong-baud), per finding #6 and `hypothesis-review.md`.
- **Fix / test:** Open the port with `exclusive=True` (pyserial sets `TIOCEXCL`)
  so a second reader fails loudly instead of silently stealing bytes. This removes
  the confound structurally rather than relying on `pkill`.

### 6. The `-DP6_SELFTEST` build injects on GPIO17 into AT91_RXD — a manufacturer of *false gibberish*, and the source of the one contaminated sample
**Type:** firmware/procedure confound (already partly covered by `hypothesis-review.md` H3). **Produces:** false gibberish.

- **Evidence:** `debug_uart.c:109‑119` `selftest_task` writes
  `"P6-SELFTEST %u\r\n"` to `DEBUG_UART` (TX = GPIO17) every 1 s;
  `debug_uart.c:173‑180` starts it whenever `P6_SELFTEST` is defined. The handoff
  wires **GPIO17 (TX) → P6.2 = AT91_RXD** (`P6_SWEEP_HANDOFF.md:21`). The only
  robot data ever seen was captured with this build transmitting *and* freshly
  "swapped" wiring (`README.md:18‑24`).
- **Failure it causes:** With swapped/loopback wiring, GPIO18 (RX) can end up
  sampling a **floating or contended net** — the ESP32's own push-pull TX fighting
  AT91_TXD, or an idle/floating AT91_RXD line — which reads as arbitrary 8-bit
  values **including high-bit bytes**. That is a far better explanation for
  "high-bit gibberish" than a valid robot signal at the wrong baud (the injected
  string is pure ASCII and would appear as readable `P6-SELFTEST` if it looped
  back cleanly). Net effect: the 8377 bytes are quite plausibly **an artifact of
  this build + harness state, not robot output** — which knocks the legs out from
  under the "RX path proven / wiring correct" claim that the whole baud theory
  rests on.
- **Fix / test:** Never diagnose with a build that transmits. Use the receive-only
  build with the robot **disconnected**: any bytes on RX with nothing attached
  convicts self-noise/contention. (Same as `hypothesis-review.md` H3/H5.)

---

## Things I checked that are NOT bugs (so they can be ruled out)
- **RX buffering is adequate:** `uart_driver_install(DEBUG_UART, 8192, 8192, 0,
  NULL, 0)` (`debug_uart.c:164`) — 8 KB RX ring > any plausible boot burst; not a
  drop source on the UART side.
- **`p6_capture.py` file handling is correctly non-destructive:** append-binary +
  timestamped default path (`:36, :43‑52`). The earlier data loss was a *reused
  filename truncation*, since fixed; the current script does not truncate.
- **`p.read(4096)` / `timeout=0.2`** (`:48`) is fine — it returns partial reads
  promptly; not a silence source.
- **`uart_set_baudrate` mid-stream** (`debug_uart.c:136`) is legitimate; the
  following `uart_flush_input` handles the transitional garbage. (Its *timing* is
  the problem in finding #2, not the call itself.)
- **GPIO17/18** are ordinary IO on the ESP32‑S3 (the USB PHY is GPIO19/20; UART0
  console is GPIO43/44) — no strapping/peripheral conflict found in the source for
  UART1's pins.

---

## Bottom line for the investigation
The banner-only capture is **not admissible evidence** for *any* conclusion about
baud or wiring, because at least three independent pipeline defects can blank out
a real burst before it ever reaches the file:
1. it is recorded on the **lossy USB‑Serial‑JTAG secondary console** while the
   full stream goes to the un-captured UART0 (finding #1);
2. the **sweep timing + per-window flush** make catching a one-shot burst at the
   correct rate a <20% coin-flip (finding #2);
3. the **USB host stack** on the shared PHY can drop/rename the capture port
   (finding #3).

**Do the two cheap discriminating tests before another sweep:** (a) capture the
**UART0** port and the USB‑JTAG port *simultaneously* during one power-cycle, and
(b) put a scope/LA on **P6.3** to confirm a signal and read its bit period
directly. Either one bypasses the entire firmware→USJ→reader→file chain that every
prior datapoint depended on.

*No existing capture files were modified. This file was created new.*
