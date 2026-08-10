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
| Exact installed-bank backup | USB `readflash`, XMODEM readflash, and dump produced no sound image. | **Fail** |
| Edited-module format/integrity rules | Header slot-presence map is inferred/partly live-verified; required metadata/checksums and safe length edits are not proven. | **Fail** |
| Recovery path for a failed sound write | No byte-exact restore image or confirmed boot/debug recovery has been established. | **Fail** |
| Small reversible write validation | Not possible until the preceding gates pass. | Blocked |

Evidence links:

- [no-burn probe](/Volumes/2TB/neato-firmware-archive/work/logs/sound-upload-noburn-20260810.md)
- [live slot sweep](/Volumes/2TB/neato-firmware-archive/work/logs/live-playsound-sweep-20260810.md)
- [USB readback probe](/Volumes/2TB/neato-firmware-archive/work/logs/sound-readback-probe-20260810.md)

Do not replace failed gates with a public default module: matching slot presence
does not prove matching content, configuration, or recoverability.
