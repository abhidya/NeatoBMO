# Neato sound-bank write gates

This checklist governs any command that could write the XV-12 sound flash.
It applies to `Upload sound` **without** `noburn` and to a patched application
that might rewrite the same region.

| Gate | Current evidence | Status |
|---|---|---|
| Target identity recorded | XV-12 `WTD41611DD-0037829-P`, software `2.4.15667`, mainboard `7.1`; read-only snapshot on 2TB volume. | Pass |
| Live slot map known | `PlaySound 0..20` accepts `0–3`, `6–10`, `19`; exactly matches the public bank's non-empty header entries. | Pass |
| Upload receiver behavior characterized | `Upload sound noburn` reaches ENQ and terminates like `Upload code noburn`. | Pass |
| No-burn validates an edited bank | One-bit-corrupted module completes identically under `noburn`. | **Fail: it is transport-only** |
| Exact installed-bank backup | USB readback remains unavailable; the archived vendor default is not proof of the former installed bytes. | Gap remains |
| Edited-module format/integrity rules | Live experiments proved a narrow safe envelope: preserve directory, page table, starts, headers, declared lengths, and non-PCM bytes; replace PCM spans only. | **Pass for PCM-only builds** |
| Recovery path for a failed sound write | Exact vendor default SHA `d3969779…b64a` restored all ten slots after a failed custom write. | **Pass** |
| Custom BMO write validation | PCM-only BMO SHA `9d3d82d9…1159` returned ACK, retained firmware identity, and activated all ten expected IDs. | **Pass** |

Evidence links:

- [no-burn probe](/Volumes/2TB/neato-firmware-archive/work/logs/sound-upload-noburn-20260810.md)
- [live slot sweep](/Volumes/2TB/neato-firmware-archive/work/logs/live-playsound-sweep-20260810.md)
- [USB readback probe](/Volumes/2TB/neato-firmware-archive/work/logs/sound-readback-probe-20260810.md)

Approved write paths today:

- **Profile install/restore** (web "Sounds" tab, CLI): only the two exact
  hashes documented in `docs/SOUND_BANK_UPDATE.md` (BMO bank / vendor
  original).
- **Generated TTS banks** (`neatobmo/tts_bank.py`): each spoken utterance
  builds a bank that preserves the validated directory/header/page structure
  and replaces PCM spans only; `BankBurner.burn_and_verify` accepts exactly
  the bytes whose SHA-256 was computed and validated in that same operation,
  then verifies the robot end-to-end after the burn. The web UI tracks a
  per-session flash-write (wear) counter.

Arbitrary uploads and metadata-changing custom banks remain blocked.
