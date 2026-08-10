# Native `PlaySound File` patch contract

The goal is runtime speech through the XV-12's original speaker without
rewriting its flash sound bank for every utterance.

## Existing pieces confirmed on the robot

- Stock firmware is `2.4.15667`, mainboard `7.1`.
- `PlaySound <0..20>` reads a page-indexed PCM library from the sound region.
- The stock library is mono signed 16-bit PCM at 22,050 Hz.
- Neato's uploader already implements a binary serial transaction: command,
  ENQ (`0x05`), payload, little-endian additive checksum, ACK (`0x06`).
- The official `DfltSoundLib.Rev1.0.bin` is not encrypted. The application
  images are encrypted in a consistent 512-byte `neato` envelope.
- The compatible public Cruz archive contains P-hardware application images
  for 2.5, 2.7, 3.1, and 3.2. The robot's installed 2.4.15667 image has not
  been found publicly and the stock console does not provide application
  readback, so the recovery snapshot is configuration/calibration data rather
  than a claim of a byte-for-byte firmware backup.

## New command

The patched application adds this form without changing stock sound IDs:

```text
PlaySound File Size <wav_length + 4>\r
```

Wire sequence:

1. Robot validates the declared size (maximum 512 KiB) and reserves a bounded
   RAM buffer.
2. Robot replies with ENQ (`0x05`).
3. Host sends a PCM WAV followed by `uint32_le(sum(wav_bytes))`.
4. Robot validates RIFF/WAVE, mono, signed 16-bit PCM, and 22,050 Hz.
5. Robot replies with ACK (`0x06`) and plays from RAM through the existing
   sound/DAC routine. NAK (`0x15`) rejects malformed or oversized input.
6. The buffer is released after playback. No sound flash is erased or written.

The host implementation lives in `SerialTransport.send_binary()` and
`Robot.play_file()`. Colibri exposes `/v1/audio/speech`; `bmo_web.py` sends that
WAV directly to `PlaySound File`.

## Firmware extraction/patch boundary

The normal 2.4 application console exposes `readflash` in help but does not
return application bytes. A validated 3.1 image sent with `Upload code noburn`
was accepted into the updater's receive path, but subsequent `dump` commands
also returned no payload. That rules out the simple USB decrypt-and-readback
route without writing flash.

The patch therefore needs a plaintext application produced by one of these
paths: a verified decrypt/repack implementation, an authenticated hardware
debug read, or a known-good unencrypted developer image. `neato_firmware.py
validate-unlock` requires size coverage, multiple Neato command markers,
substantial strings, lower entropy than ciphertext, and—when available—an
exact repack hash before any candidate is trusted.

After plaintext is acquired:

1. Locate the `PlaySound - Play the specified sound in the robot.` string and
   its command-table reference.
2. Trace `PlaySound <id>` to the library lookup and low-level PCM/DAC routine.
3. Add the `File` branch and reuse the updater receiver/checksum routine.
4. Call the existing PCM routine with the validated WAV data chunk.
5. Test from RAM/debug boot first, then produce a restorable flash image only
   after the application region has been backed up and hashed through a proven
   hardware readback path.

## Host and ESP32 state

- `Robot.play_file()` and `SerialTransport.send_binary()` implement the exact
  updater framing for direct-USB development.
- Colibri's `/v1/audio/speech` endpoint emits normalized mono, 16-bit,
  22,050-Hz WAV.
- The ESP32 `POST /speak` endpoint stages at most 512 KiB in PSRAM, validates
  the WAV, serializes USB access, then sends `PlaySound File` plus checksum.
- On stock firmware the endpoint returns a clear HTTP conflict instead of
  pretending playback succeeded. Once the handler patch is installed, no host
  or ESP32 protocol change should be needed.
