# P6 capture log — data, timeline, and the confounds

Raw captures from tapping the Neato XV-12 **P6 debug UART** with the ESP32-S3.
See `../docs/P6_SWEEP_HANDOFF.md` for the live procedure and `../docs/neato-hardware-access.md`
for the P6 pinout. **Capture rule: never truncate. `tools/p6_capture.py` now
appends to timestamped files** — an earlier reused-filename truncation destroyed
our only robot sample (below).

## Files here
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
