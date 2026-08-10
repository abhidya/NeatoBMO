# Implementation prompt: hybrid BMO sound-bank and TTS path

Use this prompt for a focused implementation task:

> Build a production-safe hybrid voice path for this NeatoBMO repository.
>
> The installed XV-12 sound bank is a fixed phrase palette, not a general text
> synthesizer. Use `neatobmo.sounds.BMO_SOUND_SLOTS` and `BMO_SEQUENCES` for
> known reactions and phrases. A new `PlaySound` command interrupts the current
> clip, so multi-slot phrases must send one command at a time and wait the
> current slot's `slot_seconds` before sending the next ID. Never use combined-ID
> syntax and never assume the firmware queues commands.
>
> For arbitrary text, use the existing Colibri `/v1/audio/speech` path to
> obtain a WAV. Validate it before playback. Prefer the ESP32 `/speak` relay or
> the separately gated `PlaySound File` transport when available; fall back to
> browser/local speech when robot streaming is unavailable. Do not rewrite the
> flash sound bank for individual utterances.
>
> Implement a `VoiceRouter` with three explicit outcomes:
>
> 1. `bank_slot`: exact short intent maps to one installed BMO slot;
> 2. `bank_sequence`: an approved fixed phrase maps to duration-paced slot IDs;
> 3. `streamed_tts`: arbitrary text becomes a validated WAV and uses the live
>    speech transport.
>
> Return structured metadata for every decision: route, requested text/intent,
> slot IDs, per-slot delays, source labels, WAV format/duration when applicable,
> selected transport, playback result, and fallback/error reason.
>
> Add web API endpoints and update the Sounds page to display the selected
> route and playback state. Serialize all robot access through the existing
> lock. Reject unknown sound IDs, malformed WAVs, oversized audio, and any
> request attempting to invoke `Upload sound` as TTS.
>
> Add unit tests for intent routing, duration-paced sequences, interruption
> prevention, WAV validation, unavailable ESP32/robot fallbacks, locking, and
> response metadata. Keep all existing bank install/restore hash allowlists and
> confirmation gates unchanged. Do not burn or modify the robot during tests.

The key boundary is intentional: use the flash bank for a small expressive
vocabulary and use streamed WAV playback for open-ended speech.

## If implementing temporary TTS-generated banks instead

Treat the validated BMO image as the persistent baseline:

- file: `assets/bmo-sound-bank-offline-20260810/DfltSoundLib.BMO.pcm-only.Rev1.0.bin`
- SHA-256: `9d3d82d9275c03fa9f2abb163cdfd9393445737999916f6337d2d6b639b51159`

A generated TTS bank is temporary. Preserve the BMO artifact unchanged, record
the temporary bank hash, burn it only after explicit confirmation, play its
duration-paced slots, and then offer or perform a separately verified BMO
restore. A BMO restore must recheck the exact hash above, require `ACK + 0x1a`,
verify `GetVersion`, and recover the live slot map
`0,1,2,3,6,7,8,9,10,19`.

Never overwrite the saved BMO artifact with generated speech. The original
Neato image remains a separate manual/emergency fallback, not the normal
post-TTS restore target.
