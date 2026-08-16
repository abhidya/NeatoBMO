#!/usr/bin/env python3
"""Offline low-entropy key-candidate sweep for the Cruz .enc envelope.

Brute-forcing AES-128 is infeasible (2^128).  This tool attacks the only
bounded case: a key *derived* from a human string via raw-pad / MD5 / SHA1 /
SHA256.  It is offline and read-only; it never writes flash or contacts the
robot.

Oracle: a correct decryption of the app region must start with an ARM926
exception vector table (8 words whose condition nibble is 0xE) and must
contain known command strings.  Wrong keys stay full-entropy.  The first
1024 bytes are scored cheaply; a full-body marker scan runs only on pass.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import struct
from collections import Counter
from pathlib import Path

try:
    from Crypto.Cipher import AES
except Exception as exc:  # pragma: no cover
    raise SystemExit("pycryptodome required: pip install pycryptodome") from exc

# Default archive path, overridable with --image.
DEFAULT_ENC = (
    "/Volumes/2TB/neato-firmware-archive/sources/"
    "Neato-XV-Series-Cruz-Rev-113-Update/OriginalVorwerkFirmwareFiles/"
    "Firmware25/XV11App.15893.P.bin.enc"
)

KNOWN_MARKERS = (
    b"Neato Robotics", b"PlaySound", b"GetVersion", b"SetMotor",
    b"SetLCD", b"SetLDSRotation", b"Copyright", b"NEROS",
)

# Curated seeds: every human-meaningful string the key could plausibly derive
# from.  Add more; each costs microseconds.
DEFAULT_SEEDS = [
    # leaked / known
    "VORVR100!%", "VORVR100", "vr100", "VR100", "kobold", "Kobold",
    "Vorwerk", "vorwerk", "Vorwerk VR100", "kobold vr100",
    "neato", "Neato", "NEATO", "Neato Robotics", "NeatoRobotics",
    "xv11", "XV11", "XV-11", "xv-11", "xv11app", "XV11App", "XV-11 App",
    "xv12", "XV12", "XV-12", "xv21", "XV21", "XV-21",
    "cruz", "Cruz", "Rev113", "rev113", "Cruz Rev113",
    "binky", "Binky", "Rev64", "rev64",
    "NEROS", "neros", "NeatoOS",
    # build numbers
    "15667", "15893", "16621", "17844", "18755", "17235", "15295",
    "2.4.15667", "2.5.15893", "2.7.16621", "3.1.17844", "3.2.18755",
    "Software 2.4", "Software 2.5",
    # serial / hardware
    "WTD41611DD", "WTD41611", "0037829", "WTD41611DD-0037829-P",
    "mainboard 7.1", "AT91SAM9XE128", "AT91SAM9XE", "SAM9XE",
    # artifacts / magic
    "DfltSoundLib", "DfltSoundLib.Rev1.0.bin", "LDS_15295", "Config.ini",
    "neato\x00", "magic", "IV", "iv",
]


def entropy(data: bytes) -> float:
    if not data:
        return 0.0
    counts = Counter(data)
    n = len(data)
    return -sum((c / n) * math.log2(c / n) for c in counts.values())


def derive_keys(seed: bytes) -> dict[str, bytes]:
    m = hashlib.md5(seed).digest()
    return {
        "raw16": (seed + b"\x00" * 16)[:16],
        "md5": m,
        "sha1_16": hashlib.sha1(seed).digest()[:16],
        "sha256_16": hashlib.sha256(seed).digest()[:16],
    }


def rules(seed: str) -> set[str]:
    out = {seed, seed.lower(), seed.upper(), seed.capitalize()}
    out.add(re.sub(r"[^a-z0-9]", "", seed.lower()))
    leet = (seed.lower().replace("a", "4").replace("e", "3")
            .replace("i", "1").replace("o", "0").replace("s", "5"))
    out.add(leet)
    out.add(seed[::-1])
    return out


def arm_vector_score(data: bytes, words: int = 8) -> tuple[int, int, int]:
    """(condition-nibble hits, total, branch opcodes) over the first words."""
    hits = branch = total = 0
    limit = min(len(data), words * 4)
    for off in range(0, limit - 3, 4):
        total += 1
        if (data[off + 3] & 0xF0) == 0xE0:
            hits += 1
        if data[off + 3] in (0xEA, 0xEB):
            branch += 1
    return hits, total, branch


def score_plaintext(pt: bytes) -> dict:
    hits, total, branch = arm_vector_score(pt)
    markers = [m.decode("ascii") for m in KNOWN_MARKERS if m in pt]
    return {
        "arm_condition_hits": hits,
        "arm_condition_total": total,
        "branch_opcodes": branch,
        "entropy_first_1024": round(entropy(pt[:1024]), 4),
        "markers_found": markers,
        "looks_like_arm_firmware": hits >= max(6, total * 3 // 4)
                                   and branch >= 4,
    }


def run(image_path: Path, seeds: list[str], fast: bool) -> list[dict]:
    data = image_path.read_bytes()
    header = data[:512]
    payload = data[512:]
    ivs = {
        "zeros": b"\x00" * 16,
        "header_16_32": header[16:32],
        "header_0_16": header[0:16],
        "ff": b"\xff" * 16,
    }
    # fast mode: score only the first 1024 bytes of plaintext
    sample = payload[:1024]
    results: list[dict] = []
    checked = 0
    for seed in seeds:
        for variant in rules(seed):
            vbytes = variant.encode("latin-1")
            for deriv, key in derive_keys(vbytes).items():
                for ivname, iv in ivs.items():
                    checked += 1
                    pt = AES.new(key, AES.MODE_CBC, iv).decrypt(sample)
                    score = score_plaintext(pt)
                    if fast or score["looks_like_arm_firmware"]:
                        full = AES.new(key, AES.MODE_CBC, iv).decrypt(payload)
                        full_score = score_plaintext(full)
                        score = full_score
                    if score["looks_like_arm_firmware"] or score["markers_found"]:
                        results.append({
                            "seed": seed, "variant": variant,
                            "derivation": deriv, "iv": ivname,
                            "key_hex": key.hex(), "score": score,
                        })
    return results, checked


def public_hits(hits: list[dict]) -> list[dict]:
    """Redact candidate secrets while retaining reproducible evidence."""
    return [
        {
            "key_sha256": hashlib.sha256(
                bytes.fromhex(hit["key_hex"])
            ).hexdigest(),
            "iv": hit["iv"],
            "score": hit["score"],
        }
        for hit in hits
    ]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", type=Path, default=Path(DEFAULT_ENC))
    parser.add_argument("--seeds", type=Path,
                        help="optional newline-separated extra wordlist")
    parser.add_argument("--full", action="store_true",
                        help="full-body marker scan on every candidate")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)

    seeds = list(DEFAULT_SEEDS)
    if args.seeds:
        seeds += [line.strip() for line in args.seeds.read_text().splitlines()
                  if line.strip() and not line.startswith("#")]

    hits, checked = run(args.image, seeds, fast=not args.full)
    report = {
        "image": str(args.image),
        "candidates_checked": checked,
        "seed_count": len(seeds),
        "hits": public_hits(hits),
    }
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(rendered)
    print(rendered, end="")
    return 0 if not hits else 1


if __name__ == "__main__":
    raise SystemExit(main())
