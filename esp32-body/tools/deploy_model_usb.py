#!/usr/bin/env python3
"""Stage a Colibri model onto a FAT volume the ESP32 can mount.

The firmware mounts the first FAT-formatted USB MSC device at /usb with
format_if_mount_failed=false, so the volume must already be FAT32 (or FAT16).
An APFS/exFAT/HFS+ disk enumerates but never mounts, which looks identical to
"no disk" in the boot log.

This copies the model and tokenizer into one of the two layouts usb_store.c
probes, verifies every byte after the copy, and refuses to touch a volume that
is not FAT. It never formats or partitions anything: pass a volume that is
already mounted and already FAT.

    python3 tools/deploy_model_usb.py /Volumes/BMO \\
        --model /Volumes/2TB/neatobmo-models/gemma-3-270m-q8_0/model.bmoq \\
        --tokenizer /Volumes/2TB/neatobmo-models/gemma-3-270m-q8_0/tokenizer.cspm
"""
from __future__ import annotations

import argparse
import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

# usb_store.c probes the root first, then this directory.
NESTED_DIR = Path("neatobmo-models/gemma-3-270m-q8_0")


def sha256(path: Path, chunk: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(chunk), b""):
            digest.update(block)
    return digest.hexdigest()


def filesystem_of(volume: Path) -> str:
    """Personality string from diskutil, e.g. 'MS-DOS FAT32'."""
    try:
        out = subprocess.run(["diskutil", "info", str(volume)],
                             capture_output=True, text=True, check=True).stdout
    except Exception:
        return "unknown"
    for line in out.splitlines():
        if "File System Personality" in line:
            return line.split(":", 1)[1].strip()
    return "unknown"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("volume", type=Path, help="mounted FAT volume, e.g. /Volumes/BMO")
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--tokenizer", type=Path, required=True)
    parser.add_argument("--prompt", type=Path, default=None,
                        help="optional prompt.txt used by the autorun demo")
    parser.add_argument("--nested", action="store_true",
                        help=f"stage under {NESTED_DIR} instead of the volume root")
    parser.add_argument("--allow-non-fat", action="store_true",
                        help="copy anyway (the ESP32 will not mount it)")
    args = parser.parse_args()

    if not args.volume.is_dir():
        print(f"error: {args.volume} is not a mounted volume", file=sys.stderr)
        return 2
    for source in (args.model, args.tokenizer):
        if not source.is_file():
            print(f"error: missing {source}", file=sys.stderr)
            return 2

    personality = filesystem_of(args.volume)
    fat = "FAT" in personality.upper()
    print(f"volume:     {args.volume}  [{personality}]")
    if not fat and not args.allow_non_fat:
        print(f"error: {args.volume} is {personality}, not FAT. The firmware mounts\n"
              f"       FAT only (format_if_mount_failed=false), so this volume would\n"
              f"       enumerate but never mount. Reformat it as MS-DOS (FAT32) or\n"
              f"       pass --allow-non-fat to copy anyway.", file=sys.stderr)
        return 1

    destination = args.volume / NESTED_DIR if args.nested else args.volume
    destination.mkdir(parents=True, exist_ok=True)

    free = shutil.disk_usage(args.volume).free
    needed = args.model.stat().st_size + args.tokenizer.stat().st_size
    print(f"needed:     {needed / 1e6:.1f} MB      free: {free / 1e6:.1f} MB")
    if needed > free:
        print("error: not enough free space on the volume", file=sys.stderr)
        return 1

    staged = [(args.model, destination / "model.bmoq"),
              (args.tokenizer, destination / "tokenizer.cspm")]
    if args.prompt and args.prompt.is_file():
        staged.append((args.prompt, destination / "prompt.txt"))

    for source, target in staged:
        print(f"copying:    {source.name} -> {target}")
        shutil.copyfile(source, target)

    print("verifying every byte after copy...")
    for source, target in staged:
        want, got = sha256(source), sha256(target)
        status = "OK" if want == got else "MISMATCH"
        print(f"  {target.name:16s} {status}  {got[:16]}")
        if want != got:
            print("error: copy verification failed", file=sys.stderr)
            return 1

    print("\nstaged. Move the drive to the ESP32's USB host port; the boot log "
          "should show coli_msc mounting it at /usb.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
