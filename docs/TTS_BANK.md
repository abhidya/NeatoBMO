# BMO speaks: automatic TTS through the sound flash

Type text → BMO says it. Long text is packed into ≈17 s banks at sentence
boundaries and burned+spoken chunk by chunk. The speech bank **persists**
(no restore write per utterance); "Bring back BMO sounds" reinstalls the
BMO bank on demand. Chat replies speak the same way when the voice selector
is set to 🤖 robot — stage cues (`neatobmo/cues.py`) are stripped first, so
only the cue-free speech text reaches the bank. In the default **soundboard**
mode (`NEATOBMO_SPEECH`) an exact authentic catalog clip is relayed through
the ESP32 `/speak` path with **no flash write**; only when no clip fits is
speech synthesized and packed into a bank, and `soundbyte` mode condenses LLM
replies to a ~1.5 s spoken burst (`cues.condense`); hand-authored routine
replies bypass the cap.

> Each chunk is one 770,048-byte flash write, except that re-speaking a
> bank whose hash is already installed skips the write. There is no
> per-utterance confirmation — the gates are internal and automatic
> (below). The web UI surfaces a per-session flash-write (wear) counter
> via `/tts-bank/status`.

## Pipeline

```
text → sentence units, each synthesized solo in a producer thread that
       runs concurrently with burn/play (first chunk flushes after one
       sentence so speech starts fast; pack_audio_chunks)
     → WAV from the neural BMO voice (Piper prosody → RVC timbre,
       tools/bmo_voice_server.py at :8486, auto-started); fallbacks:
       Colibri /v1/audio/speech, then local espeak-ng en+f4 -s160 -p70
     → 22050 Hz mono s16le, silence-trimmed, peak-normalized to ≈-8 dBFS
       (~40% FS — -1 dBFS overdrove the robot speaker)
     → boundary-aware split across the ten live slots, tiny fades,
       sentence-gap silence between units
     → PCM-only overlay on the BMO baseline → byte-exact validation
     → Upload sound (USB direct, or relayed via ESP32 POST /soundbank);
       skipped when the same bank hash is already installed
     → silent GetVersion identity check (no audible sweep)
     → PlaySound per slot, content-paced (content length + 0.15 s margin,
       not the zero-padded declared slot length); each reply doubles as
       slot verification ("out of range" ⇒ abort)
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
  directory marker, and a matching `X-Bank-SHA256`. With PSRAM the bytes are
  staged and checked before the USB transaction starts; on no-PSRAM boards a
  streaming HTTP→USB path hashes in flight and, on a short body or SHA
  mismatch, poisons the trailing transport checksum so the robot NAKs the
  transfer.
- After every write: GetVersion must show `WTD41611DD` +
  `Software,2,5,15893`. Playback replies verify each live slot without the
  audible 0–20 sweep (the explicit BMO restore still runs the full sweep).
- One speech job at a time; robot access serialized through `rlock`;
  `/sound-bank-install` is blocked while speech runs.
- Approved soundboard pages are already complete Neato bank images. Send
  `{"sound_key":"<catalog key>","confirmation":"INSTALL PREGENERATED BMO SOUND BANK"}`
  to `/sound-bank-install` to install the catalog item's saved, SHA-verified
  module without synthesis, transcoding, or bank construction. This is an
  explicit offline/fallback operation; normal exact-clip speech uses runtime
  WAV playback and performs zero flash writes.
- `GET /voice/module?key=<catalog key>` returns the same approved pre-generated
  module for ESP32/offline caching. Rejected and unknown keys are never served.
- Runtime extraction keeps the four most recently used verified modules in
  memory, avoiding repeated disk reads and SHA work without retaining the full
  roughly 27 MB library in RAM.
- Speech is never silently truncated: unfittable text raises with a clear
  message (`max_chunks` default 24 ≈ 7 minutes of speech).

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
