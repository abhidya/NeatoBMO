#!/usr/bin/env python3
"""Pack the normalized BMO catalog into persistent Neato flash modules.

Each output page is an exact 770,048-byte PCM-only overlay of the proven BMO
baseline.  Clips are assigned to the ten live sound slots with a best-fit
subset search; clips longer than one slot are split across multiple slots and
the catalog records their paced playback sequence.  Duplicate PCM entries are
aliases and do not consume another slot or module.
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import shutil
import wave
from array import array
from dataclasses import dataclass, field
from pathlib import Path

from neatobmo import tts_bank


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LIBRARY = ROOT / "assets" / "bmo-soundboard-library-20260810"
DEFAULT_PUBLISH = ROOT / "docs" / "bmo-soundboard"
DEFAULT_BASELINE = (
    ROOT / "assets" / "bmo-sound-bank-offline-20260810"
    / "DfltSoundLib.BMO.pcm-only.Rev1.0.bin"
)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def wav_samples(path: Path) -> array:
    with wave.open(str(path), "rb") as wav:
        fmt = (wav.getnchannels(), wav.getsampwidth(), wav.getframerate())
        if fmt != (1, 2, tts_bank.PCM_SAMPLE_RATE):
            raise ValueError(f"{path}: expected mono s16le 22050 Hz, got {fmt}")
        samples = array("h")
        samples.frombytes(wav.readframes(wav.getnframes()))
    # The proven bank validator rejects full-scale samples.  Clamp the rare
    # decoder edge value once at build time; this is inaudible and prevents a
    # source encoder's -32768 rounding from invalidating an otherwise safe bank.
    for index, sample in enumerate(samples):
        if sample <= -32767:
            samples[index] = -32766
        elif sample >= 32767:
            samples[index] = 32766
    return samples


@dataclass
class Clip:
    key: str
    label: str
    samples: array
    pcm_sha256: str
    source: dict


@dataclass
class Placement:
    clip: Clip
    slot_ids: tuple[int, ...]
    segments: list[tts_bank.SlotSegment]


@dataclass
class Page:
    index: int
    free: dict[int, int]
    placements: list[Placement] = field(default_factory=list)


def best_slots(free: dict[int, int], sample_count: int) -> tuple[int, ...] | None:
    """Return a deterministic smallest/waste-minimizing subset of free slots."""
    items = sorted(free.items())
    best = None
    for count in range(1, len(items) + 1):
        for combo in itertools.combinations(items, count):
            capacity = sum(samples for _, samples in combo)
            if capacity < sample_count:
                continue
            # Prefer fewer slots, then less silence, then stable numeric IDs.
            rank = (count, capacity - sample_count,
                    tuple(sound_id for sound_id, _ in combo))
            if best is None or rank < best[0]:
                best = (rank, tuple(sound_id for sound_id, _ in combo))
        if best is not None:
            break
    return None if best is None else best[1]


def place_clip(page: Page, clip: Clip) -> bool:
    slot_ids = best_slots(page.free, len(clip.samples))
    if slot_ids is None:
        return False
    # Largest capacities first gives the boundary finder the widest clean-cut
    # window while the subset selection already minimizes total wasted space.
    slot_ids = tuple(sorted(slot_ids, key=lambda sid: (-page.free[sid], sid)))
    capacities = [(sound_id, page.free[sound_id]) for sound_id in slot_ids]
    segments = split_exact(clip.samples, capacities)
    for sound_id in slot_ids:
        del page.free[sound_id]
    page.placements.append(Placement(clip, slot_ids, segments))
    return True


def split_exact(samples: array,
                capacities: list[tuple[int, int]]) -> list[tts_bank.SlotSegment]:
    """Fill selected slots without dropping samples at quiet-cut boundaries."""
    segments = []
    position = 0
    fade_samples = max(1, int(tts_bank.FADE_SECONDS * tts_bank.PCM_SAMPLE_RATE))
    for index, (sound_id, capacity) in enumerate(capacities):
        if position >= len(samples):
            break
        end = min(len(samples), position + capacity)
        chunk = list(samples[position:end])
        faded_in = index > 0
        faded_out = end < len(samples)
        if faded_in:
            for i in range(min(fade_samples, len(chunk))):
                chunk[i] = int(chunk[i] * i / fade_samples)
        if faded_out:
            for i in range(min(fade_samples, len(chunk))):
                at = len(chunk) - 1 - i
                chunk[at] = int(chunk[at] * i / fade_samples)
        segments.append(tts_bank.SlotSegment(
            index=index, sound_id=sound_id, capacity_samples=capacity,
            pcm=array("h", chunk).tobytes(),
            faded_in=faded_in, faded_out=faded_out))
        position = end
    if position != len(samples):
        raise ValueError("selected slot capacities did not contain the clip")
    return segments


def pack_clips(clips: list[Clip], capacities: dict[int, int]) -> list[Page]:
    pages: list[Page] = []
    for clip in sorted(clips, key=lambda item: (-len(item.samples), item.key)):
        for page in pages:
            if place_clip(page, clip):
                break
        else:
            page = Page(len(pages), dict(capacities))
            if not place_clip(page, clip):
                maximum = sum(capacities.values()) / tts_bank.PCM_SAMPLE_RATE
                seconds = len(clip.samples) / tts_bank.PCM_SAMPLE_RATE
                raise ValueError(
                    f"{clip.key} is {seconds:.3f}s; module capacity is {maximum:.3f}s")
            pages.append(page)
    return pages


def build(library: Path, baseline_path: Path, publish: Path) -> dict:
    source_catalog = json.loads((library / "catalog.json").read_text())
    baseline = baseline_path.read_bytes()
    if digest(baseline) != tts_bank.BMO_BANK_SHA256:
        raise ValueError("baseline BMO bank hash mismatch")
    records = {record.sound_id: record
               for record in tts_bank.record_ranges_from_bytes(baseline)}
    capacities = {sound_id: records[sound_id].sample_count
                  for sound_id in tts_bank.SLOT_SEQUENCE}

    canonical_by_pcm: dict[str, Clip] = {}
    aliases: dict[str, list[dict]] = {}
    for sound in source_catalog["sounds"]:
        samples = wav_samples(library / sound["wav"])
        pcm_hash = hashlib.sha256(samples.tobytes()).hexdigest()
        aliases.setdefault(pcm_hash, []).append(sound)
        if pcm_hash not in canonical_by_pcm:
            canonical_by_pcm[pcm_hash] = Clip(
                key=sound["key"], label=sound["label"],
                samples=samples,
                pcm_sha256=pcm_hash, source=sound)

    pages = pack_clips(list(canonical_by_pcm.values()), capacities)
    modules_dir = publish / "modules"
    if modules_dir.exists():
        shutil.rmtree(modules_dir)
    modules_dir.mkdir(parents=True)
    sound_lookup: dict[str, dict] = {}
    page_catalog = []

    for page in pages:
        all_segments = [segment for placement in page.placements
                        for segment in placement.segments]
        built, manifest = tts_bank.build_tts_bank(
            baseline, all_segments, unused_slots="silence")
        validation = tts_bank.validate_tts_bank(
            baseline, built, all_segments, unused_slots="silence")
        if not validation["ok"]:
            failed = [entry["check"] for entry in validation["checks"]
                      if not entry["ok"]]
            raise ValueError(f"page {page.index} failed validation: {failed}")
        filename = f"bmo-page-{page.index:03d}-{validation['sha256'][:12]}.bin"
        module_path = modules_dir / filename
        module_path.write_bytes(built)
        page_sounds = []
        for placement in page.placements:
            mapping = {
                "page": page.index,
                "module": f"modules/{filename}",
                "module_sha256": validation["sha256"],
                "slots": [segment.sound_id for segment in placement.segments],
                "commands": [f"PlaySound {segment.sound_id}"
                             for segment in placement.segments],
                "slot_seconds": [segment.slot_seconds for segment in placement.segments],
                "content_seconds": round(
                    len(placement.clip.samples) / tts_bank.PCM_SAMPLE_RATE, 6),
                "segments": [{
                    "slot": segment.sound_id,
                    "pcm_offset": records[segment.sound_id].pcm_offset,
                    "content_bytes": len(segment.pcm),
                    "slot_bytes": records[segment.sound_id].pcm_byte_count,
                    "content_seconds": segment.content_seconds,
                    "slot_seconds": segment.slot_seconds,
                } for segment in placement.segments],
            }
            for alias in aliases[placement.clip.pcm_sha256]:
                sound_lookup[alias["key"]] = {**mapping,
                    "key": alias["key"], "label": alias["label"],
                    "verification": alias["verification"],
                    "category": alias.get("category"),
                    "source_page_url": alias.get("source_page_url"),
                    "canonical_key": placement.clip.key,
                }
            page_sounds.append({
                "canonical_key": placement.clip.key,
                "label": placement.clip.label,
                "slots": mapping["slots"],
                "content_seconds": mapping["content_seconds"],
                "alias_count": len(aliases[placement.clip.pcm_sha256]),
            })
        page_catalog.append({
            "index": page.index,
            "file": f"modules/{filename}",
            "bytes": len(built),
            "sha256": validation["sha256"],
            "transport_additive_checksum_hex": manifest[
                "transport_additive_checksum_hex"],
            "sounds": page_sounds,
        })

    catalog = {
        "schema": 1,
        "format": "Neato XV sound library, PCM-only preserved-layout modules",
        "baseline_sha256": tts_bank.BMO_BANK_SHA256,
        "module_bytes": tts_bank.EXPECTED_BANK_BYTES,
        "module_count": len(page_catalog),
        "sound_count": len(sound_lookup),
        "unique_sound_count": len(canonical_by_pcm),
        "live_slots": list(tts_bank.SLOT_SEQUENCE),
        "pages": page_catalog,
        "sounds": sorted(sound_lookup.values(), key=lambda item: item["key"]),
    }
    publish.mkdir(parents=True, exist_ok=True)
    catalog_path = publish / "catalog.json"
    catalog_path.write_text(json.dumps(catalog, indent=2, ensure_ascii=False) + "\n")
    return catalog


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--library", type=Path, default=DEFAULT_LIBRARY)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--publish", type=Path, default=DEFAULT_PUBLISH)
    args = parser.parse_args()
    catalog = build(args.library.resolve(), args.baseline.resolve(),
                    args.publish.resolve())
    total = catalog["module_count"] * catalog["module_bytes"]
    print(json.dumps({
        "modules": catalog["module_count"],
        "sounds": catalog["sound_count"],
        "unique_sounds": catalog["unique_sound_count"],
        "total_module_bytes": total,
        "catalog": str((args.publish / "catalog.json").resolve()),
    }, indent=2))


if __name__ == "__main__":
    main()
