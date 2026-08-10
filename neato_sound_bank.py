#!/usr/bin/env python3
"""Inspect and safely stage Neato XV sound-library module images.

This intentionally does not edit a sound slot or upload anything.  The public
``DfltSoundLib.Rev1.0.bin`` image is a page-oriented module; its internal slot
format is not yet proven.  These commands provide reproducible evidence for a
future one-slot experiment without pretending that a raw PCM offset is an
editable slot boundary.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import shutil
import struct
from typing import Iterable
import wave


PAGE_SIZE = 512
PAGE_MAGIC = b"KT"
DEFAULT_AUDIO_START_PAGE = 8
PCM_SAMPLE_RATE = 22_050
PCM_BYTES_PER_SAMPLE = 2


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class SoundBank:
    path: str
    file_size: int
    sha256: str
    page_size: int
    page_count: int
    header_page_count: int
    header_magic: str
    declared_value: int
    page_header_indices: list[int]
    audio_start_page: int
    raw_pcm_bytes: int
    raw_pcm_seconds: float

    @classmethod
    def read(cls, path: Path) -> "SoundBank":
        data = path.read_bytes()
        if not data or len(data) % PAGE_SIZE:
            raise ValueError("sound library must be non-empty and 512-byte aligned")
        if data[:2] != PAGE_MAGIC:
            raise ValueError("sound library does not begin with the expected KT marker")

        page_count = len(data) // PAGE_SIZE
        header_pages: list[int] = []
        for page in range(page_count):
            offset = page * PAGE_SIZE
            if data[offset:offset + 2] != PAGE_MAGIC:
                break
            header_pages.append(page)

        if len(header_pages) < DEFAULT_AUDIO_START_PAGE:
            raise ValueError("expected at least eight KT header pages before PCM data")

        declared_value = struct.unpack_from("<H", data, 4)[0]
        raw_pcm_bytes = len(data) - DEFAULT_AUDIO_START_PAGE * PAGE_SIZE
        return cls(
            path=str(path),
            file_size=len(data),
            sha256=sha256(data),
            page_size=PAGE_SIZE,
            page_count=page_count,
            header_page_count=len(header_pages),
            header_magic=data[:2].decode("ascii"),
            declared_value=declared_value,
            page_header_indices=header_pages,
            audio_start_page=DEFAULT_AUDIO_START_PAGE,
            raw_pcm_bytes=raw_pcm_bytes,
            raw_pcm_seconds=round(raw_pcm_bytes / (PCM_SAMPLE_RATE * PCM_BYTES_PER_SAMPLE), 6),
        )


def emit(value: object, output: Path | None) -> None:
    rendered = json.dumps(value, indent=2, sort_keys=True) + "\n"
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered)
    else:
        print(rendered, end="")


def copy_identity(source: Path, destination: Path) -> dict[str, object]:
    if source.resolve() == destination.resolve():
        raise ValueError("source and destination must differ")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    source_hash = sha256(source.read_bytes())
    destination_hash = sha256(destination.read_bytes())
    return {
        "source": str(source),
        "destination": str(destination),
        "source_sha256": source_hash,
        "destination_sha256": destination_hash,
        "exact_match": source_hash == destination_hash,
    }


def extract_pcm(source: Path, destination: Path) -> dict[str, object]:
    bank = SoundBank.read(source)
    data = source.read_bytes()[bank.audio_start_page * PAGE_SIZE:]
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)
    return {
        "source": str(source),
        "destination": str(destination),
        "offset": bank.audio_start_page * PAGE_SIZE,
        "bytes": len(data),
        "format": "signed 16-bit little-endian mono PCM, 22050 Hz (format inferred from existing project evidence)",
        "sha256": sha256(data),
    }


def slot_page_table(source: Path) -> list[tuple[int, int]]:
    """Return non-empty sound-ID/page pairs inferred from the first header page.

    The first header's declared value is 20 and its following 21 u16 values
    align with the documented `PlaySound 0..20` range.  This makes the table a
    strong structural inference, but not hardware proof: do not upload an
    edited module based solely on it.
    """
    bank = SoundBank.read(source)
    page_zero = source.read_bytes()[:PAGE_SIZE]
    entry_count = bank.declared_value + 1
    if 8 + entry_count * 2 > PAGE_SIZE:
        raise ValueError("declared sound-table length does not fit in header page")
    table = struct.unpack_from(f"<{entry_count}H", page_zero, 8)
    return [
        (sound_id, page)
        for sound_id, page in enumerate(table)
        if bank.audio_start_page <= page < bank.page_count
    ]


def candidate_boundaries(source: Path) -> list[dict[str, object]]:
    bank = SoundBank.read(source)
    slots = slot_page_table(source)
    pages = [page for _, page in slots] + [bank.page_count]
    return [
        {
            "candidate_index": index,
            "inferred_sound_id": sound_id,
            "start_page": start,
            "end_page_exclusive": end,
            "start_offset": start * PAGE_SIZE,
            "byte_length": (end - start) * PAGE_SIZE,
            "duration_seconds": round((end - start) * PAGE_SIZE /
                                      (PCM_SAMPLE_RATE * PCM_BYTES_PER_SAMPLE), 6),
            "mapping_status": (
                "inferred from the 21-entry header table; verify against "
                "hardware before treating as a PlaySound ID"
            ),
        }
        for index, ((sound_id, start), end) in enumerate(zip(slots, pages[1:]))
    ]


def export_candidate_wavs(source: Path, destination: Path) -> dict[str, object]:
    data = source.read_bytes()
    destination.mkdir(parents=True, exist_ok=True)
    entries = candidate_boundaries(source)
    files: list[dict[str, object]] = []
    for entry in entries:
        start = int(entry["start_offset"])
        end = start + int(entry["byte_length"])
        output = destination / (
            f"candidate-{int(entry['candidate_index']):02d}-"
            f"slot-{int(entry['inferred_sound_id']):02d}-"
            f"pages-{int(entry['start_page'])}-{int(entry['end_page_exclusive'])}.wav"
        )
        with wave.open(str(output), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(PCM_BYTES_PER_SAMPLE)
            wav.setframerate(PCM_SAMPLE_RATE)
            wav.writeframes(data[start:end])
        files.append({**entry, "path": str(output), "sha256": sha256(output.read_bytes())})
    return {"source": str(source), "candidate_files": files}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)

    inspect_parser = actions.add_parser("inspect")
    inspect_parser.add_argument("image", type=Path)
    inspect_parser.add_argument("--output", type=Path)

    copy_parser = actions.add_parser("copy-identity")
    copy_parser.add_argument("image", type=Path)
    copy_parser.add_argument("destination", type=Path)
    copy_parser.add_argument("--output", type=Path)

    extract_parser = actions.add_parser("extract-pcm")
    extract_parser.add_argument("image", type=Path)
    extract_parser.add_argument("destination", type=Path)
    extract_parser.add_argument("--output", type=Path)

    candidates_parser = actions.add_parser("candidate-boundaries")
    candidates_parser.add_argument("image", type=Path)
    candidates_parser.add_argument("--output", type=Path)

    wavs_parser = actions.add_parser("export-candidate-wavs")
    wavs_parser.add_argument("image", type=Path)
    wavs_parser.add_argument("destination", type=Path)
    wavs_parser.add_argument("--output", type=Path)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.action == "inspect":
        emit(asdict(SoundBank.read(args.image)), args.output)
    elif args.action == "copy-identity":
        emit(copy_identity(args.image, args.destination), args.output)
    elif args.action == "extract-pcm":
        emit(extract_pcm(args.image, args.destination), args.output)
    elif args.action == "candidate-boundaries":
        emit(candidate_boundaries(args.image), args.output)
    else:
        emit(export_candidate_wavs(args.image, args.destination), args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
