#!/usr/bin/env python3
"""Generate BMO's "thinking" sounds for the reserved sound-bank slots.

Every generated speech bank keeps slots 3 and 19 (0.32 s each) and slot 9
(2.0 s) loaded with these instead of silence, so a thinking loop can play
them while the brain or synthesizer is busy — no extra flash writes.

Slot 3 / 19: original chiptune blips (hmm? / boop).
Slot 9: neural BMO hum via the voice server when it's up; chiptune
arpeggio fallback otherwise.  Output: assets/bmo-thinking-sounds/*.wav
(22050 Hz mono s16le, peak-matched to the speech loudness target).
"""

from __future__ import annotations

import json
import math
import struct
import sys
import urllib.request
from array import array
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from neatobmo import tts_bank

OUT = Path(__file__).resolve().parents[1] / "assets/bmo-thinking-sounds"
RATE = tts_bank.PCM_SAMPLE_RATE
PEAK = tts_bank.NORMALIZE_PEAK
LIMITS = {"thinking-blip-a": 7088, "thinking-blip-b": 7088,
          "thinking-hum": 44291}  # slot 3 / 19 / 9 sample capacities


def tone(frequencies, seconds, wobble=0.0):
    n = int(seconds * RATE)
    note = max(1, n // len(frequencies))
    out = []
    for k, freq in enumerate(frequencies):
        for i in range(note):
            t = i / RATE
            local = i / note
            envelope = max(0.0, min(local / 0.10, (1.0 - local) / 0.25, 1.0))
            phase = 2 * math.pi * freq * (1 + wobble * math.sin(2 * math.pi * 5 * t)) * t
            sample = 0.7 * math.sin(phase) + 0.2 * math.sin(3 * phase)
            out.append(sample * envelope)
    peak = max(abs(s) for s in out) or 1.0
    return array("h", (int(s / peak * PEAK) for s in out))


def neural_hum():
    req = urllib.request.Request(
        "http://127.0.0.1:8486/synth",
        data=json.dumps({"text": "Hmmm. Hm hm hmmm."}).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as resp:
        wav = resp.read()
    return tts_bank.prepare_speech_pcm(wav)


def write(name, samples):
    limit = LIMITS[name]
    if len(samples) > limit:
        samples = samples[:limit]
        fade = min(len(samples), 600)
        for i in range(fade):
            samples[len(samples) - 1 - i] = int(
                samples[len(samples) - 1 - i] * i / fade)
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{name}.wav"
    path.write_bytes(tts_bank.pcm_to_wav_bytes(samples.tobytes()))
    print(f"{name}: {len(samples) / RATE:.3f}s -> {path}")


def main():
    write("thinking-blip-a", tone((392, 523), 0.28))          # rising "hm?"
    write("thinking-blip-b", tone((659, 587), 0.24))          # soft boop
    try:
        hum = neural_hum()
        source = "neural BMO hum"
    except Exception as exc:
        print(f"voice server unavailable ({exc}); chiptune fallback")
        hum = tone((262, 330, 392, 330, 262, 196), 1.9, wobble=0.01)
        source = "chiptune arpeggio"
    write("thinking-hum", hum)
    print("hum source:", source)


if __name__ == "__main__":
    main()
