# Offline BMO sound-bank replacement package — 2026-08-10

This package builds a BMO-filled Neato XV-12 sound-bank image offline. It does
not contact, command, or write the robot.

## Final artifact

- Bank image: `DfltSoundLib.BMO.Rev1.0.bin`
- Size: `770048` bytes
- SHA-256: `c17d42ec605efde8affd3d184ce41a2fd08aae80795633c4d6fe6b3e6750900f`
- Offline transport additive checksum: `0x047d66ad`
- Validation: `validation-report.json` reports `passed: true`

## Approved source set

Only these seven user-approved Myinstants URLs were used:

- `https://www.myinstants.com/en/instant/bmo-video-games-96304/`
- `https://www.myinstants.com/en/instant/its-bmo-time-1369/`
- `https://www.myinstants.com/en/instant/bmo-hello-72979/`
- `https://www.myinstants.com/en/instant/bmobutt-65338/`
- `https://www.myinstants.com/en/instant/json-bmon-68510/`
- `https://www.myinstants.com/en/instant/bmo-homeboys-64616/`
- `https://www.myinstants.com/en/instant/yeah-bmo-47447/`

Non-BMO search-page results are excluded. The final package uses only the seven
explicit instant URLs listed above; no search-page-only audio is used in the
source inventory, previews, mapping, sequences, or bank.

`source-inventory.json` records source URLs, original MP3 hashes, and the slot
uses. Licensing/provenance remains Myinstants/source dependent; this is a local
user-approved build artifact, not a redistributable sound pack.

## Slot mapping

All ten live/restored IDs are filled with BMO sounds. Diagnostic tones are not
preserved per the updated user requirement.

| ID | Clip role | Duration | Capacity | Source |
|---:|---|---:|---:|---|
| 0 | video games segment 1 | 2.740136 s | 2.881995 s | BMO Video Games |
| 1 | video games segment 2 | 1.799819 s | 1.867392 s | BMO Video Games |
| 2 | video games segment 3 | 0.916735 s | 1.869388 s | BMO Video Games |
| 3 | yeah reaction excerpt | 0.300000 s | 0.321451 s | Yeah BMO |
| 6 | its bmo time | 1.930023 s | 2.008662 s | its bmo time |
| 7 | well hello there | 1.700000 s | 2.017324 s | BMO_Hello |
| 8 | bmobutt approved excerpt | 1.950023 s | 2.008662 s | BMObutt |
| 9 | json bmon reaction | 1.320000 s | 2.008662 s | Json Bmon |
| 10 | bmo homeboys excerpt | 1.980000 s | 1.991338 s | BMO Homeboys |
| 19 | short reaction excerpt | 0.300000 s | 0.321451 s | Json Bmon |

The long BMO Video Games source is split across slots 0, 1, and 2 using
zero-crossing boundary search and tiny fades. IDs 3 and 19 use short faded
reaction excerpts under the 0.321-second fixed capacity.

## Burst sequences

`sequence-manifest.json` defines host-driven burst sequences. The command model
is separate single-ID commands only, sent immediately by default:

- `bmo_video_games_burst`: `PlaySound 0`, `PlaySound 1`, `PlaySound 2`
- `bmo_all_slots_demo_burst`: `PlaySound 0`, `1`, `2`, `6`, `7`, `8`, `9`, `10`, `3`, `19`
- `bmo_short_reactions_burst`: `PlaySound 3`, `PlaySound 19`

Stitched zero-gap reference WAVs are in `stitched-sequence-previews/`.

Native robot events still trigger one sound slot at a time. Multi-slot lines
require a host helper issuing rapid consecutive `PlaySound <id>` commands.
Do not use combined-ID syntax such as `PlaySound 1-2-3`; current protocol
evidence supports single-ID commands only.

Dry-run helper example:

```sh
python3 tools/neato_sound_sequence.py \
  assets/bmo-sound-bank-offline-20260810/sequence-manifest.json \
  bmo_video_games_burst
```

That prints the exact separate commands. Actual robot contact requires the
explicit `--execute` flag and is intentionally not performed here.

## Build and validation evidence

Builder:

```sh
python3 tools/neato_sound_bank.py build-from-wavs \
  assets/neato-xv12-sound-capture-20260810/public-reference/DfltSoundLib.Rev1.0.bin \
  assets/bmo-sound-bank-offline-20260810/bmo-replacements.approved.json \
  assets/bmo-sound-bank-offline-20260810/DfltSoundLib.BMO.Rev1.0.bin \
  --output assets/bmo-sound-bank-offline-20260810/build-report.json
```

Validation performed:

- exact output size is `770048` bytes;
- 512-byte page alignment retained;
- first eight `KT` directory pages unchanged;
- record start pages unchanged;
- all ten built PCM hashes match the normalized preview WAV PCM hashes;
- all slots fit fixed capacities;
- no generated preview reaches full-scale clipping;
- parsed round-trip exported `built-preview-wavs/` from the final image.

## Post-burn acceptance plan

This is a plan only. The leader owns any eventual burn and live verification.

After a future leader-controlled burn:

1. Wait for the expected ACK/terminator and stable normal application state.
2. Verify `GetVersion` responds normally.
3. Sweep every live slot individually and record replies plus expected content:
   `0` video games segment 1, `1` segment 2, `2` segment 3, `3` yeah reaction,
   `6` its bmo time, `7` well hello there, `8` bmobutt excerpt, `9` json bmon,
   `10` homeboys excerpt, `19` short reaction.
4. Execute every defined rapid burst sequence from `sequence-manifest.json`.
5. Record command order, replies, audible continuity/gaps, and expected
   clip/segment names.
6. Stop on any missing slot, out-of-range reply, interrupted playback,
   unexpected gap/queue behavior, distortion, or clipped audible output.

Live verification must confirm that this device still queues or overlaps rapid
single-ID `PlaySound` commands acceptably after the new bank is installed.
