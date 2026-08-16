#!/usr/bin/env python3
"""Fingerprint and remove known soundboard idents/whooshes from BMO clips."""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

LIBRARY = ROOT / "assets" / "bmo-soundboard-library-20260810"
PUBLISH = ROOT / "docs" / "bmo-soundboard"
APPROVALS = ROOT / "docs" / "bmo-clip-approvals.json"
EDITORIAL = ROOT / "docs" / "bmo-audio-editorial.json"
SAMPLE_RATE = 22_050
MATCH_THRESHOLD = 0.70

# Templates are isolated from source MP3s so already-trimmed runtime WAVs do
# not erase the detector's reference. Times were established by human QA.
TEMPLATES = {
    "whoosh-tone": ("101-24061886-bmo-hello", 0.62, 1.18),
    "whoosh-sweep": ("101-28062487-i-love-you", 0.87, 1.57),
    "spoken-101-ident": (
        "101-28055268-who-wants-to-play-video-games", 0.24, 1.11),
    "spoken-101soundboards-dot-com": (
        "101-28054910-bmo-always-bounces-back", 0.06, 1.58),
}


def decode_mp3(path: Path) -> np.ndarray:
    command = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(path),
        "-ac", "1", "-ar", str(SAMPLE_RATE), "-af",
        "highpass=f=70,lowpass=f=10000,alimiter=limit=0.891251",
        "-f", "s16le", "-",
    ]
    return np.frombuffer(subprocess.check_output(command), dtype="<i2").astype(
        np.float64)


def read_wav(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as audio:
        return np.frombuffer(audio.readframes(audio.getnframes()),
                             dtype="<i2").astype(np.float64)


def correlation(audio: np.ndarray, template: np.ndarray) -> tuple[float, float]:
    # Downsampling keeps a full scan cheap while preserving these long,
    # broadband signatures. Normalized local energy prevents volume bias.
    audio = audio[:int(len(audio) * 0.7):10]
    template = template[::10]
    template -= template.mean()
    if len(audio) < len(template):
        return 0.0, 0.0
    raw = np.correlate(audio, template, "valid")
    window = np.ones(len(template))
    sums = np.convolve(audio, window, "valid")
    squares = np.convolve(audio * audio, window, "valid")
    variance = np.maximum(squares - sums * sums / len(template), 1.0)
    scores = raw / (np.sqrt(variance) * np.linalg.norm(template))
    index = int(np.argmax(scores))
    return float(scores[index]), index * 10 / SAMPLE_RATE


def silences(path: Path) -> list[tuple[float, float]]:
    command = [
        "ffmpeg", "-hide_banner", "-i", str(path), "-af",
        "silencedetect=noise=-38dB:d=0.08", "-f", "null", "-",
    ]
    stderr = subprocess.run(command, stdout=subprocess.DEVNULL,
                            stderr=subprocess.PIPE, text=True,
                            check=False).stderr
    starts = [float(value) for value in
              re.findall(r"silence_start: ([0-9.]+)", stderr)]
    ends = [float(value) for value in
            re.findall(r"silence_end: ([0-9.]+)", stderr)]
    return list(zip(starts, ends))


def trim_boundary(path: Path, artifact_end: float) -> float | None:
    candidates = [(start, end) for start, end in silences(path)
                  if start <= artifact_end + 0.15 and
                  end >= artifact_end - 0.05]
    if not candidates:
        return None
    return round(max(0.0, candidates[0][1] - 0.03), 3)


def audit(library: Path, publish: Path) -> list[dict]:
    source = json.loads((library / "catalog.json").read_text())
    sources = {sound["key"]: sound for sound in source["sounds"]}
    catalog = json.loads((publish / "catalog.json").read_text())
    approvals = json.loads(APPROVALS.read_text())
    excluded = set(approvals.get("approved", [])) | set(
        approvals.get("rejected", []))
    templates = {}
    for name, (key, start, end) in TEMPLATES.items():
        pcm = decode_mp3(library / sources[key]["source_mp3"])
        templates[name] = pcm[int(start * SAMPLE_RATE):int(end * SAMPLE_RATE)]

    findings = []
    for sound in catalog["sounds"]:
        key = sound["key"]
        if (sound.get("verification") != "dedicated-bmo-board-metadata" or
                key in excluded or key not in sources):
            continue
        path = library / sources[key]["wav"]
        audio = read_wav(path)
        matches = []
        for name, template in templates.items():
            score, start = correlation(audio, template.copy())
            matches.append((score, name, start, len(template) / SAMPLE_RATE))
        score, artifact, start, duration = max(matches)
        boundary = (trim_boundary(path, start + duration)
                    if score >= MATCH_THRESHOLD else None)
        findings.append({
            "key": key,
            "label": sound["label"],
            "artifact": artifact,
            "correlation": round(score, 4),
            "match_start_seconds": round(start, 3),
            "proposed_trim_start_seconds": boundary,
            "action": "trim" if boundary is not None else "review",
        })
    return findings


def apply(findings: list[dict], library: Path, publish: Path) -> None:
    accepted = [finding for finding in findings if finding["action"] == "trim"]
    editorial = json.loads(EDITORIAL.read_text())
    trims = editorial.setdefault("trim_start_seconds", {})
    evidence = editorial.setdefault("trim_evidence", {})
    for finding in accepted:
        key = finding["key"]
        trims[key] = finding["proposed_trim_start_seconds"]
        evidence[key] = {
            "artifact": finding["artifact"],
            "correlation": finding["correlation"],
            "review": "automatic-high-confidence",
        }
    EDITORIAL.write_text(json.dumps(editorial, indent=2) + "\n")

    # Import only after writing the manifest so builder module constants see
    # the newly accepted edits.
    from tools import build_bmo_sound_library as sound_library
    from tools.patch_bmo_flash_clips import patch_clips
    sound_library.refresh_existing(library)
    patch_clips(library, publish, [finding["key"] for finding in accepted])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--library", type=Path, default=LIBRARY)
    parser.add_argument("--publish", type=Path, default=PUBLISH)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    findings = audit(args.library.resolve(), args.publish.resolve())
    if args.apply:
        apply(findings, args.library.resolve(), args.publish.resolve())
    payload = {"threshold": MATCH_THRESHOLD, "findings": findings}
    rendered = json.dumps(payload, indent=2) + "\n"
    if args.output:
        args.output.write_text(rendered)
    print(rendered, end="")


if __name__ == "__main__":
    main()
