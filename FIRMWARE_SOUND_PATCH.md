# Native `PlaySound File` patch contract

## Distilled CFW execution path

The first deliverable is not the full audio handler. It is a **version-only
custom firmware proof** built from the exact application running on this
XV-12. That smaller patch proves acquisition, modification, execution, health
checks, and rollback before functional code is changed.

Upload acceptance and executable-image validity are separate gates. The
updater passes the caller-provided bytes unchanged, and `Upload code noburn`
does not reject arbitrary content. This confirms that the transport is not a
signature validator; it does **not** prove whether a later boot stage decrypts,
transforms, or directly executes the payload. Blindly modifying a public
`.enc` file therefore remains unsafe even though the receiver accepts it.

The preferred path avoids that ambiguity:

1. Identify the exact flash device, geometry, debug connections, and boot path
   on this P-family board. Begin with passive P6 DBGU capture; do not use J3.
2. Establish a non-destructive raw read path and take two byte-identical full
   captures, including any NAND OOB/ECC data required for restoration.
3. Extract and validate the installed `2.4.15667` application. Require command
   strings, a coherent ARM9 memory map, load/entry addresses, and reproducible
   region hashes.
4. Prove the execution path with an unchanged extracted image before patching.
   Prefer a RAM/debug boot. If only flash execution is possible, first prove a
   hardware restore on a spare/duplicate flash device.
5. Build a deterministic same-size version patch. Change only the smallest
   constant or fixed-width string that drives `GetVersion`; emit a manifest of
   base hash, output hash, offsets, old/new bytes, and all integrity updates.
6. Run the patched image through the same reversible path and require the CFW
   version plus a complete health regression. Only then authorize a persistent
   application write.
7. Add `PlaySound File` as the second patch, using the validated version-only
   CFW as the known-good baseline.

The detailed implementation plan, acceptance gates, artifacts, and fallback
routes live in
[`.omx/plans/neato-cfw-version-path.md`](.omx/plans/neato-cfw-version-path.md).

### Offline version-patch tooling

`tools/neato_cfw.py` now implements the file-only part of the version-proof
workflow. It cannot contact or write the robot.

Inspect a future raw capture and locate candidate version representations:

```sh
python3 tools/neato_cfw.py inspect-raw raw-application.bin \
  --find 15667 --output raw-application.inspect.json
```

After disassembly proves the correct representation and offset, build a
same-size patch pinned to the raw capture's SHA-256:

```sh
python3 tools/neato_cfw.py patch-version \
  raw-application.bin cfw-version-proof.bin \
  --base-sha256 <64-hex-capture-sha256> \
  --old 15667 --new 95667 --encoding ascii \
  --offset <reviewed-offset>
```

Omit `--offset` only when the old encoded value occurs exactly once. Use
`--encoding u16le` or `--encoding u32le` if disassembly proves the version is a
little-endian integer constant rather than ASCII. The tool refuses a base hash
mismatch, missing or ambiguous old value, size-changing replacement, existing
output file, or out-of-range offset. It writes a deterministic JSON manifest.

Independently re-check the base, patched file, and manifest before any hardware
tool is allowed to consume the result:

```sh
python3 tools/neato_cfw.py verify-patch \
  raw-application.bin cfw-version-proof.bin \
  cfw-version-proof.bin.manifest.json
```

`verify-patch` requires the manifest hashes and sizes, expected old/new bytes,
and an exact reconstruction proving that no bytes outside the approved
replacement changed. It deliberately does not guess or update unknown boot
checksums; those must be identified from the acquired image first.

The goal is runtime speech through the XV-12's original speaker without
rewriting its flash sound bank for every utterance. (The shipped interim
path does exactly that rewrite: `neatobmo/tts_bank.py` packs speech into
validated ~17 s banks and burns them chunk by chunk, with persistent mode
avoiding only the restore write. This patch remains the way to retire that
flash wear entirely.)

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
`Robot.play_file()` (currently unused by the web app, which speaks through
the TTS sound-bank path instead). Speech WAVs come from the neural BMO voice
server by default (`tools/bmo_voice_server.py`), with Colibri
`/v1/audio/speech` and espeak-ng as fallbacks; once this patch lands,
`bmo_web.py` can relay those WAVs to `PlaySound File` via the ESP32 `/speak`
endpoint with no host protocol change.

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

The patch therefore needs an executable application produced by one of these
paths: an authenticated raw hardware read, a verified decrypt/repack
implementation, or a known-good unencrypted developer image. A raw
read/debug-write path is preferred because it can make the updater's `.enc`
container irrelevant. `tools/neato_firmware.py
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
- The ESP32 also serves `POST /soundbank` (`neato_audio.c`): a SHA-256-gated
  sound-bank install with a streaming HTTP→USB relay path for boards without
  PSRAM — this is the transport the shipped TTS-bank speech uses today.
- On stock firmware `/speak` returns a clear HTTP conflict instead of
  pretending playback succeeded. Once the handler patch is installed, no host
  or ESP32 protocol change should be needed.

## Stock sound-bank alternative: current state

`Upload sound` is a module-level updater command, not a documented
per-`PlaySound` slot command. The public default module is an unencrypted,
770,048-byte, 512-byte-page image. It can be transferred without decrypting
the application. The module's integrity constraints and record→sound-ID
mapping have since been proven live (see `docs/SOUND_BANK_UPDATE.md`,
"Proven customization constraint"): preserve the directory, page table,
record headers, declared lengths, and all non-PCM bytes; replace PCM spans
only. `neatobmo/tts_bank.py` implements that envelope and burns custom banks
in production today.

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
`Upload code noburn` test. A later simultaneous P6 capture revealed the hidden
terminal status: the updater reported `Options : NoWrite`, completed receipt of
all 770052 framed bytes, then printed `Upload fail - nandflashWrite() fail -
-1`. Therefore the USB terminator is **not** evidence of successful validation
or acceptance; it only marks command completion. `GetVersion` immediately
succeeded afterward. The original USB record is
[on the 2TB work volume](/Volumes/2TB/neato-firmware-archive/work/logs/sound-upload-noburn-20260810.md).
The P6 record is `captures/20260811_B02_sound_noburn_exact_p6.log`.
It proves receiver entry and no-burn completion only; it does not prove that
the public module is accepted by this 2.4 firmware or matches the installed
sound bank.
An intentionally one-bit-corrupted module completes identically under
`noburn`, confirming that it is not an integrity/compatibility validator. The
new P6 failure status explains why identical USB termination cannot distinguish
valid from corrupted content.

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
That left hardware-level acquisition as the only path to a byte-exact image
of the *originally installed* bank. Bank-writing has since proceeded without
it: the archived vendor default (SHA `d3969779…b64a`) proved out as the
rollback image, restoring all ten slots after a failed custom write, and the
validated BMO bank plus generated TTS banks now install routinely under the
write gates.

The current approval boundary is maintained in
[SOUND_BANK_WRITE_GATES.md](SOUND_BANK_WRITE_GATES.md).
