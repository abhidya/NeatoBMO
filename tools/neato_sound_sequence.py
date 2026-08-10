#!/usr/bin/env python3
"""Play sound-bank slot sequences as separate Neato PlaySound commands.

This helper deliberately sends one documented command per slot.  It never
constructs a combined-ID command such as ``PlaySound 1-2-3``.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import time
from typing import Iterable


def load_sequences(path: Path) -> dict[str, list[int]]:
    manifest = json.loads(path.read_text())
    sequences = manifest.get("sequences")
    if not isinstance(sequences, dict):
        raise ValueError(f"{path}: expected a sequences object")
    parsed: dict[str, list[int]] = {}
    for name, sequence in sequences.items():
        ids = sequence.get("ids") if isinstance(sequence, dict) else sequence
        if not isinstance(ids, list) or not ids:
            raise ValueError(f"{path}: sequence {name!r} must contain a non-empty ids list")
        parsed[name] = [int(sound_id) for sound_id in ids]
    return parsed


def commands_for_sequence(sequence: Iterable[int]) -> list[str]:
    return [f"PlaySound {int(sound_id)}" for sound_id in sequence]


def play_sequence(target: str | None, commands: list[str], delay: float) -> list[str]:
    from neatobmo.robot import Robot

    robot = Robot(target)
    try:
        replies: list[str] = []
        for command in commands:
            replies.append(robot.cmd(command))
            if delay > 0:
                time.sleep(delay)
        return replies
    finally:
        robot.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("sequence")
    parser.add_argument("--target", help="Neato serial target passed to neatobmo Robot")
    parser.add_argument(
        "--delay",
        type=float,
        default=0.0,
        help="Optional fallback delay between single-ID PlaySound commands; default is burst mode.",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually contact the robot. Without this flag, only print the commands.",
    )
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    sequences = load_sequences(args.manifest)
    if args.sequence not in sequences:
        known = ", ".join(sorted(sequences))
        raise SystemExit(f"unknown sequence {args.sequence!r}; known sequences: {known}")
    commands = commands_for_sequence(sequences[args.sequence])
    if not args.execute:
        print("\n".join(commands))
        return 0
    replies = play_sequence(args.target, commands, args.delay)
    print(json.dumps({"commands": commands, "replies": replies}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
