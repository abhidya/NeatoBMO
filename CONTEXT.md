# CONTEXT — NeatoBMO domain language

The shared vocabulary for this codebase. Use these terms exactly — in
code, commits, docs, and reviews. If a concept needs a new name, add it
here in the same change.

## The creature

- **BMO** — the persona: a sweet childlike robot buddy in a Neato XV-12
  vacuum body. Speaks in tiny simple words, says "friend" never "human".
- **Body** — the Neato XV-12 plus the ESP32-S3 riding it. Owns real-time
  robot control; never blocks on the brain.
- **Console** — the web UI (`static/console.html`, served by `bmo_web.py`),
  styled as BMO itself. Currently also BMO's face between LCD cascades.
- **Brain** — the local OLMoE LLM behind an OpenAI-compatible API
  (`bmo_brain_server.py`, "Colibri"). Slow (tens of seconds); everything
  around it is built to hide that.

## Conversation

- **Routine layer** (`neatobmo/routines.py`) — Siri-style pattern-matched
  instant answers (~2 ms) tried *before* the brain. Routine turns are
  `remember()`-ed into brain history so its memory stays coherent.
- **Stage cue** — the persona's "tool call": a bracketed token in the
  reply (`[happy] [wiggle] [sound:videogames]`). OLMoE can't emit real
  tool calls; `neatobmo/cues.py` parses cues best-effort instead. Cues
  resolve only to a fixed vocabulary — the brain never gets raw motor
  access.
- **Plan** — `cues.parse()`'s three views of one reply: `speech` (clean
  words for TTS), `display` (face cues as emoji, feeds the cascade),
  `steps` (ordered sound/move actions).
- **Soundbyte mode** — the default speech policy: spoken words are capped
  at a ~1.5 s burst and the soundboard/moves/faces carry the reply.
- **BurstBudget** (`cues.BurstBudget`) — the one owner of that cap, used
  by both the blocking (`condense()`) and streaming reply paths, including
  the always-leave-with-a-soundbyte guarantee.

## Voice

- **Voice ladder** (`neatobmo/voice.py`, `VoiceSynth`) — neural BMO clone
  (Piper prosody → RVC timbre, `tools/bmo_voice_server.py` on :8486) →
  Colibri espeak endpoint → local espeak-ng. Cache keys on the *requested*
  voice so fallbacks still hit.
- **Sound bank** — the robot's 770048-byte sound-flash image. Speech ships
  by packing TTS PCM into ~17 s **banks** and burning them
  (`neatobmo/tts_bank.py`); every burn is validated and verified.
- **Bank profile** (`tts_bank.BANK_PROFILES`) — one of the two approved
  flashable images (BMO / original), each gated by an exact SHA-256 and a
  typed confirmation phrase.
- **BankBurner** — the injected burn/verify/play/restore engine. The only
  thing allowed to write sound flash; callers hold the body lock.
- **Thinking sounds** — three reserved slots in every generated speech
  bank (blips + a BMO hum) vocalized by a background loop while the
  brain/synth is slow.
- **Chirp-speak** — the fallback when a reply has no cue sounds and robot
  speech is off: play a mood-guessed soundboard clip (like R2-D2).

## Face

- **Face vocabulary** (`neatobmo/faces.py`) — THE single source of truth:
  grid geometry, emoji map, `parse_emojis`, preview rects. The firmware's
  tables (`esp32-body/src/faces_table.h`) are GENERATED from it by
  `tools/gen_faces_table.py`; `tests/test_emote.py` enforces sync.
- **Grid face** — what the LCD can actually draw: full-height eye pillars
  × full-width mouth bands, full redraw each time. (SetLCD has no segment
  grammar; FGWhite is a no-op; a trailing number on HLine/VLine is parsed
  as Contrast and written to NAND.)
- **Cascade** — playing a reply's emoji sequence as faces with eyelid
  flashes between them; newest cascade wins. Player: `neatobmo/emote.py`
  (USB) and `esp32-body/src/faces.c` (on-device), same tables.

## Seams

- **Transport** (`neatobmo/transport.py`) — how command bytes reach the
  Neato: `SerialTransport` (direct USB) or `BridgeTransport` (ESP32 raw
  TCP :3333). Two adapters, one interface: `send` / `send_binary`.
- **Esp32Client** (`neatobmo/esp32.py`) — the one owner of the ESP32's
  HTTP endpoints (`/speak`, `/emote`, `/ota`, `/soundbank`), injected
  wherever the device is addressed over HTTP.
- **BodyController** (`neatobmo/body.py`) — the one owner of robot access
  policy: the lock, worker threads, the never-crash-the-chat swallow rule,
  and the ESP32-first/USB-fallback emote path.
- **SpeechService** (`neatobmo/speech.py`) — the speech-job state machine:
  synth pipeline → chunk packing → burn+speak, restore, profile installs.
- **Config** (`neatobmo/config.py`) — every `NEATOBMO_*` knob, read once
  in the composition root.

## Firmware (esp32-body)

- **NeatoLink** (`src/neato_usb.c`) — the firmware's Neato USB transport:
  tx mutex, ENQ/ACK binary protocol, streaming checksum, rx fan-out.
  Binary streams use a **transaction handle** (`neato_txn_t`) so only the
  task that opened a stream can write or close it. The host binds only USB
  VID:PID `2108:780B`; serial paths are ephemeral. After an updater reboot it
  must rediscover and re-identify the robot with `GetVersion`, and an absent
  USB device requires a physical or controllable-hub VBUS cycle rather than
  merely reopening the old path. Command capabilities are version-sensitive:
  2.5 and 2.7 matched in the captured help surface, while 3.1 omitted several
  diagnostic/calibration commands and the Upload `dump`/`xmodem` verbs. Live
  grammar is authoritative over help: on 3.1, tested `region + dump + Size`
  forms entered the host upload receiver even though `dump` was unadvertised.
  ENQ means receiver entry, never readback or persistent-write success.
- **neato_protocol** (`src/neato_protocol.c`) — the command vocabulary
  above the transport (LCD ops, PlaySound, TestMode, LEDs). Encodes the
  hardware footguns so callers speak intent, not strings.
- **coli_mcu** (`components/coli_mcu/`) — on-MCU LLM groundwork: USB-MSC
  model storage, the **BMOQ** quantized-model format, streamed Q4 kernels,
  OLMoE decode layers. Host-testable by design (`tools/Makefile`).

## Architecture words

From the review discipline: **module** (interface + implementation),
**seam** (where an interface lives), **adapter** (a concrete thing at a
seam), **deep** (much behaviour, small interface), **shallow** (interface
≈ implementation). The interface is the test surface.
