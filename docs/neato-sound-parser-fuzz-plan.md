# Sound-bank parser fuzz plan (memory-safety hunt)

Status: **plan — no destructive writes authorized by this document.**
Target: XV-12 `WTD41611DD-0037829-P`, Cruz Rev113/P, mainboard 7.1.
Prefer a **donor Cruz/P board**; the live board is only in scope for the
low-risk tier below, under an explicit recovery invariant.

## TL;DR

The sound bank is the one place we can place arbitrary, **unencrypted,
unauthenticated** bytes into memory that the stock ARM926 application then
parses. That makes it the only software-only candidate for native code
execution that does not require the AES key. But it is a *write primitive*,
not an exploit: there is no demonstrated memory-safety bug in the stock
sound parser yet, and the parser itself is inside the encrypted application,
so this is a **black-box fuzz against the parser using P6 as a crash oracle**,
not a guaranteed win.

This document:

1. Fixes the parser attack surface with real byte offsets.
2. Ranks the candidate bugs and the risk of each mutation tier.
3. Specifies a fail-closed fuzz harness and an iteration protocol.
4. Defines crash-oracle classification and a recovery invariant.

## Why the sound upload is the right channel

Proven, in-repo:

- `Upload sound` writes the caller's bytes to NAND region `0x400000` with no
  content validation. A one-bit mutation at offset 4108 produces an identical
  P6 sequence under `noburn` (`captures/20260811_B03_sound_noburn_onebit_p6.log`);
  the receiver is transport-only.
- The bank is **not encrypted** (`DfltSoundLib.Rev1.0.bin` is a plain `KT`
  directory + PCM). The application image, by contrast, is AES-CBC encrypted
  (`docs/neato-envelope-crypto.md`), so the code region is unreachable without
  the key, while the sound region is fully attacker-controlled.
- The ESP32 `/soundbank` gate is integrity-only, not an allowlist: exact
  770048 bytes, a `KT` prefix, and the SHA-256 header must match the bytes you
  send (`esp32-body/src/neato_audio.c:288`). Any 770048-byte `KT…` blob passes.
  The SHA allowlist lives only in the Python layer (`neatobmo/tts_bank.py`,
  `bmo_web.py`). Direct USB bypasses even that.
- The firmware **does parse** the bank: `PlaySound 0..20` accepts exactly
  `0,1,2,3,6,7,8,9,10,19`, and two custom layouts that changed the directory
  "activated only slot 0" (`docs/SOUND_BANK_UPDATE.md:82`). So the directory /
  page table / record headers are read and interpreted by the application.

## Proven parser facts (measured, 2026-08)

Bank = 770,048 bytes = 1,504 × 512-byte pages.

```
page 0 (offset 0x0000):
  0x00..0x01  "KT" magic
  0x04..0x05  u16 declared value = 20  (entry_count = declared + 1 = 21)
  0x08..0x31  21 × u16 page-index table (one entry per sound id 0..20)
pages 1..7   additional "KT" directory pages (offsets 0x0200..0x0FFF)
pages 8..    records (16-byte header + PCM), one per live slot
```

Table entries (page 0, 2 bytes each, LE):

| sound id | table offset | value (start_page) | record header offset |
|---:|---:|---:|---:|
| 0 | `0x0008` | 8 | `0x001000` |
| 1 | `0x000A` | 257 | `0x020200` |
| 2 | `0x000C` | 418 | `0x034400` |
| 3 | `0x000E` | 580 | `0x048800` |
| 6 | `0x0014` | 608 | `0x04c000` |
| 7 | `0x0016` | 782 | `0x061c00` |
| 8 | `0x0018` | 956 | `0x077800` |
| 9 | `0x001A` | 1130 | `0x08d400` |
| 10 | `0x001C` | 1304 | `0x0a3000` |
| 19 | `0x002E` | 1476 | `0x0b8800` |
| 4,5,11..18,20 | — | 0 (empty → "out of range") | — |

Record header (16 bytes at `record_offset`):

```
+0  u16 flags        = 0x0101
+2  u16 sample_rate  = 22050
+4  u32 sample_count
+8  u32 pcm_byte_count
+12 u32 reserved     = 0
```

Reference record 0: `sample_count=63548`, `pcm_byte_count=127096`
(`= 63548*2`), capacity 127,488 bytes. The Python mirror
`record_ranges_from_bytes` (`neatobmo/tts_bank.py:150`) rejects any deviation
from these invariants — but that is a *host-side safety mirror*, not proof the
firmware performs the same checks.

## Attack surface & candidate bugs

The parser consumes three attacker-controlled field classes. For each, the
candidate firmware bug and its exploit value:

### 1. `entry_count` (offset `0x04`, u16; firmware likely reads `declared + 1`)

- Value `0xFFFF` → 65,536 entries → a 128 KiB table read starting at `0x0008`.
  - If the firmware **reads** the table unbounded: OOB read, but still inside
    the 770 KiB bank (low value).
  - If the firmware **allocates** a RAM table sized by `entry_count` and then
    *writes* decoded entries into it: stack/heap overflow → the real prize.
- Value `0` or `1` → under-allocated table; a later `PlaySound n` with `n` above
  the table end is an OOB index into the directory.

### 2. Page-table entries (offset `0x08 + 2*id`, u16)

Each is a `start_page`; the record offset is (almost certainly) `start_page*512`,
and capacity is (almost certainly) derived from the next non-empty entry.

- Point a slot at a directory page `0..7` → header is `KT…` garbage; `flags !=
  0x0101`; may be silently ignored or may be interpreted (huge/small `pcm_byte_count`).
- Point a slot at `>= 1504` → `start_page*512` reads past the bank; if the
  firmware uses an unbounded 32-bit offset it reads/wraps into unrelated NAND.
  `0xFFFF` → offset `0xFFFF*512 ≈ 32 MiB` above `0x400000`.
- **Non-monotonic / overlapping / duplicated** start pages → if capacity is
  `(end_page - start_page)*512` in unsigned arithmetic, `end < start` wraps to a
  ~4 GiB capacity. Any copy/read sized by that wraps or faults.
- Note: the "only slot 0 activated" result means a *structurally valid but
  reordered* table does not hang boot — the scanner tolerates reordering. That
  is weak evidence the table is read at PlaySound time, not boot time. It says
  nothing about *wildly out-of-range* values.

### 3. Record header fields (per-slot, 16 bytes)

- `pcm_byte_count` (u32) = `0xFFFFFFFF` or any value ≫ capacity → if the
  firmware does `memcpy(ram_buf, nand_pcm, pcm_byte_count)`, this is a
  **direct buffer overflow** into the DAC/playback buffer → the top candidate.
- `sample_count` (u32) huge → if firmware computes `sample_count * 2` and that
  overflows 32 bits before allocation, a small buffer is allocated and a large
  read follows (classic integer-overflow → heap overflow).
- `sample_count` vs `pcm_byte_count` mismatch (`pcm != 2*samples`) → which field
  is trusted for allocation vs copy is the bug discriminator.
- `sample_rate != 22050` → resampler/divider path; a 0 or huge rate may divide
  by zero or over-run.
- `reserved != 0`, `flags != 0x0101` → unknown parser branches; lowest value,
  likely clean reject, but cheap to test.

## Risk tiers

**Tier 1 — record-header mutations on a single slot (directory/table intact).**
The table still resolves the slot, so the robot should stay responsive; a crash
is scoped to the mutated slot's `PlaySound` and is recoverable by re-uploading a
known-good bank. *This is the only tier acceptable on the live board*, and only
because the app console (and thus `Upload sound` recovery) is expected to stay up.

**Tier 2 — directory/table mutations (entry_count, page-table values).**
These can change *which* bytes the firmware treats as structure at a time we
cannot yet pin (boot scan vs PlaySound scan). If the bank is scanned at boot, a
hang can make the console unreachable, and — because the factory app shares the
same NAND sound region — **BACK→factory does not provide a clean sound region.**
Do this tier **only on a donor board**.

**Tier 3 — size / non-`KT` / non-512-aligned banks.** Only possible over direct
USB (the ESP32 bridge hard-rejects these). Highest brick risk, lowest expected
signal. Donor only, last.

## Fuzz harness (proposed: `tools/neato_sound_fuzz.py`)

Model it on `tools/neato_sound_burn_exact.py` and `tools/neato_code_noburn.py`
(fail-closed, identity-gated, typed confirmation, JSON result). Responsibilities:

1. **Generator** — derive every test image from the exact vendor-default bank
   (`d3969779…b64a`) with one mutation at a time. Emit the image + a manifest
   `{base_sha256, offset, field, old_bytes, new_bytes, output_sha256}`.
   Never accept an arbitrary input file as a fuzz target.
2. **Burner** — reuse `SerialTransport.send_binary("Upload sound", payload)`
   (`neatobmo/transport.py:50`). Direct USB only for Tier 2/3; the ESP32 bridge
   is acceptable for Tier 1 (size + `KT` are preserved).
3. **Observer** — before/after `GetVersion`; targeted `PlaySound <id>` and a
   full `0..20` sweep; simultaneous P6 capture via `tools/p6_capture.py`.
4. **Recovery** — after every iteration, burn the vendor-default bank and
   re-verify `GetVersion` + the ten-slot map. Fail closed if recovery does not
   confirm.

CLI sketch:

```sh
python3 tools/neato_sound_fuzz.py tier1 \
  --field pcm_byte_count --slot 0 --values 0xFFFFFFFF,0x00000000,0x7FFFFFFF \
  --port /dev/cu.usbmodemXXXX --recovery-bank <vendor-default.bin> \
  --p6 <captures/p6_<epoch>.log> --execute
```

Every run records the exact image SHA-256 and a typed confirmation phrase of the
form `FUZZ SOUND <sha256>` before the port opens, matching the repo's existing
gate style.

## Iteration protocol (per test case)

1. Build the single-mutation image offline; record its SHA-256 and manifest.
2. Pre-burn identity: `GetVersion` must contain `WTD41611DD` and an approved
   software string before any bytes move.
3. Burn via `Upload sound`; require ENQ → payload+checksum → ACK (`0x06`).
4. Immediately `GetVersion` (health). If it fails to respond → **boot-time parse
   is implicated; stop the tier and recover on donor**.
5. Targeted `PlaySound <mutated-id>` and the `0..20` sweep; record every reply
   and whether each slot is accepted.
6. Read P6 for the crash signature (next section).
7. Recover: burn vendor-default, re-verify identity + slot map.
8. Append the JSON result; never overwrite a prior result file.

## Crash-oracle classification (P6)

Record the P6 stream for every iteration and classify:

| P6 / USB signature | Interpretation |
|---|---|
| `Data abort` / `Prefetch abort` / `Undefined instruction` / a reset-vector dump | Memory-safety bug reached → **stop, preserve, escalate** |
| `Watchdog` reset / spontaneous reboot / USB re-enumeration without a command | Possible crash → re-run once to confirm determinism |
| Silent hang (no P6, no USB terminator) on a `PlaySound` | Possible infinite loop / DMA stall → Tier 2 implication |
| `out of range` / clean NAK / healthy `GetVersion` | Clean reject — parser bounds-checked this field; move on |

Any confirmed fault is the deliverable that justifies the readback/key path in
`docs/neato-nand-readback-and-key-extraction-runbook.md`; it is **not** yet
control of the program counter.

## What this can and cannot deliver

- **Can:** prove or disprove that the stock sound parser is memory-unsafe, and
  locate *which* field, on *which* slot, triggers it. That is a concrete,
  evidence-backed justification for the expensive hardware acquisition.
- **Cannot, by itself:** turn a crash into controlled ARM926 code execution.
  Without a debugger (P10 found no stable TAP) or the plaintext, converting a
  fault into RIP control is not feasible. Treat a confirmed crash as a pointer
  toward the parser, not as an end.

## Safety boundaries (non-negotiable)

- Never short **J3/ERASE** — permanent brick.
- Tier 2/3 and any size/non-`KT` work: **donor board only**.
- Never overwrite an existing result/capture file (append-only, like
  `tools/p6_capture.py`).
- Recovery is not optional: a test case is "done" only after the vendor-default
  bank is re-burned and the ten-slot map is re-verified.
- One variable per iteration; never stack mutations.

## Evidence links

- `docs/SOUND_BANK_UPDATE.md` — proven PCM-only constraint; slot map.
- `docs/neatoos-execution-probe.md` — the parallel code-region gate findings.
- `docs/neato-envelope-crypto.md` — why the code region is off-limits and the
  sound region is the open input.
- `captures/20260811_B03_sound_noburn_onebit_p6.log` — transport-only proof.
- `captures/20260811_C01_original_sound_burn_p6.log` — region `0x400000` write.
