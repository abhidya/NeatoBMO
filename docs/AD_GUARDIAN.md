# BMO Ad Guardian

BMO can reuse the same always-on microphone stream used for wake-word detection to recognize known commercials and automatically trigger TV mute/unmute actions through Tuya/Smart Life.

## Runtime flow

1. The microphone pipeline continuously produces 16 kHz mono PCM.
2. Wake-word detection and ad fingerprinting consume the same PCM frames independently.
3. The fingerprint matcher reports `{ad_id, duration_ms, matched_offset_ms, confidence}`.
4. `ad_guard` computes the known remainder of the commercial and suppresses TV audio for that interval.
5. Back-to-back matches extend the suppression deadline instead of briefly unmuting between ads.
6. Suppression calls a Tuya Tap-to-Run scene. Restoration calls a second scene (or the same mute-toggle scene if the TV exposes only a toggle).

The state machine is intentionally independent of the fingerprint implementation so BMO can later use a local database, a community-updated database, or both.

## Tuya / Smart Life access

Do **not** store the Smart Life username/password in firmware.

Use a Tuya Cloud project instead:

1. Create a Tuya Cloud project in the data center matching the Smart Life account region.
2. Link the Smart Life account to the project by QR authorization.
3. Create Tap-to-Run actions in Smart Life for TV mute and TV unmute (or one mute-toggle action).
4. Record the cloud project Client ID, Client Secret, and Tap-to-Run rule IDs.
5. Provision those secrets into encrypted NVS at setup time. Never commit them to Git.

`tuya_scene.c` implements Tuya HMAC-SHA256 request signing, token refresh, and the `/v2.0/cloud/scene/rule/{rule_id}/actions/trigger` call.

## Fingerprint engine plan

Do not build a neural ad classifier first. The primary detector should be Shazam-style acoustic fingerprint matching because a positive match gives both identity and current offset.

Recommended embedded implementation:

- Input: shared 16 kHz signed 16-bit mono PCM.
- Window: 1024 or 2048 samples with 50% overlap.
- FFT: Espressif ESP-DSP (optimized ESP32-S3 implementation).
- Features: local spectral peaks in logarithmic frequency bands.
- Hash: anchor peak + target peak frequency bins + delta-time.
- Lookup: sorted/hash-indexed fingerprint records stored on SSD/flash.
- Match decision: cluster hash hits by `(ad_id, time_offset)` and require a minimum vote count plus confidence threshold.
- Output: `ad_guard_report_match()`.

A fingerprint record should contain only compact hashes and timing metadata; raw commercial audio does not need to live on BMO.

## Automatic database updates

The local catalog should support signed incremental packs:

```
manifest.json
  version
  generated_at
  pack_sha256
  signature

ads.pack
  ad_id
  duration_ms
  hash -> offset_ms
```

BMO downloads a new manifest periodically, verifies its signature, then atomically swaps the fingerprint pack. Candidate fingerprints discovered from DNS/network hints or manual mute events can be queued for review without automatically trusting them.

## Privacy / safety rules

- Never upload ambient microphone recordings by default.
- Candidate submissions should prefer hashes, duration, coarse source metadata, and optional user confirmation.
- Tuya Client Secret belongs in encrypted NVS, not source code or logs.
- A failed Tuya request must never leave BMO believing the TV is muted; log actuator failures separately from detector state.

## Current branch status

Implemented:

- `ad_guard.c/.h`: ad-match timing, confidence gate, remainder calculation, back-to-back extension, and timed restore.
- `tuya_scene.c/.h`: Tuya Cloud authentication/signing, token refresh, and Tap-to-Run trigger support.

Still required before enabling in `app_main`:

- Shared PCM tap from the wake-word/microphone pipeline.
- ESP-DSP fingerprint extractor + local lookup database.
- Encrypted-NVS provisioning UI for Tuya credentials and rule IDs.
- SNTP readiness gate before Tuya calls.
- Async actuator worker so cloud calls never block the audio pipeline or ESP timer task.
- Tests with recorded TV audio and real Tuya IR hardware.
