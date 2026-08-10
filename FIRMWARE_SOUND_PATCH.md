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
route without writing flash. It also does not prove that the robot decrypted
the image in any recoverable location.

The public `.enc` application files remain opaque updater inputs. Current
evidence shows a 512-byte `neato` format-2 envelope, high-entropy aligned
payloads, and a Windows updater that transfers the already-encrypted file. No
checked artifact proves a decryptor, signer, repacker, plaintext image, or
host-side unlock path. See
[FIRMWARE_ARCHIVE.md](FIRMWARE_ARCHIVE.md#current-unlockrecovery-status) and
[/Volumes/2TB/neato-firmware-archive/work/logs/research-profile.md](/Volumes/2TB/neato-firmware-archive/work/logs/research-profile.md).

The patch therefore needs a plaintext application produced by one of these
paths: a verified decrypt/repack implementation, an authenticated hardware
debug read, or a known-good unencrypted developer image. `tools/neato_firmware.py
validate-unlock` requires size coverage, multiple Neato command markers,
substantial strings, lower entropy than ciphertext, and—when available—an
exact repack hash before any candidate is trusted.

The active next step is not patching. It is read-only recovery preparation:
verify the actual P-board markings, passively capture P6 DBGU at 115200 8N1,
and only if the AT91 ROM monitor is reached, use official SAM-BA read commands
to capture duplicate matching raw images under
[/Volumes/2TB/neato-firmware-archive/work/inputs/](/Volumes/2TB/neato-firmware-archive/work/inputs/).
P10 JTAG is not the primary route, J3 erase is out of scope, and any erase,
program, unlock, or write prompt is a stop condition.

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

## Stock sound-bank alternative: current state

`Upload sound` is a module-level updater command, not a documented
per-`PlaySound` slot command. The public default module is an unencrypted,
770,048-byte, 512-byte-page image. It can be transferred without decrypting
the application, but a successful custom upload still requires knowing the
module's integrity fields and the mapping from its records to sound IDs.

`tools/neato_sound_bank.py` is intentionally analysis-only: it validates the page
layout, makes byte-identical staging copies, extracts the inferred raw PCM,
and exports conservative candidate clips. Its ten candidate boundaries are
explicitly not sound IDs. The generated evidence is retained on the 2TB work
volume:

- [candidate boundaries](/Volumes/2TB/neato-firmware-archive/work/logs/default-sound-bank-candidates.json)
- [candidate WAV manifest](/Volumes/2TB/neato-firmware-archive/work/logs/default-sound-bank-candidate-wavs.json)

The next safe hardware experiment is a fresh protocol capture of a
byte-identical public-module `Upload sound noburn` attempt, followed by a
targeted `PlaySound 0..20` sweep. It can establish whether `noburn` accepts a
sound module and whether the updater reports format errors, without erasing
the robot's current library. No write/upload is authorized by this tooling.

That `noburn` probe is now complete: the XV-12 emitted ENQ, received the
byte-identical module, then returned an empty command terminator rather than
ACK or NAK. That is the same completion pattern captured from an earlier
`Upload code noburn` test, so it is consistent with no-burn completion rather
than a rejection. `GetVersion` immediately succeeded afterward. The full record is
[on the 2TB work volume](/Volumes/2TB/neato-firmware-archive/work/logs/sound-upload-noburn-20260810.md).
It proves receiver entry and no-burn completion only; it does not prove that
the public module is accepted by this 2.4 firmware or matches the installed
sound bank.
An intentionally one-bit-corrupted module completes identically under
`noburn`, confirming that it is not an integrity/compatibility validator.

A direct USB `PlaySound 0..20` sweep now validates the public header's
slot-presence map on the live XV-12: only `0–3`, `6–10`, and `19` are accepted;
the other eleven return `out of range`. This confirms ten usable stock slots,
not 21. The result is captured in
[live-playsound-sweep-20260810.md](/Volumes/2TB/neato-firmware-archive/work/logs/live-playsound-sweep-20260810.md).
It also makes the unpatched speech-bank ceiling clear: arbitrary speech cannot
be assembled faithfully from ten fixed clips, although they are enough for a
small set of expressive cues or pre-recorded phrases.
The host runtime now filters its sound vocabulary to these live-verified IDs,
so personality behaviors do not send documented-but-invalid commands to this
specific XV-12.

Audible microphone captures and a conservative cue classification are preserved
under [assets/neato-xv12-sound-capture-20260810](assets/neato-xv12-sound-capture-20260810/).
They are a listening reference, not a sound-flash backup.

The direct USB readback gate is now tested and closed on firmware `2.4.15667`:
`Upload sound readflash` returns only its echo/terminator; its XMODEM form
never starts a transfer; and `Upload sound dump` yields no sound bytes. See
[sound-readback-probe-20260810.md](/Volumes/2TB/neato-firmware-archive/work/logs/sound-readback-probe-20260810.md).
That leaves hardware-level acquisition as the only path to a byte-exact
installed-bank rollback image before a bank-writing experiment.

The current approval boundary is maintained in
[SOUND_BANK_WRITE_GATES.md](SOUND_BANK_WRITE_GATES.md).
