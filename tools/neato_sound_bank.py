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
import math
from pathlib import Path
import shutil
import struct
from typing import Iterable, Mapping
import wave


PAGE_SIZE = 512
PAGE_MAGIC = b"KT"
DEFAULT_AUDIO_START_PAGE = 8
PCM_SAMPLE_RATE = 22_050
PCM_BYTES_PER_SAMPLE = 2
RECORD_HEADER_SIZE = 16
RECORD_FLAGS = b"\x01\x01"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def page_align(length: int) -> int:
    return ((length + PAGE_SIZE - 1) // PAGE_SIZE) * PAGE_SIZE


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


def record_ranges(source: Path) -> list[dict[str, object]]:
    """Return parsed record boundaries and metadata for every live slot."""
    data = source.read_bytes()
    bank = SoundBank.read(source)
    slots = slot_page_table(source)
    records: list[dict[str, object]] = []
    for (sound_id, start_page), (_, end_page) in zip(
        slots, slots[1:] + [(-1, bank.page_count)]
    ):
        record_offset = start_page * PAGE_SIZE
        record_capacity = (end_page - start_page) * PAGE_SIZE
        header = data[record_offset:record_offset + RECORD_HEADER_SIZE]
        if len(header) != RECORD_HEADER_SIZE:
            raise ValueError(f"slot {sound_id}: truncated record header")
        flags = header[:2]
        sample_rate = struct.unpack_from("<H", header, 2)[0]
        sample_count = struct.unpack_from("<I", header, 4)[0]
        pcm_byte_count = struct.unpack_from("<I", header, 8)[0]
        reserved = struct.unpack_from("<I", header, 12)[0]
        pcm_offset = record_offset + RECORD_HEADER_SIZE
        padding_offset = pcm_offset + pcm_byte_count
        if flags != RECORD_FLAGS:
            raise ValueError(f"slot {sound_id}: unsupported record flags {flags.hex()}")
        if sample_rate != PCM_SAMPLE_RATE:
            raise ValueError(f"slot {sound_id}: unsupported sample rate {sample_rate}")
        if pcm_byte_count != sample_count * PCM_BYTES_PER_SAMPLE:
            raise ValueError(f"slot {sound_id}: PCM byte count does not match samples")
        if reserved != 0:
            raise ValueError(f"slot {sound_id}: reserved record field is nonzero")
        if pcm_byte_count % PCM_BYTES_PER_SAMPLE:
            raise ValueError(f"slot {sound_id}: PCM byte count is not sample-aligned")
        if RECORD_HEADER_SIZE + pcm_byte_count > record_capacity:
            raise ValueError(f"slot {sound_id}: PCM exceeds record capacity")
        padding = data[padding_offset:record_offset + record_capacity]
        if any(padding):
            raise ValueError(f"slot {sound_id}: record padding is not zero")
        pcm = data[pcm_offset:padding_offset]
        records.append({
            "sound_id": sound_id,
            "start_page": start_page,
            "end_page_exclusive": end_page,
            "record_offset": record_offset,
            "record_capacity_bytes": record_capacity,
            "pcm_offset": pcm_offset,
            "sample_rate": sample_rate,
            "sample_count": sample_count,
            "pcm_byte_count": pcm_byte_count,
            "duration_seconds": round(sample_count / PCM_SAMPLE_RATE, 6),
            "zero_padding_bytes": record_capacity - RECORD_HEADER_SIZE - pcm_byte_count,
            "pcm_sha256": sha256(pcm),
        })
    return records


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


def export_record_wavs(source: Path, destination: Path) -> dict[str, object]:
    data = source.read_bytes()
    destination.mkdir(parents=True, exist_ok=True)
    files: list[dict[str, object]] = []
    for record in record_ranges(source):
        start = int(record["pcm_offset"])
        end = start + int(record["pcm_byte_count"])
        output = destination / f"slot-{int(record['sound_id']):02d}.wav"
        with wave.open(str(output), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(PCM_BYTES_PER_SAMPLE)
            wav.setframerate(PCM_SAMPLE_RATE)
            wav.writeframes(data[start:end])
        files.append({**record, "path": str(output), "wav_sha256": sha256(output.read_bytes())})
    return {"source": str(source), "record_wavs": files}


def read_exact_pcm_wav(path: Path) -> bytes:
    with wave.open(str(path), "rb") as wav:
        params = (
            wav.getnchannels(),
            wav.getsampwidth(),
            wav.getframerate(),
            wav.getcomptype(),
        )
        if params != (1, PCM_BYTES_PER_SAMPLE, PCM_SAMPLE_RATE, "NONE"):
            raise ValueError(
                f"{path}: expected mono signed 16-bit PCM WAV at 22050 Hz, got "
                f"channels={params[0]} width={params[1]} rate={params[2]} compression={params[3]}"
            )
        frames = wav.readframes(wav.getnframes())
    if len(frames) % PCM_BYTES_PER_SAMPLE:
        raise ValueError(f"{path}: PCM payload is not 16-bit sample aligned")
    return frames


def load_replacement_manifest(path: Path) -> dict[int, Path]:
    manifest = json.loads(path.read_text())
    replacements = manifest.get("replacements", manifest)
    if not isinstance(replacements, dict):
        raise ValueError(f"{path}: expected object with a replacements map")
    loaded: dict[int, Path] = {}
    for key, value in replacements.items():
        sound_id = int(key)
        clip = Path(value)
        if not clip.is_absolute():
            clip = path.parent / clip
        loaded[sound_id] = clip
    return loaded


def build_from_wavs(source: Path, replacements: Mapping[int, Path],
                    destination: Path) -> dict[str, object]:
    """Build an edited bank while preserving the directory and page starts."""
    original = source.read_bytes()
    bank = SoundBank.read(source)
    output = bytearray(original)
    records = record_ranges(source)
    record_by_id = {int(record["sound_id"]): record for record in records}
    unknown = sorted(set(replacements) - set(record_by_id))
    if unknown:
        raise ValueError(f"replacement requested for absent sound IDs: {unknown}")

    changes: list[dict[str, object]] = []
    for sound_id, clip in sorted(replacements.items()):
        record = record_by_id[sound_id]
        pcm = read_exact_pcm_wav(clip)
        capacity = int(record["record_capacity_bytes"]) - RECORD_HEADER_SIZE
        if len(pcm) > capacity:
            raise ValueError(
                f"{clip}: {len(pcm)} PCM bytes exceeds slot {sound_id} capacity {capacity}"
            )
        if len(pcm) % PCM_BYTES_PER_SAMPLE:
            raise ValueError(f"{clip}: replacement PCM is not sample-aligned")

        record_offset = int(record["record_offset"])
        record_capacity = int(record["record_capacity_bytes"])
        sample_count = len(pcm) // PCM_BYTES_PER_SAMPLE
        header = (
            RECORD_FLAGS
            + struct.pack("<H", PCM_SAMPLE_RATE)
            + struct.pack("<I", sample_count)
            + struct.pack("<I", len(pcm))
            + struct.pack("<I", 0)
        )
        replacement_record = header + pcm + bytes(record_capacity - len(header) - len(pcm))
        output[record_offset:record_offset + record_capacity] = replacement_record
        changes.append({
            "sound_id": sound_id,
            "source_wav": str(clip),
            "sample_count": sample_count,
            "pcm_byte_count": len(pcm),
            "duration_seconds": round(sample_count / PCM_SAMPLE_RATE, 6),
            "pcm_sha256": sha256(pcm),
            "record_capacity_bytes": record_capacity,
            "zero_padding_bytes": record_capacity - RECORD_HEADER_SIZE - len(pcm),
        })

    if len(output) != len(original):
        raise ValueError("internal error: built image size changed")
    if len(output) != bank.file_size:
        raise ValueError("internal error: built image differs from source size")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(output)
    built = bytes(output)
    return {
        "source": str(source),
        "destination": str(destination),
        "file_size": len(built),
        "page_count": len(built) // PAGE_SIZE,
        "sha256": sha256(built),
        "transport_additive_checksum_hex": f"0x{(sum(built) & 0xFFFFFFFF):08x}",
        "replaced_sound_ids": [change["sound_id"] for change in changes],
        "changes": changes,
        "preserved_sound_ids": [
            int(record["sound_id"]) for record in records
            if int(record["sound_id"]) not in replacements
        ],
    }


def build_compact_from_wavs(source: Path, replacements: Mapping[int, Path],
                            destination: Path) -> dict[str, object]:
    """Build an edited bank with compact 512-aligned records and updated table.

    This is intended for the XV sound-library scanner behavior observed after
    the fixed-page build: do not leave full zero pages between records.
    """
    original = source.read_bytes()
    bank = SoundBank.read(source)
    records = record_ranges(source)
    live_sound_ids = [int(record["sound_id"]) for record in records]
    missing = sorted(set(live_sound_ids) - set(replacements))
    unknown = sorted(set(replacements) - set(live_sound_ids))
    if missing:
        raise ValueError(f"compact build requires replacements for all live sound IDs: {missing}")
    if unknown:
        raise ValueError(f"replacement requested for absent sound IDs: {unknown}")

    directory = bytearray(original[:DEFAULT_AUDIO_START_PAGE * PAGE_SIZE])
    # Reset the page0 table while preserving all other directory metadata.
    entry_count = bank.declared_value + 1
    if 8 + entry_count * 2 > PAGE_SIZE:
        raise ValueError("declared sound-table length does not fit in header page")
    for offset in range(8, 8 + entry_count * 2):
        directory[offset] = 0

    output = bytearray(len(original))
    output[:len(directory)] = directory
    current_page = DEFAULT_AUDIO_START_PAGE
    changes: list[dict[str, object]] = []
    for sound_id in live_sound_ids:
        pcm = read_exact_pcm_wav(replacements[sound_id])
        if len(pcm) % PCM_BYTES_PER_SAMPLE:
            raise ValueError(f"{replacements[sound_id]}: replacement PCM is not sample-aligned")
        record_bytes_used = RECORD_HEADER_SIZE + len(pcm)
        record_capacity = page_align(record_bytes_used)
        record_page_count = record_capacity // PAGE_SIZE
        record_offset = current_page * PAGE_SIZE
        next_page = current_page + record_page_count
        if next_page > bank.page_count:
            raise ValueError(
                f"compact build exceeds bank capacity at slot {sound_id}: "
                f"next page {next_page} > page count {bank.page_count}"
            )
        sample_count = len(pcm) // PCM_BYTES_PER_SAMPLE
        header = (
            RECORD_FLAGS
            + struct.pack("<H", PCM_SAMPLE_RATE)
            + struct.pack("<I", sample_count)
            + struct.pack("<I", len(pcm))
            + struct.pack("<I", 0)
        )
        struct.pack_into("<H", output, 8 + sound_id * 2, current_page)
        output[record_offset:record_offset + len(header)] = header
        output[record_offset + len(header):record_offset + len(header) + len(pcm)] = pcm
        changes.append({
            "sound_id": sound_id,
            "source_wav": str(replacements[sound_id]),
            "start_page": current_page,
            "end_page_exclusive": next_page,
            "sample_count": sample_count,
            "pcm_byte_count": len(pcm),
            "duration_seconds": round(sample_count / PCM_SAMPLE_RATE, 6),
            "pcm_sha256": sha256(pcm),
            "record_capacity_bytes": record_capacity,
            "zero_padding_bytes": record_capacity - record_bytes_used,
        })
        current_page = next_page

    built = bytes(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(built)
    return {
        "source": str(source),
        "destination": str(destination),
        "layout": "compact",
        "file_size": len(built),
        "page_count": len(built) // PAGE_SIZE,
        "first_trailing_zero_page": current_page,
        "sha256": sha256(built),
        "transport_additive_checksum_hex": f"0x{(sum(built) & 0xFFFFFFFF):08x}",
        "replaced_sound_ids": live_sound_ids,
        "changes": changes,
    }


def build_pcm_only_from_wavs(source: Path, replacements: Mapping[int, Path],
                             destination: Path) -> dict[str, object]:
    """Replace only original PCM regions; preserve directory, headers, padding.

    The replacement clip is written at the start of the original PCM field and
    zero-padded to the original pcm_byte_count.  No metadata is changed.
    """
    original = source.read_bytes()
    output = bytearray(original)
    records = record_ranges(source)
    record_by_id = {int(record["sound_id"]): record for record in records}
    missing = sorted(set(record_by_id) - set(replacements))
    unknown = sorted(set(replacements) - set(record_by_id))
    if missing:
        raise ValueError(f"PCM-only build requires replacements for all live sound IDs: {missing}")
    if unknown:
        raise ValueError(f"replacement requested for absent sound IDs: {unknown}")

    changes: list[dict[str, object]] = []
    for sound_id in sorted(record_by_id):
        record = record_by_id[sound_id]
        clip = replacements[sound_id]
        pcm = read_exact_pcm_wav(clip)
        target_len = int(record["pcm_byte_count"])
        if len(pcm) > target_len:
            raise ValueError(
                f"{clip}: {len(pcm)} PCM bytes exceeds original slot {sound_id} "
                f"PCM length {target_len}"
            )
        pcm_offset = int(record["pcm_offset"])
        output[pcm_offset:pcm_offset + target_len] = pcm + bytes(target_len - len(pcm))
        changes.append({
            "sound_id": sound_id,
            "source_wav": str(clip),
            "original_sample_count": int(record["sample_count"]),
            "original_pcm_byte_count": target_len,
            "source_pcm_byte_count": len(pcm),
            "source_duration_seconds": round((len(pcm) // PCM_BYTES_PER_SAMPLE) / PCM_SAMPLE_RATE, 6),
            "silence_padding_bytes": target_len - len(pcm),
            "written_pcm_sha256": sha256(pcm + bytes(target_len - len(pcm))),
            "leading_content_sha256": sha256(pcm),
        })

    built = bytes(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(built)
    return {
        "source": str(source),
        "destination": str(destination),
        "layout": "pcm-only-preserve-directory-headers-padding",
        "file_size": len(built),
        "page_count": len(built) // PAGE_SIZE,
        "sha256": sha256(built),
        "transport_additive_checksum_hex": f"0x{(sum(built) & 0xFFFFFFFF):08x}",
        "replaced_sound_ids": [change["sound_id"] for change in changes],
        "changes": changes,
    }


def blank_pages_between_records(source: Path) -> list[dict[str, object]]:
    """Report fully zero padding pages after PCM before the next record."""
    data = source.read_bytes()
    records = record_ranges(source)
    blanks: list[dict[str, object]] = []
    for record in records[:-1]:
        sound_id = int(record["sound_id"])
        padding_start = int(record["pcm_offset"]) + int(record["pcm_byte_count"])
        padding_end = int(record["end_page_exclusive"]) * PAGE_SIZE
        page = (padding_start + PAGE_SIZE - 1) // PAGE_SIZE
        while (page + 1) * PAGE_SIZE <= padding_end:
            page_data = data[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]
            if not any(page_data):
                blanks.append({
                    "after_sound_id": sound_id,
                    "blank_page": page,
                    "next_record_page": int(record["end_page_exclusive"]),
                })
            page += 1
    return blanks


def synthesize_bmo_wavs(destination: Path) -> dict[str, object]:
    """Create original BMO-inspired chiptune bleeps, not sampled show audio."""
    destination.mkdir(parents=True, exist_ok=True)
    specs = {
        0: {"name": "hello_prompt", "notes": (660, 880, 990, 1320), "duration": 0.72},
        1: {"name": "button_chirp", "notes": (1175, 1568), "duration": 0.28},
        2: {"name": "happy_confirm", "notes": (784, 988, 1175, 1568), "duration": 0.92},
        3: {"name": "concern_alert", "notes": (988, 740, 622), "duration": 0.76},
        19: {"name": "attention_ping", "notes": (1319, 1047, 1319), "duration": 0.46},
    }
    files: list[dict[str, object]] = []
    for sound_id, spec in specs.items():
        pcm = bytearray()
        notes = tuple(spec["notes"])
        total_samples = int(float(spec["duration"]) * PCM_SAMPLE_RATE)
        note_samples = max(1, total_samples // len(notes))
        for note_index, frequency in enumerate(notes):
            for sample_index in range(note_samples):
                t = sample_index / PCM_SAMPLE_RATE
                # Soft ADSR envelope keeps the clips click-free and toy-like.
                local = sample_index / note_samples
                attack = min(1.0, local / 0.08)
                release = min(1.0, (1.0 - local) / 0.18)
                envelope = max(0.0, min(attack, release))
                wobble = 0.018 * math.sin(2 * math.pi * 7.0 * t + note_index)
                phase = 2 * math.pi * frequency * (1.0 + wobble) * t
                # Square-ish but band-limited enough for the stock 22.05 kHz path.
                sample = (
                    0.70 * math.sin(phase)
                    + 0.20 * math.sin(3 * phase)
                    + 0.08 * math.sin(5 * phase)
                )
                value = int(max(-1.0, min(1.0, sample * envelope * 0.38)) * 32767)
                pcm.extend(struct.pack("<h", value))
            gap_samples = int(0.025 * PCM_SAMPLE_RATE)
            pcm.extend(b"\x00\x00" * gap_samples)
        output = destination / f"slot-{sound_id:02d}-{spec['name']}.wav"
        with wave.open(str(output), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(PCM_BYTES_PER_SAMPLE)
            wav.setframerate(PCM_SAMPLE_RATE)
            wav.writeframes(bytes(pcm))
        files.append({
            "sound_id": sound_id,
            "name": spec["name"],
            "path": str(output),
            "provenance": "locally synthesized original chiptune/BMO-inspired tone; no external samples",
            "duration_seconds": round((len(pcm) // PCM_BYTES_PER_SAMPLE) / PCM_SAMPLE_RATE, 6),
            "pcm_sha256": sha256(bytes(pcm)),
            "wav_sha256": sha256(output.read_bytes()),
        })
    return {"generated_wavs": files}


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

    records_parser = actions.add_parser("record-ranges")
    records_parser.add_argument("image", type=Path)
    records_parser.add_argument("--output", type=Path)

    record_wavs_parser = actions.add_parser("export-record-wavs")
    record_wavs_parser.add_argument("image", type=Path)
    record_wavs_parser.add_argument("destination", type=Path)
    record_wavs_parser.add_argument("--output", type=Path)

    build_parser_ = actions.add_parser("build-from-wavs")
    build_parser_.add_argument("image", type=Path)
    build_parser_.add_argument("manifest", type=Path)
    build_parser_.add_argument("destination", type=Path)
    build_parser_.add_argument("--output", type=Path)

    compact_parser = actions.add_parser("build-compact-from-wavs")
    compact_parser.add_argument("image", type=Path)
    compact_parser.add_argument("manifest", type=Path)
    compact_parser.add_argument("destination", type=Path)
    compact_parser.add_argument("--output", type=Path)

    pcm_only_parser = actions.add_parser("build-pcm-only-from-wavs")
    pcm_only_parser.add_argument("image", type=Path)
    pcm_only_parser.add_argument("manifest", type=Path)
    pcm_only_parser.add_argument("destination", type=Path)
    pcm_only_parser.add_argument("--output", type=Path)

    blanks_parser = actions.add_parser("blank-pages-between-records")
    blanks_parser.add_argument("image", type=Path)
    blanks_parser.add_argument("--output", type=Path)

    synth_parser = actions.add_parser("synthesize-bmo-wavs")
    synth_parser.add_argument("destination", type=Path)
    synth_parser.add_argument("--output", type=Path)
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
    elif args.action == "export-candidate-wavs":
        emit(export_candidate_wavs(args.image, args.destination), args.output)
    elif args.action == "record-ranges":
        emit(record_ranges(args.image), args.output)
    elif args.action == "export-record-wavs":
        emit(export_record_wavs(args.image, args.destination), args.output)
    elif args.action == "build-from-wavs":
        emit(
            build_from_wavs(
                args.image,
                load_replacement_manifest(args.manifest),
                args.destination,
            ),
            args.output,
        )
    elif args.action == "build-compact-from-wavs":
        emit(
            build_compact_from_wavs(
                args.image,
                load_replacement_manifest(args.manifest),
                args.destination,
            ),
            args.output,
        )
    elif args.action == "build-pcm-only-from-wavs":
        emit(
            build_pcm_only_from_wavs(
                args.image,
                load_replacement_manifest(args.manifest),
                args.destination,
            ),
            args.output,
        )
    elif args.action == "blank-pages-between-records":
        emit(blank_pages_between_records(args.image), args.output)
    else:
        emit(synthesize_bmo_wavs(args.destination), args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
