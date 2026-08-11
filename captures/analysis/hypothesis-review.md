# Adversarial review — refuting "correct wiring, wrong baud"

**Reviewer stance:** challenge, not confirm. Claim under test:
> "The wiring is correct; the robot is transmitting; we just have the wrong baud
> (115200 is unverified)."

**Verdict up front:** the claim is **weakly supported and actively contradicted**
by the current reproducible evidence. It rests on a single lost, doubly-confounded
sample; every clean capture since is **silent**, and silence is the one thing a
mere baud mismatch **cannot** produce. Confidence in "baud mismatch" as the
blocker: **~10–15% (LOW)**. See verdict section.

---

## What the evidence actually is (verified this session)

**Observation A — the 8377-byte "gibberish":** a single sample, now `rm`'d and
unrecoverable, captured under **three** simultaneous confounds:
1. two `p6_capture.py` readers on one serial port,
2. the `-DP6_SELFTEST` build actively transmitting on GPIO17 into the net,
3. wiring freshly "swapped" per a later-retracted suggestion.

**Observation B — silence:** every clean single-reader capture since = **zero
robot bytes**, across a receive-only baud sweep and **two** Neato power-cycles.

**Surviving capture, byte-level (analyzed with python):**
`captures/2026-08-11T1229_p6_sweep_bannersonly.txt` = 8843 bytes.
- **high-bit bytes (>=0x80): 0**
- non-whitespace control bytes: 0
- distinct byte values: **18**, every one from the banner charset `=BAUD 0-9`+ws
- 302 `==== BAUD N ====` banners over the 11 sweep rates (~27 cycles each)
- **non-whitespace content between banners: 0 segments.** It is *pure* banner +
  CRLF. Not one stray byte. The line was dead at all 11 bauds, both cycles.

**Two facts from source that reshape the confounds:**

- `tools/p6_capture.py` reads raw with `p.read(4096)` and appends binary. Two
  readers on one tty therefore split the stream into **disjoint whole-byte
  subsets** — each reader drops bytes, but **no byte is bit-corrupted**.
  → **Dual-reader splitting cannot manufacture high-bit garbage.** It produces
  *dropouts/gaps*, not values >=0x80. This partly exonerates confound #1 as a
  cause of Observation A and is a load-bearing point below.
- `-DP6_SELFTEST` transmits the **ASCII** string `"P6-SELFTEST %u\r\n"` (all
  bytes < 0x80). A clean same-peripheral loopback or crosstalk of it would show
  **readable "P6-SELFTEST"** text — not high-bit gibberish. So the 8377 bytes
  were *not* the self-test arriving intact either.

---

## Hypotheses for A (garbage-under-confounds) and B (clean silence)

| # | Hypothesis | Explains A (garbage)? | Explains B (silence)? | Prior | Cheap discriminating test |
|---|------------|-----------------------|------------------------|-------|---------------------------|
| **H1** | **Correct wiring, wrong baud** (the claim) | Yes — wrong-baud sampling of a live line yields ~50% high-bit pseudo-random bytes | **No.** A wrong baud on a *live* line still yields *garbage bytes at every wrong rate and readable text at the right one* — never zero across 11 rates. Silence refutes it. | Low | Baud sweep already run → **failed** (silence). Decisive test: scope P6.3 for a live signal + measure bit period (H-Scope below). |
| **H2** | **Floating / loose RX (GPIO18) or lifted GND** — intermittent contact | Yes — a floating CMOS input picks up noise → random framing → high-bit bytes | **Yes** — when the same marginal joint fully opens, the input idles/settles → zero bytes. Garbage→silence = a *contact change*, exactly what the handoff admits ("P6 connection came loose"). | **High** | Continuity/DMM: P6.4↔ESP32 GND and P6.3↔GPIO18 (<1 Ω, wiggle-test). DMM on GPIO18 to GND: floating/noisy vs steady idle-high. |
| **H3** | **Self-test injection / bus collision generated the bytes** — GPIO17 driving the same net as AT91_TXD, or ringing | Yes — a driven-against-driven collision or ESP32 TX coupling into RX makes high-bit hash | **Yes** — remove the SELFTEST build (done for the sweep) and the source of bytes is gone → silence. | **High** | Re-flash SELFTEST **with the robot disconnected**; if RX shows bytes with nothing attached, the ESP32 is self-generating. Also: normal bridge, robot off — any bytes = self-noise. |
| **H4** | **AT91 simply doesn't print on cold boot** (DBGU quiet in this fuse/mode, or app console is a different UART, or needs a reset/trigger) | No — needs A to be noise/injection (H2/H3) | **Yes** — no source, so every baud is silent regardless of wiring or baud | **High** | Scope P6.3 during power-on (H-Scope): static line = not transmitting. Try holding/pulsing reset; try sending a char to AT91_RXD to elicit a ROM prompt. |
| **H5** | **ESP32-side RX fault** — UART1/GPIO18 not actually receiving; "RX proven" was never proven | If the 8377 bytes were H2/H3 noise, RX was *never* validated as a real path | **Yes** — a dead RX is silent at every baud | Medium | **Loopback self-test:** jumper GPIO17→GPIO18, run SELFTEST, expect readable `P6-SELFTEST N` in USB capture. No text ⇒ RX/UART/console chain is broken. |
| **H6** | **Dual-reader byte-splitting made valid bytes look like garbage** | **No** (refuted) — raw whole-byte reads drop bytes, they don't set high bits | No | **Very low** | N/A — killed by `p6_capture.py` source. At most it explains *why text looked mangled*, never high-bit values. |
| **H7** | **Inverted / wrong idle-state or level mismatch** (idle-low seen as idle-high, or non-3.3 V swing) | Yes — every frame mis-frames → garbage | Partial — usually still yields *garbage*, not silence, if a signal exists | Low | Scope idle level of P6.3: TTL DBGU idles **high** (~3.3 V). Idle-low or mid-rail ⇒ inversion/level/float. |
| **H8** | **Wrong header / P6 is not the live DBGU at that phase** | No (A = noise) | **Yes** — tapping an inactive pin is silent | Low–Med | Scope/DMM sweep the P6 pins (and candidate DBGU pads) at power-on for the one that toggles; confirm pinout against U29 datasheet. |
| **H9** | **Sweep sampling miss** — the one-shot boot burst never landed in a window | No | Partial — but a burst hitting a *wrong-baud* window still records *garbage*; total silence across ~27 cycles × 2 power-cycles makes a pure timing miss very unlikely | Low | Longer capture; or normal fixed-baud bridge armed *before* power-on so no window gap exists. |

**Reading of the table:** the high-prior explanations for the garbage→silence
transition are **H2 (loose/floating wire), H3 (injection artifact), and H4
(AT91 not transmitting)** — none of which is baud. H1 is the *only* live
hypothesis that the current silence directly **contradicts**.

---

## The core logical flaw in the claim

The handoff reasons: *"garbage not silence ⇒ correct wiring, wrong baud."*
That inference had force **only while the garbage was reproducible**. The state
has since flipped to **silence**, and the same logic, run forward, points the
other way: **a wrong baud on a genuinely live, correctly-wired line produces
garbage, never silence.** So the present, clean, reproducible evidence is
*evidence against* H1, not for it. The one garbage sample that seeded H1 was
produced under injection + a just-disturbed harness and cannot be reproduced —
its provenance (real AT91 output vs. floating-line noise vs. ESP32 self-noise)
is now **unknowable**. "RX path proven / wiring correct" inherits all of that
uncertainty: it was asserted from those same 8377 bytes.

---

## Recommended next experiment (highest info-gain per effort)

**H-Scope — put a scope or logic analyzer (or, minimally, a DMM) directly on
P6.3 (AT91_TXD) referenced to P6.4 (GND), then power-cycle the Neato.**

This observes the **signal source itself**, bypassing the entire
ESP32 → reader → file chain that every prior datapoint depended on. One capture
discriminates almost the whole table:

| Outcome at P6.3 on power-on | Implies | H-verdict |
|---|---|---|
| Clean UART bursts toggling; measurable bit period → **baud = 1/bit-time** | Robot **is** transmitting; read the true rate straight off | **Confirms** a baud/RX-chain issue; hands you the answer, retiring the sweep. H1 or H5 |
| Line **static idle-high**, no toggling at power-on | AT91 not printing on this pin/phase | **Kills H1**; supports **H4/H8** |
| Line **floating / noisy / mid-rail**, or dead only when wire is wiggled | Bad contact / lifted GND | Supports **H2/H7**; kills H1 |

**If no scope/LA is on hand,** the fallback decisive test is **H5's loopback
self-test** (jumper GPIO17→GPIO18, flash `-DP6_SELFTEST`, expect readable
`P6-SELFTEST N` in the USB capture). It costs one flash and proves — or
demolishes — the never-validated assumption that GPIO18 RX + UART1 + console +
file actually carry bytes. Pair it with a **robot-disconnected** run of the same
build: any bytes with nothing attached convicts **H3** (self-noise) and retro-
actively explains the 8377 bytes without any AT91 involvement at all.

**Predicted outcomes by hypothesis** (H-Scope): H1→live toggling at some rate;
H2→noisy/intermittent, contact-dependent; H3→**clean** P6.3 (the garbage was
never here — it was on the ESP32 side); H4→static idle-high; H5→clean live P6.3
(fault is downstream in the ESP32); H7→wrong idle level/inversion; H8→P6.3 quiet
but another pad toggles.

---

## Confidence verdict

- **"Baud mismatch is the blocker": ~10–15% (LOW).** It is contradicted by the
  present silence, cannot explain the garbage→silence transition, and its sole
  supporting datapoint is lost and triply-confounded. It should **not** be the
  working theory.
- **Most probable actual blocker:** a **physical-layer / source problem** —
  **H2 (loose or floating RX / lifted GND, ~35%)**, **H4 (AT91 not emitting on
  cold boot, ~25%)**, **H3 (the 8377 bytes were ESP32 self-noise/injection,
  ~20%)** — with H5/H7/H8 splitting most of the remainder. These are not
  mutually exclusive (a loose harness *and* a quiet DBGU can both be true).
- **Do not reflash the normal build to a "found" baud, and do not keep iterating
  the sweep,** until H-Scope (or the loopback self-test) establishes that a
  signal exists on P6.3 at all. The sweep can only ever succeed if there is a
  live signal to decode; the current evidence says there isn't one reaching RX.

---

*No existing capture files were modified; this analysis file was created new.*
