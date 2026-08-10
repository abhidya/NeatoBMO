# Neato XV-12 captured sound palette

This directory preserves the microphone captures made while directly issuing
`PlaySound` to XV-12 `WTD41611DD-0037829-P` (firmware `2.4.15667`). It is an
audible reference and recovery aid; it is **not** a byte-exact backup of the
sound flash.

## Contents

- `raw/` — settled microphone recordings. These retain the evidence needed to
  revisit trim timing.
- `clips/` — reliable trimmed captures for the distinct events detected.
- `public-reference/DfltSoundLib.Rev1.0.bin` — the archived public default
  module, SHA-256
  `d3969779a6a195812d72b6859454de004ea45beefdee6f1b5c50a2632564b64a`.
  It has the same live slot-presence layout but is not proven to be the bank
  installed on this robot.

## Audibly characterized slots

| ID | Capture | Character | Suggested BMO use | Confidence |
|---:|---|---|---|---|
| 0 | `slot-00-notification.wav` | Three-note high/low/high electronic chime (~1747/877 Hz). | Prompt, acknowledgement, general notification. | High |
| 1 | `slot-01-chirp.wav` | Short bright chirp (~2091 Hz). | Button/UI confirmation. | Medium |
| 2 | `slot-02-warble.wav` | Loud sustained warble around 1 kHz. | Important state transition. | High |
| 3 | `slot-03-warble.wav` | Sustained ~1 kHz warble, related to ID 2. | Attention/status transition. | High |
| 6 | `slot-06-high-tone.wav` | Thin high sustained tone (~2993 Hz). | Secondary attention/searching cue. | High |
| 8 | `slot-08-status-tone.wav` | Sustained mid-high tone (~1365 Hz). | Operation-progress cue. | High |
| 9 | `slot-09-status-tone.wav` | Related, slightly harsher 1240–1370 Hz tone. | Related status/error cue. | High |

IDs `7`, `10`, and `19` were accepted by the robot but were not separable from
the microphone noise floor in this capture. They are retained in the raw
recordings rather than mislabeled as silent.

The actual device accepts only IDs `0–3`, `6–10`, and `19`; all other
documented IDs return `out of range` on this XV-12.

Do not use the public-reference module as a flash rollback image. The direct
USB protocol could not export the installed sound flash.
