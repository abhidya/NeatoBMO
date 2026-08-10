# Sound burn forensics: why the custom bank exposed only the first slots

Scope: XV-12 `WTD41611DD-0037829-P`, firmware `2.4.15667`, mainboard `7.1`.
This note reconciles the public sound-bank record format with the post-burn
failure where only the first one or two sound IDs were accepted.

## TL;DR

The public `DfltSoundLib.Rev1.0.bin` is parseable as a 512-byte-paged sound
library: eight `KT` directory pages, a page-0 sound-ID table, and one 16-byte
record header per live slot:

```text
u16 flags        = 0x0101
u16 sample_rate  = 22050
u32 sample_count
u32 pcm_byte_count
u32 reserved     = 0
then pcm_byte_count bytes of 22050 Hz mono s16le PCM
then zero padding to the next record boundary
```

That offline format inference was not sufficient for firmware acceptance. The
two failed custom images changed the 16-byte record headers, especially
`sample_count` and `pcm_byte_count`. The successful image kept the vendor
directory, table, record start pages, 16-byte headers, declared lengths, and
non-PCM padding byte-for-byte, replacing only bytes inside the original PCM
regions.

Practical rule: treat record headers and directory metadata as firmware-owned.
Only edit original PCM spans, and pad shorter replacement audio with zero-valued
PCM to the original declared `pcm_byte_count`.

## Raw observations

### Baseline / recovery

- Public reference image:
  `assets/neato-xv12-sound-capture-20260810/public-reference/DfltSoundLib.Rev1.0.bin`
  SHA-256 `d3969779a6a195812d72b6859454de004ea45beefdee6f1b5c50a2632564b64a`.
- The default bank was successfully written and verified with live IDs
  `0,1,2,3,6,7,8,9,10,19`
  (`.omx/specs/autoresearch-neato-xv12-sound-bank/bmo-burn-experiment-20260810.md:10`).
- `docs/SOUND_BANK_UPDATE.md` now lists both the original image and the
  PCM-only BMO image as successfully burned and verified with that same live
  map (`docs/SOUND_BANK_UPDATE.md:11`).

### Failed fixed-page custom image

- Artifact:
  `assets/bmo-sound-bank-offline-20260810/DfltSoundLib.BMO.Rev1.0.bin`
  SHA-256 `c17d42ec605efde8affd3d184ce41a2fd08aae80795633c4d6fe6b3e6750900f`.
- Upload path completed: transfer ended with `ACK + 0x1a`, and `GetVersion`
  stayed healthy (`.omx/specs/autoresearch-neato-xv12-sound-bank/bmo-burn-experiment-20260810.md:19`).
- The curated burn note says post-write accepted IDs were `0` only and
  `1..20` were out of range
  (`.omx/specs/autoresearch-neato-xv12-sound-bank/bmo-burn-experiment-20260810.md:22`).
- The preserved raw profile is slightly different: `sound_burn_post_profile.json`
  shows plain replies for `PlaySound 0` and `PlaySound 1`, then out-of-range
  for `2`, `3`, and `4..20`
  (`sound_burn_post_profile.json:11`, `sound_burn_post_profile.json:16`,
  `sound_burn_post_profile.json:21`, `sound_burn_post_profile.json:54`).

The safest reconciliation is: at least the first slot survived, possibly the
first two in one raw sweep, but activation stopped before ID 2/3 instead of
preserving the ten-slot library.

### Failed compact custom image

- Artifact:
  `assets/bmo-sound-bank-offline-20260810/DfltSoundLib.BMO.compact.Rev1.0.bin`
  SHA-256 `a7bdb1142c627d44a695f6cb82f4389521ee2ea1068dd491c86414e8627ac848`.
- Upload path completed with clean `ACK + 0x1a`; `GetVersion` remained healthy
  (`.omx/specs/autoresearch-neato-xv12-sound-bank/bmo-burn-experiment-20260810.md:59`).
- Post-write still exposed only the initial accepted region, summarized in the
  curated note as ID `0` only
  (`.omx/specs/autoresearch-neato-xv12-sound-bank/bmo-burn-experiment-20260810.md:60`).
- `assets/bmo-sound-bank-offline-20260810/compact-blank-pages-between-records.json`
  is `[]`, so the compact image removed all complete all-zero pages between
  records.

This falsifies the first simple theory that "the first full blank page ends the
library" is the sole cause.

### Successful PCM-only custom image

- Artifact:
  `assets/bmo-sound-bank-offline-20260810/DfltSoundLib.BMO.pcm-only.Rev1.0.bin`
  SHA-256 `9d3d82d9275c03fa9f2abb163cdfd9393445737999916f6337d2d6b639b51159`.
- Upload completed with clean `ACK + 0x1a`; `GetVersion` passed; accepted IDs
  were exactly `0,1,2,3,6,7,8,9,10,19`
  (`.omx/specs/autoresearch-neato-xv12-sound-bank/bmo-burn-experiment-20260810.md:82`,
  `.omx/specs/autoresearch-neato-xv12-sound-bank/bmo-burn-experiment-20260810.md:85`).
- Validation reports all of these invariants as true:
  directory unchanged, record start pages unchanged, record headers unchanged,
  non-PCM bytes unchanged, all live IDs present, PCM tails zero-silenced, and
  no clipping (`assets/bmo-sound-bank-offline-20260810/pcm-only-validation-report.json`).
- `assets/bmo-sound-bank-offline-20260810/report.md` documents the rule:
  preserve every directory byte, original start page, 16-byte record header, and
  original padding byte; replace only exact original PCM payload bytes
  (`assets/bmo-sound-bank-offline-20260810/report.md:25`).

## Record metadata comparison

The stock and PCM-only successful images have identical structural metadata:

| ID | stock start-end | stock samples | stock PCM bytes | PCM-only samples | PCM-only PCM bytes |
|---:|---:|---:|---:|---:|---:|
| 0 | 8-257 | 63548 | 127096 | 63548 | 127096 |
| 1 | 257-418 | 41176 | 82352 | 41176 | 82352 |
| 2 | 418-580 | 41220 | 82440 | 41220 | 82440 |
| 3 | 580-608 | 7088 | 14176 | 7088 | 14176 |
| 6 | 608-782 | 44291 | 88582 | 44291 | 88582 |
| 7 | 782-956 | 44482 | 88964 | 44482 | 88964 |
| 8 | 956-1130 | 44291 | 88582 | 44291 | 88582 |
| 9 | 1130-1304 | 44291 | 88582 | 44291 | 88582 |
| 10 | 1304-1476 | 43909 | 87818 | 43909 | 87818 |
| 19 | 1476-1504 | 7088 | 14176 | 7088 | 14176 |

The failed fixed-page image kept original start/end pages but changed every
slot's declared length:

| ID | failed fixed start-end | failed fixed samples | failed fixed PCM bytes | zero padding |
|---:|---:|---:|---:|---:|
| 0 | 8-257 | 60420 | 120840 | 6632 |
| 1 | 257-418 | 39686 | 79372 | 3044 |
| 2 | 418-580 | 20214 | 40428 | 42500 |
| 3 | 580-608 | 6615 | 13230 | 1090 |
| 6 | 608-782 | 42557 | 85114 | 3958 |
| 7 | 782-956 | 37485 | 74970 | 14102 |
| 8 | 956-1130 | 42998 | 85996 | 3076 |
| 9 | 1130-1304 | 29106 | 58212 | 30860 |
| 10 | 1304-1476 | 43659 | 87318 | 730 |
| 19 | 1476-1504 | 6615 | 13230 | 1090 |

The failed compact image changed both the page table/start pages and every
slot's declared length:

| ID | failed compact start-end | failed compact samples | failed compact PCM bytes | zero padding |
|---:|---:|---:|---:|---:|
| 0 | 8-245 | 60420 | 120840 | 488 |
| 1 | 245-401 | 39686 | 79372 | 484 |
| 2 | 401-480 | 20214 | 40428 | 4 |
| 3 | 480-506 | 6615 | 13230 | 66 |
| 6 | 506-673 | 42557 | 85114 | 374 |
| 7 | 673-820 | 37485 | 74970 | 278 |
| 8 | 820-988 | 42998 | 85996 | 4 |
| 9 | 988-1102 | 29106 | 58212 | 140 |
| 10 | 1102-1273 | 43659 | 87318 | 218 |
| 19 | 1273-1504 | 6615 | 13230 | 105026 |

## Ranked hypotheses

### 1. Highest confidence: firmware indexes/validates by vendor-declared record metadata, not just parseable headers

Evidence:

- Both failed images changed `sample_count` and `pcm_byte_count` in the 16-byte
  record header for every replaced slot.
- The compact image removed all inter-record blank-page gaps and still failed.
- The successful PCM-only image preserved the complete vendor directory,
  original start pages, all 16-byte record headers, declared lengths, and
  non-PCM padding, while changing only PCM bytes.
- The upload protocol accepted all three custom images at the transport level,
  so the failure is not explained by checksum/framing alone.

Inference:

Firmware 2.4.15667 likely has a sound-library activation or lookup path that
depends on the original declared record metadata. It may build an in-memory
table from those exact lengths, compare against another directory/integrity
source, or reject/stop enumerating when metadata differs from an expected
layout.

Minimal safe fix:

Keep the PCM-only rule. Do not update sample counts, PCM byte counts, start
pages, slot table entries, directory bytes, or padding.

### 2. Medium confidence: there is an unparsed directory/check/index field tied to original page starts and lengths

Evidence:

- The visible page-0 table is not the whole acceptance story: compact page
  table edits produced a structurally parseable image with no blank gaps, but
  the robot still exposed only the first region.
- The successful image changed no directory byte outside PCM spans.
- `tools/neato_sound_bank.py` explicitly treats the page-0 table as a strong
  inference rather than hardware proof (`tools/neato_sound_bank.py:134`).

Inference:

The eight `KT` pages probably contain more than the simple page-0 sound-ID
table we currently parse. One of those bytes may encode layout consistency,
record count boundaries, checks, class flags, or a factory directory that must
match the original start pages and lengths.

Next evidence to prove/disprove:

Write a read-only analyzer that diffs all eight `KT` pages field-by-field
between stock, fixed, compact, and PCM-only images. No robot write is needed.

### 3. Medium-low confidence: the raw `0/1` vs curated `0 only` discrepancy is a logging/association issue, not a different root cause

Evidence:

- The raw `sound_burn_post_profile.json` shows IDs `0` and `1` accepted by reply
  text, then ID `2` and higher out of range.
- Later curated docs summarize both failed images as only ID `0` valid.
- Either way, the library truncates before the known ten-slot map and before
  the third BMO Video Games segment in slot `2`.

Inference:

The discrepancy could come from an older failed artifact, partial sweep,
missing `accepted` booleans on IDs `0` and `1`, or a later manual summary error.
It does not change the main conclusion: metadata-changing builds do not
preserve the library map.

Next evidence to prove/disprove:

If live hardware is used again, save the complete JSON output from
`tools/neato_sound_burn_exact.py` for each burned SHA. That output binds image
hash, ACK/NAK state, `GetVersion`, and full `PlaySound 0..20` sweep in one
object (`tools/neato_sound_burn_exact.py:92`, `tools/neato_sound_burn_exact.py:147`).

### 4. Low confidence: blank-page scanning contributes to the first failure but is not sufficient

Evidence:

- The fixed-page failed image has complete zero pages after early slots; slot 0
  alone has twelve full blank pages before page 257.
- The compact failed image has no inter-record blank pages and still fails.

Inference:

Blank pages may still be one rejection signal or an accelerator for early
termination, but they cannot be the sole activation rule.

## What is evidence vs speculation

Evidence:

- The 16-byte record parser is implemented and tested in
  `tools/neato_sound_bank.py:155`.
- The failed fixed-page builder rewrites each 16-byte header with replacement
  sample counts and PCM byte counts (`tools/neato_sound_bank.py:331`).
- The failed compact builder rewrites the page table/start pages and each
  16-byte header (`tools/neato_sound_bank.py:393`, `tools/neato_sound_bank.py:427`).
- The successful PCM-only builder writes only inside the original PCM span and
  zero-pads to the original `pcm_byte_count` (`tools/neato_sound_bank.py:461`,
  `tools/neato_sound_bank.py:490`).
- Live burn notes show failed metadata-changing layouts and successful
  PCM-only layout.

Speculation:

- The exact firmware routine that invalidates later IDs is still unknown.
- We do not yet know whether the firmware compares against hidden directory
  fields, uses a cached count, scans physical records, validates checksums, or
  has hardcoded length expectations.
- We do not have a direct flash readback proving the installed bytes after each
  burn.

## Current operating constraint

For this robot/firmware, the sound bank is customizable only within the existing
PCM fields unless another live experiment proves a broader envelope. Treat the
public bank as a fixed structural container, not a general-purpose rebuildable
sound-library format.
