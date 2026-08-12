#!/usr/bin/env python3
"""Generate deterministic offline NeatoOS Phase A .enc probe containers."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import struct
import tempfile
from typing import Iterable


SCHEMA = "neatoos-probe-image/v1"
HEADER_SIZE = 512
PAGE_SIZE = 512
FORMAT_BYTE = 0x02
MARKER = b"neato"
RAW_LABEL = "raw-structural"
REFERENCE_LABEL = "reference-header"
FULL_REFERENCE_LABEL = "reference-header-full-length"
NOT_ENCRYPTED = "NOT ENCRYPTED"
NOT_AUTHENTICATED = "NOT AUTHENTICATED"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def page_pad(data: bytes) -> bytes:
    padding = (-len(data)) % PAGE_SIZE
    return data + (b"\0" * padding)


def experimental_field(raw: bytes) -> bytes:
    return hashlib.sha256(b"neatoos raw structural experimental field v1\0" + raw).digest()[:16]


def deterministic_filler(length: int) -> bytes:
    """Return deterministic non-secret bytes for the controlled NAND tail.

    The stream is counter-mode SHA-256 with a fixed domain separator.  It is
    deliberately not encryption and exists only to make every uploaded byte
    known and reproducible.
    """
    output = bytearray()
    counter = 0
    while len(output) < length:
        output.extend(
            hashlib.sha256(
                b"neatoos full-length deterministic filler v1\0"
                + counter.to_bytes(8, "little")
            ).digest()
        )
        counter += 1
    return bytes(output[:length])


def structural_header(raw: bytes) -> bytes:
    header = bytearray(HEADER_SIZE)
    struct.pack_into("<I", header, 0, len(raw))
    header[4] = FORMAT_BYTE
    header[5 : 5 + len(MARKER)] = MARKER
    header[16:32] = experimental_field(raw)
    return bytes(header)


@dataclass(frozen=True)
class OutputImage:
    label: str
    path: Path
    data: bytes
    header_source: str
    header_sha256: str
    raw_payload_size: int
    padded_payload_size: int
    raw_payload_sha256: str
    declared_length: int
    unknown_0x10_0x1f_value: str
    expected_experiment: str
    declared_length_matches_payload: bool

    def manifest(self) -> dict[str, object]:
        return {
            "schema": SCHEMA,
            "label": self.label,
            "warnings": [NOT_ENCRYPTED, NOT_AUTHENTICATED],
            "encryption_status": NOT_ENCRYPTED,
            "authentication_status": NOT_AUTHENTICATED,
            "expected_experiment": self.expected_experiment,
            "path": str(self.path),
            "size": len(self.data),
            "sha256": sha256(self.data),
            "output_sha256": sha256(self.data),
            "header": {
                "source": self.header_source,
                "size": HEADER_SIZE,
                "sha256": self.header_sha256,
                "declared_length": self.declared_length,
                "unknown_0x10_0x1f_value": self.unknown_0x10_0x1f_value,
                "declared_length_matches_payload": self.declared_length_matches_payload,
            },
            "payload": {
                "raw_size": self.raw_payload_size,
                "padded_size": self.padded_payload_size,
                "padding": self.padded_payload_size - self.raw_payload_size,
                "raw_sha256": self.raw_payload_sha256,
                "page_padding_size": self.padded_payload_size - self.raw_payload_size,
            },
            "safety": {
                "hardware_access": False,
                "upload_commands": False,
                "flash_commands": False,
                "erase_commands": False,
                "encrypted": False,
                "authenticated": False,
            },
            "note": "Offline probe container only; not an encrypted or authenticated Neato firmware image.",
        }


def verify_reference(reference: Path, expected_sha256: str) -> bytes:
    data = reference.read_bytes()
    digest = sha256(data)
    if digest.lower() != expected_sha256.lower():
        raise SystemExit(
            f"reference SHA-256 mismatch: expected {expected_sha256}, got {digest}"
        )
    if len(data) < HEADER_SIZE:
        raise SystemExit(f"reference image is shorter than {HEADER_SIZE} bytes")
    return data[:HEADER_SIZE]


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def generate(
    raw_path: Path,
    reference_path: Path,
    reference_sha256: str,
    out_dir: Path,
    stem: str,
) -> list[OutputImage]:
    raw = raw_path.read_bytes()
    padded_payload = page_pad(raw)
    reference = verify_reference(reference_path, reference_sha256)
    reference_total_size = reference_path.stat().st_size

    out_dir.mkdir(parents=True, exist_ok=True)

    structural = structural_header(raw) + padded_payload
    reference_header = reference + padded_payload
    full_payload_size = reference_total_size - HEADER_SIZE
    if len(padded_payload) > full_payload_size:
        raise SystemExit("raw payload plus page padding exceeds reference image body size")
    full_reference_body = padded_payload + deterministic_filler(
        full_payload_size - len(padded_payload)
    )
    full_reference_header = reference + full_reference_body
    structural_unknown = structural[16:32].hex()
    reference_unknown = reference[16:32].hex()
    reference_declared_length = struct.unpack_from("<I", reference, 0)[0]

    outputs = [
        OutputImage(
            label=RAW_LABEL,
            path=out_dir / "neatoos-structural-probe.bin.enc",
            data=structural,
            header_source="synthetic-neato-structural-header",
            header_sha256=sha256(structural[:HEADER_SIZE]),
            raw_payload_size=len(raw),
            padded_payload_size=len(padded_payload),
            raw_payload_sha256=sha256(raw),
            declared_length=len(raw),
            unknown_0x10_0x1f_value=structural_unknown,
            expected_experiment="synthetic structural envelope with raw body replacement",
            declared_length_matches_payload=True,
        ),
        OutputImage(
            label=REFERENCE_LABEL,
            path=out_dir / "neatoos-reference-header-probe.bin.enc",
            data=reference_header,
            header_source=str(reference_path),
            header_sha256=sha256(reference),
            raw_payload_size=len(raw),
            padded_payload_size=len(padded_payload),
            raw_payload_sha256=sha256(raw),
            declared_length=reference_declared_length,
            unknown_0x10_0x1f_value=reference_unknown,
            expected_experiment=(
                "verified Cruz-P 2.5 reference header with raw body replacement; "
                "declared header length intentionally remains vendor original"
            ),
            declared_length_matches_payload=(reference_declared_length == len(raw)),
        ),
        OutputImage(
            label=FULL_REFERENCE_LABEL,
            path=out_dir / "neatoos-reference-header-full-length-probe.bin.enc",
            data=full_reference_header,
            header_source=str(reference_path),
            header_sha256=sha256(reference),
            raw_payload_size=len(raw),
            padded_payload_size=len(full_reference_body),
            raw_payload_sha256=sha256(raw),
            declared_length=reference_declared_length,
            unknown_0x10_0x1f_value=reference_unknown,
            expected_experiment=(
                "verified Cruz-P 2.5 reference header, identical first 1024 bytes "
                "to the short reference-header probe, then deterministic filler "
                "to the exact 805888-byte vendor file size"
            ),
            declared_length_matches_payload=(
                reference_declared_length == len(full_reference_body)
            ),
        ),
    ]

    for output in outputs:
        output.path.write_bytes(output.data)
        write_json(output.path.with_suffix(output.path.suffix + ".manifest.json"), output.manifest())

    write_json(
        out_dir / f"{stem}.manifest.json",
        {
            "schema": "neatoos-probe-set/v1",
            "raw_input": {
                "path": str(raw_path),
                "size": len(raw),
                "sha256": sha256(raw),
            },
            "reference_input": {
                "path": str(reference_path),
                "sha256": reference_sha256.lower(),
                "header_sha256": sha256(reference),
                "size": reference_total_size,
            },
            "outputs": [output.manifest() for output in outputs],
        },
    )

    return outputs


def self_test() -> dict[str, object]:
    with tempfile.TemporaryDirectory() as directory:
        temp = Path(directory)
        raw = temp / "neatoos-raw.bin"
        reference = temp / "reference.enc"
        out_dir = temp / "out"
        raw.write_bytes(b"NEATOOS RAW V0\r\n")
        reference.write_bytes(structural_header(b"reference") + page_pad(b"reference"))
        outputs = generate(raw, reference, sha256(reference.read_bytes()), out_dir, "selftest")
        return {
            "schema": "neatoos-probe-self-test/v1",
            "outputs": [output.manifest() for output in outputs],
        }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("raw", type=Path, nargs="?")
    parser.add_argument("--reference", type=Path)
    parser.add_argument("--reference-sha256")
    parser.add_argument("--out-dir", type=Path, default=Path("build/probes"))
    parser.add_argument("--stem", default="neatoos-raw")
    parser.add_argument("--self-test", action="store_true")
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.self_test:
        print(json.dumps(self_test(), indent=2, sort_keys=True))
        return 0

    if args.raw is None or args.reference is None or args.reference_sha256 is None:
        raise SystemExit("raw, --reference, and --reference-sha256 are required")
    if not args.raw.is_file():
        raise SystemExit(f"missing raw payload: {args.raw}")
    if not args.reference.is_file():
        raise SystemExit(f"missing reference image: {args.reference}")

    generate(args.raw, args.reference, args.reference_sha256, args.out_dir, args.stem)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
