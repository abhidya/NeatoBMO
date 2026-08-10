#!/usr/bin/env python3
"""Inspect and validate archived Neato XV firmware without flashing it."""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict, dataclass
import hashlib
import json
import math
from pathlib import Path
import re
import struct
from typing import Iterable


HEADER_SIZE = 512
BLOCK_SIZE = 16
PAGE_SIZE = 512
KNOWN_PLAINTEXT_MARKERS = (
    b"Neato Robotics",
    b"PlaySound",
    b"GetVersion",
    b"SetMotor",
    b"Copyright",
    b"NEROS",
)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def entropy(data: bytes) -> float:
    if not data:
        return 0.0
    counts = Counter(data)
    length = len(data)
    return -sum((count / length) * math.log2(count / length) for count in counts.values())


def printable_strings(data: bytes, minimum: int = 6) -> list[str]:
    pattern = rb"[\x20-\x7e]{" + str(minimum).encode() + rb",}"
    return [match.decode("ascii") for match in re.findall(pattern, data)]


@dataclass(frozen=True)
class EncryptedImage:
    path: str
    file_size: int
    file_sha256: str
    declared_plaintext_size: int
    format_version: int
    marker: str
    authenticator_hex: str
    reserved_header_nonzero_bytes: int
    payload_size: int
    page_padding_size: int
    payload_sha256: str
    payload_entropy: float
    duplicate_cipher_blocks: int

    @classmethod
    def read(cls, path: Path) -> "EncryptedImage":
        data = path.read_bytes()
        if len(data) < HEADER_SIZE:
            raise ValueError(f"{path}: shorter than the {HEADER_SIZE}-byte image header")
        declared_size = struct.unpack_from("<I", data)[0]
        format_version = data[4]
        marker = data[5:16].split(b"\0", 1)[0].decode("ascii", errors="replace")
        payload = data[HEADER_SIZE:]
        if not payload or len(payload) % PAGE_SIZE:
            raise ValueError(f"{path}: encrypted payload is not page aligned")
        if declared_size > len(payload):
            raise ValueError(f"{path}: declared plaintext is larger than its payload")
        blocks = [payload[offset : offset + BLOCK_SIZE]
                  for offset in range(0, len(payload), BLOCK_SIZE)]
        block_counts = Counter(blocks)
        duplicates = sum(count - 1 for count in block_counts.values() if count > 1)
        return cls(
            path=str(path),
            file_size=len(data),
            file_sha256=sha256(data),
            declared_plaintext_size=declared_size,
            format_version=format_version,
            marker=marker,
            authenticator_hex=data[16:32].hex(),
            reserved_header_nonzero_bytes=sum(byte != 0 for byte in data[32:HEADER_SIZE]),
            payload_size=len(payload),
            page_padding_size=len(payload) - declared_size,
            payload_sha256=sha256(payload),
            payload_entropy=round(entropy(payload), 6),
            duplicate_cipher_blocks=duplicates,
        )


def arm32_screening_signals(data: bytes, sample_words: int = 256) -> dict[str, object]:
    """Return informational ARM32 signals without assuming the target ISA.

    A random word has a 1/16 chance of using ARM's AL condition nibble, so the
    older "anything except 0xf" test was not discriminating.  These signals
    are useful when reverse engineering identifies an ARM image, but they are
    deliberately not an unlock gate until the Cruz processor ISA is proven.
    """
    word_count = min(len(data) // 4, sample_words)
    if not word_count:
        return {"sample_words": 0, "al_condition_ratio": 0.0,
                "vector_branch_count": 0}
    always = 0
    for offset in range(0, word_count * 4, 4):
        word = struct.unpack_from("<I", data, offset)[0]
        if word >> 28 == 0xE:
            always += 1
    vector_words = min(word_count, 8)
    vector_branches = sum(
        1 for offset in range(0, vector_words * 4, 4)
        if data[offset + 3] in (0xEA, 0xEB)
    )
    return {
        "sample_words": word_count,
        "al_condition_ratio": round(always / word_count, 6),
        "vector_branch_count": vector_branches,
    }


def validate_plaintext(image: EncryptedImage, plaintext: bytes,
                       repacked: bytes | None = None) -> dict[str, object]:
    declared = image.declared_plaintext_size
    content = plaintext[:declared]
    found_markers = [marker.decode("ascii") for marker in KNOWN_PLAINTEXT_MARKERS
                     if marker in content]
    strings = printable_strings(content)
    arm32_signals = arm32_screening_signals(content)
    checks: dict[str, bool] = {
        "covers_declared_size": len(plaintext) >= declared,
        "has_neato_code_markers": len(found_markers) >= 2,
        "has_substantial_strings": len(strings) >= 20,
        "plaintext_entropy_below_ciphertext": entropy(content) < image.payload_entropy,
    }
    if repacked is not None:
        checks["exact_repack"] = sha256(repacked) == image.file_sha256
    required = tuple(checks) if repacked is not None else tuple(
        key for key in checks if key != "exact_repack"
    )
    return {
        "passed": all(checks[key] for key in required),
        "checks": checks,
        "declared_plaintext_size": declared,
        "provided_plaintext_size": len(plaintext),
        "plaintext_sha256": sha256(content),
        "plaintext_entropy": round(entropy(content), 6),
        "arm32_screening_signals": arm32_signals,
        "found_markers": found_markers,
        "printable_string_count": len(strings),
        "sample_strings": strings[:20],
    }


def catalog(root: Path) -> dict[str, object]:
    files = []
    hash_groups: dict[str, list[str]] = {}
    for path in sorted(item for item in root.rglob("*") if item.is_file()
                       and ".git" not in item.parts):
        data = path.read_bytes()
        digest = sha256(data)
        relative = str(path.relative_to(root))
        record: dict[str, object] = {
            "path": relative,
            "size": len(data),
            "sha256": digest,
        }
        if path.name.endswith(".bin.enc"):
            record["encrypted_image"] = asdict(EncryptedImage.read(path))
        files.append(record)
        hash_groups.setdefault(digest, []).append(relative)
    duplicates = [
        {"sha256": digest, "paths": paths}
        for digest, paths in sorted(hash_groups.items())
        if len(paths) > 1
    ]
    return {
        "root": str(root),
        "file_count": len(files),
        "files": files,
        "duplicate_content_groups": duplicates,
    }


def emit(value: object, output: Path | None) -> None:
    rendered = json.dumps(value, indent=2, sort_keys=True) + "\n"
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered)
    else:
        print(rendered, end="")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="action", required=True)

    inspect_parser = subparsers.add_parser("inspect")
    inspect_parser.add_argument("images", type=Path, nargs="+")
    inspect_parser.add_argument("--output", type=Path)

    catalog_parser = subparsers.add_parser("catalog")
    catalog_parser.add_argument("root", type=Path)
    catalog_parser.add_argument("--output", type=Path)

    validate_parser = subparsers.add_parser("validate-unlock")
    validate_parser.add_argument("image", type=Path)
    validate_parser.add_argument("plaintext", type=Path)
    validate_parser.add_argument("--repacked", type=Path)
    validate_parser.add_argument("--output", type=Path)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.action == "inspect":
        emit([asdict(EncryptedImage.read(path)) for path in args.images], args.output)
        return 0
    if args.action == "catalog":
        emit(catalog(args.root), args.output)
        return 0

    image = EncryptedImage.read(args.image)
    repacked = args.repacked.read_bytes() if args.repacked else None
    result = validate_plaintext(image, args.plaintext.read_bytes(), repacked)
    emit(result, args.output)
    return 0 if result["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
