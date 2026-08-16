#!/usr/bin/env python3
"""Extract quarantined BMO catalog entries for human listening review."""
import argparse
import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from neatobmo.soundboard_voice import SoundboardVoice  # noqa: E402


def slug(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:72]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path,
                        default=ROOT / "tmp" / "bmo-soundboard-review")
    args = parser.parse_args()

    catalog = ROOT / "docs" / "bmo-soundboard" / "catalog.json"
    board = SoundboardVoice(catalog)
    args.output.mkdir(parents=True, exist_ok=True)
    review = []
    for number, sound in enumerate(board.quarantined_sounds, 1):
        filename = f"{number:02d}-{slug(sound['label'])}.wav"
        wav = board.render_for_review(sound["key"])
        if wav is None:
            continue
        (args.output / filename).write_bytes(wav)
        review.append({
            "number": number,
            "key": sound["key"],
            "label": sound["label"],
            "file": filename,
            "source_page_url": sound.get("source_page_url"),
            "decision": "pending",
        })
    (args.output / "review.json").write_text(
        json.dumps({"schema": 1, "clips": review}, indent=2) + "\n")
    print(f"Prepared {len(review)} quarantined clips in {args.output}")


if __name__ == "__main__":
    main()
