# BMO speaks: automatic TTS through the sound flash

Type text → BMO says it. Long text is packed into ≈17 s banks at sentence
boundaries and burned+spoken chunk by chunk. The speech bank **persists**
(no restore write per utterance); "Bring back BMO sounds" reinstalls the
BMO bank on demand. Chat replies speak the same way when the voice selector
is set to 🤖 robot.

> Each chunk is one 770,048-byte flash write. There is no per-utterance
> confirmation — the gates are internal and automatic (below).

## Pipeline

```
text → sentence units → greedy chunk packing (fit test = real slot planning)
     → espeak-ng WAV (Colibri server, or local fallback with identical
       voice/speed/pitch: en+f4, -s 160, -p 70)
     → 22050 Hz mono s16le, silence-trimmed, peak-normalized (-1 dBFS)
     → boundary-aware split across the ten live slots, tiny fades
     → PCM-only overlay on the BMO baseline → byte-exact validation
     → Upload sound (USB direct, or relayed via ESP32 POST /soundbank)
     → silent GetVersion identity check (no audible sweep)
     → PlaySound per slot, waiting each slot's full declared duration;
       each reply doubles as slot verification ("out of range" ⇒ abort)
     → next chunk … → last bank stays installed
```

Core: `neatobmo/tts_bank.py` (hardware-free, injectable).
Web: `bmo_web.py` — “TTS” tab + `/tts-bank/speak|status|stop|restore`.
ESP32: `esp32-body/src/neato_audio.c` `POST /soundbank` relays the
`Upload sound` ENQ/checksum/ACK transaction over USB, so speech works
end-to-end when the robot hangs off the ESP32 instead of the Mac.

## Automatic internal gates (not UX gates)

- The BMO baseline artifact is re-hashed before every build; a mismatch
  aborts. The artifacts themselves are never written.
- Every generated bank passes `validate_tts_bank` before any burn: exact
  size, untouched directory/slot table/record headers, zero changes outside
  the original PCM fields, per-slot fit, no clipping. A failed validation
  raises `BankValidationError` and nothing is sent.
- `BankBurner.burn_and_verify` only accepts the exact bytes validated in the
  current operation (size + SHA-256 recheck).
- The ESP32 `/soundbank` endpoint independently requires exact size, the KT
  directory marker, and a matching `X-Bank-SHA256` over the staged bytes
  before starting the USB transaction.
- After every write: GetVersion must show `WTD41611DD` +
  `Software,2,4,15667`. Playback replies verify each live slot without the
  audible 0–20 sweep (the explicit BMO restore still runs the full sweep).
- One speech job at a time; robot access serialized through `rlock`;
  `/sound-bank-install` is blocked while speech runs.
- Speech is never silently truncated: unfittable text raises with a clear
  message (`max_chunks` default 12 ≈ 3.5 minutes of speech).

## Slot sequence

Chunks fill slots 0, 1, 2, 6, 7, 8, 9, 10, 3, 19 (long slots first),
≈17.296 s per bank. Segments are zero-padded to each slot's original
declared PCM length; unused slots are silenced (recorded in the manifest).

## Artifacts (never modified)

- BMO bank (restore target): `assets/bmo-sound-bank-offline-20260810/`
  `DfltSoundLib.BMO.pcm-only.Rev1.0.bin`, SHA-256 `9d3d…1159`
- Original emergency bank: `assets/neato-xv12-sound-capture-20260810/`
  `public-reference/DfltSoundLib.Rev1.0.bin`, SHA-256 `d396…b64a`
  (Sounds-tab installer, manual fallback only)

Restores re-read the artifact from disk and refuse on any size/hash
mismatch.

## Logs

Every synthesis, chunk plan, burn, playback and restore step — hashes,
timings, verification results — appends to `logs/tts-bank-operations.jsonl`.
