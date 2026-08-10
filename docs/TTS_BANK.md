# TTS Bank: guarded text-to-speech through the sound flash

Turns arbitrary text into a **temporary** ten-slot sound bank, burns it after
an explicit typed confirmation, speaks it with duration-paced `PlaySound`
commands, and restores the persistent BMO bank. This is *not* `PlaySound File`
streaming — it rewrites the sound flash twice per utterance.

> ⚠️ **Flash endurance**: every utterance is two full 770,048-byte flash
> writes (temporary bank + BMO restore). Use sparingly.

## Pipeline

```
text → Colibri espeak-ng WAV → 22050 Hz mono s16le PCM
     → trim silence → peak-normalize (-1 dBFS)
     → split near quiet boundaries across the ten live slots (tiny fades)
     → PCM-only overlay on the persistent BMO bank (headers/directory untouched)
     → validate byte-exactly → preview (per-slot + stitched WAVs)
     → typed "BURN TTS <sha256>" confirmation → Upload sound
     → verify ACK, GetVersion identity, exact live map 0,1,2,3,6,7,8,9,10,19
     → PlaySound per slot, waiting each slot's full declared duration
     → restore BMO bank (hash re-proved from disk) → re-verify
```

Core logic: `neatobmo/tts_bank.py` (hardware-free, fully injectable).
Web wiring: `bmo_web.py` — “TTS” tab + `/tts-bank/*` endpoints.
Tests: `tests/test_tts_bank.py`, `tests/test_tts_controller.py` (fakes only;
no robot contact).

## Slot sequence and capacity

Speech fills slots in this order (long slots first):

| order | ID | capacity |
|---|---|---|
| 1 | 0 | 2.881995 s |
| 2 | 1 | 1.867392 s |
| 3 | 2 | 1.869388 s |
| 4 | 6 | 2.008662 s |
| 5 | 7 | 2.017324 s |
| 6 | 8 | 2.008662 s |
| 7 | 9 | 2.008662 s |
| 8 | 10 | 1.991338 s |
| 9 | 3 | 0.321451 s |
| 10 | 19 | 0.321451 s |

Total ≈ 17.296 s. Longer speech is rejected, never truncated. Shorter
segments are zero-padded to each slot's original declared PCM length.

## Invariants (enforced by `validate_tts_bank`)

- exact file size 770,048 bytes;
- all eight KT directory pages, slot table, record start pages, 16-byte
  record headers, sample counts and PCM byte counts byte-identical to the
  persistent BMO baseline;
- zero byte changes outside the original PCM payload fields;
- every segment fits its slot; no full-scale (clipped) samples;
- preview PCM == built-bank PCM; SHA-256 and additive transport checksum
  recorded; per-slot manifest (unused slots explicitly `silenced` or
  `baseline-bmo`).

A failed validation blocks the burn unconditionally.

## Artifacts (never modified)

- Persistent baseline + restore target:
  `assets/bmo-sound-bank-offline-20260810/DfltSoundLib.BMO.pcm-only.Rev1.0.bin`
  SHA-256 `9d3d82d9275c03fa9f2abb163cdfd9393445737999916f6337d2d6b639b51159`
- Manual/emergency fallback only:
  `assets/neato-xv12-sound-capture-20260810/public-reference/DfltSoundLib.Rev1.0.bin`
  SHA-256 `d3969779a6a195812d72b6859454de004ea45beefdee6f1b5c50a2632564b64a`

The restore path re-reads the artifact from disk and refuses to burn on any
size or hash mismatch.

## Burn confirmation and restore modes

The server only burns the bank generated and validated in the current
operation, and only after the user types exactly:

```
BURN TTS <full-generated-sha256>
```

Restoration is either **automatic** (checkbox, default on — runs even when
playback fails or is stopped) or an explicit **Restore BMO** action. Until a
restore verifies, the UI shows a loud "temporary TTS bank is still installed"
warning; a temporary bank is never left installed silently.

## Playback rules

The firmware does not queue `PlaySound` and a new command interrupts the
current clip. The player sends one documented command per slot and waits that
slot's full declared duration before the next; combined-ID syntax is never
used. Progress (segment, slot, elapsed, remaining, stop/error state) is
exposed via `GET /tts-bank/status`.

## Logs

Every generation, burn, playback, and restore step (hashes, timings,
verification results) is appended to `logs/tts-bank-operations.jsonl`.
