# Updating or restoring the Neato XV-12 sound bank

This repository contains two approved **profile** sound-bank images for XV-12
`WTD41611DD-0037829-P`, firmware `2.4.15667`, mainboard `7.1` (the remote
soundboard additionally publishes 36 derived modules built off the same
validated BMO baseline — see `docs/BMO_REMOTE_SOUNDBOARD.md`):

| Profile | File | SHA-256 |
|---|---|---|
| BMO | `assets/bmo-sound-bank-offline-20260810/DfltSoundLib.BMO.pcm-only.Rev1.0.bin` | `9d3d82d9275c03fa9f2abb163cdfd9393445737999916f6337d2d6b639b51159` |
| Original | `assets/neato-xv12-sound-capture-20260810/public-reference/DfltSoundLib.Rev1.0.bin` | `d3969779a6a195812d72b6859454de004ea45beefdee6f1b5c50a2632564b64a` |

Both images have been burned successfully and verified with the expected live
slot map `0,1,2,3,6,7,8,9,10,19`.

The BMO image is the day-to-day baseline. TTS-generated speech banks are
**persistent by design**: after BMO speaks, the last speech bank stays in the
sound flash (`installed profile: tts`) until "🎵 Bring back BMO sounds" —
a one-click `POST /tts-bank/restore`, no typed phrase — reinstalls the BMO
bank (see `docs/TTS_BANK.md`). No generated bank ever replaces the saved
artifacts on disk. The original Neato profile is retained as an
emergency/manual fallback.

## Web portal

Run `python3 bmo_web.py` and open `http://localhost:8485` (port overridable
via `PORT`). The **Sounds** tab provides metadata and playback for every BMO
slot, burst sequences, downloads for both exact images, and guarded
installation profiles. The **TTS** tab is the normal bank-writing path:
automatic speech through `/tts-bank/speak|status|stop|restore`
(`docs/TTS_BANK.md`).

Multi-slot sequences are duration-paced. Sending commands immediately does not
queue them on this firmware; the next command interrupts current playback.

A write requires typing the selected profile's phrase exactly:

- `INSTALL BMO`
- `RESTORE ORIGINAL`

The server refuses arbitrary files. It verifies exact size and SHA-256 before
calling `Upload sound`, waits five seconds, checks `GetVersion`, and sweeps IDs
0–20. Success requires exactly `0,1,2,3,6,7,8,9,10,19`.

Keep the robot powered and USB-connected. Do not reload or close the portal
while the write is running.

## Command line

Install BMO:

```sh
python3 tools/neato_sound_burn_exact.py \
  assets/bmo-sound-bank-offline-20260810/DfltSoundLib.BMO.pcm-only.Rev1.0.bin \
  --execute-destructive-write
```

Restore the original bank:

```sh
python3 tools/neato_sound_burn_exact.py \
  assets/neato-xv12-sound-capture-20260810/public-reference/DfltSoundLib.Rev1.0.bin \
  --execute-destructive-write
```

## Proven customization constraint

Firmware 2.4.15667 activated only slot 0 for two otherwise valid custom
layouts. The successful BMO image preserves byte-for-byte:

- all eight directory pages and the slot table;
- original record start pages;
- every 16-byte record header and declared length;
- all bytes outside the original PCM fields.

Only PCM bytes are replaced. Shorter clips are padded with zero-valued PCM to
the original declared length. Future sound banks must follow this PCM-only rule
unless another live experiment proves a broader format.

Full evidence is in
`.omx/specs/autoresearch-neato-xv12-sound-bank/bmo-burn-experiment-20260810.md`.
