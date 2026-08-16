#!/usr/bin/env python3
"""Replace selected audio inside existing Neato modules without repacking pages."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from neatobmo import tts_bank  # noqa: E402
from tools.build_bmo_flash_library import split_exact, wav_samples  # noqa: E402


DEFAULT_LIBRARY = ROOT / "assets" / "bmo-soundboard-library-20260810"
DEFAULT_PUBLISH = ROOT / "docs" / "bmo-soundboard"


def patch_clips(library: Path, publish: Path, keys: list[str]) -> dict:
    catalog_path = publish / "catalog.json"
    catalog = json.loads(catalog_path.read_text())
    source_catalog = json.loads((library / "catalog.json").read_text())
    sounds = {sound["key"]: sound for sound in catalog["sounds"]}
    sources = {sound["key"]: sound for sound in source_catalog["sounds"]}
    pages = {page["index"]: page for page in catalog["pages"]}
    keys_by_page = {}
    for key in keys:
        keys_by_page.setdefault(sounds[key]["page"], []).append(key)

    changed = []
    for page_index, page_keys in sorted(keys_by_page.items()):
        page = pages[page_index]
        old_path = publish / page["file"]
        module = bytearray(old_path.read_bytes())
        for key in page_keys:
            sound = sounds[key]
            source = sources[key]
            samples = wav_samples(library / source["wav"])
            capacities = [(segment["slot"], segment["slot_bytes"] // 2)
                          for segment in sound["segments"]]
            if len(samples) > sum(size for _, size in capacities):
                raise ValueError(f"trimmed {key} no longer fits its original slots")
            replacement = split_exact(samples, capacities)

            for segment in sound["segments"]:
                start = segment["pcm_offset"]
                module[start:start + segment["slot_bytes"]] = bytes(
                    segment["slot_bytes"])
            replacement_meta = []
            original_by_slot = {segment["slot"]: segment
                                for segment in sound["segments"]}
            for segment in replacement:
                original = original_by_slot[segment.sound_id]
                start = original["pcm_offset"]
                module[start:start + len(segment.pcm)] = segment.pcm
                replacement_meta.append({
                    "slot": segment.sound_id,
                    "pcm_offset": start,
                    "content_bytes": len(segment.pcm),
                    "slot_bytes": original["slot_bytes"],
                    "content_seconds": segment.content_seconds,
                    "slot_seconds": original["slot_seconds"],
                })
            slots = [segment.sound_id for segment in replacement]
            seconds = round(len(samples) / tts_bank.PCM_SAMPLE_RATE, 6)
            mapping = {
                "slots": slots,
                "commands": [f"PlaySound {slot}" for slot in slots],
                "slot_seconds": [original_by_slot[slot]["slot_seconds"]
                                 for slot in slots],
                "content_seconds": seconds,
                "segments": replacement_meta,
            }
            for alias in catalog["sounds"]:
                if alias.get("canonical_key") == sound["canonical_key"]:
                    alias.update(mapping)
                    if "editorial_trim_start_seconds" in source:
                        alias["editorial_trim_start_seconds"] = source[
                            "editorial_trim_start_seconds"]
            page_sound = next(item for item in page["sounds"]
                              if item["canonical_key"] == sound["canonical_key"])
            page_sound["slots"] = slots
            page_sound["content_seconds"] = seconds

        payload = bytes(module)
        digest = hashlib.sha256(payload).hexdigest()
        filename = f"bmo-page-{page_index:03d}-{digest[:12]}.bin"
        new_path = old_path.with_name(filename)
        new_path.write_bytes(payload)
        if new_path != old_path:
            old_path.unlink()
        page["file"] = f"modules/{filename}"
        page["sha256"] = digest
        page["transport_additive_checksum_hex"] = (
            tts_bank.additive_checksum_hex(payload))
        for sound in catalog["sounds"]:
            if sound["page"] == page_index:
                sound["module"] = page["file"]
                sound["module_sha256"] = digest
        changed.append({"page": page_index, "file": page["file"],
                        "keys": page_keys})

    catalog_path.write_text(json.dumps(catalog, indent=2,
                                       ensure_ascii=False) + "\n")
    return {"changed_pages": changed}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("key", nargs="+")
    parser.add_argument("--library", type=Path, default=DEFAULT_LIBRARY)
    parser.add_argument("--publish", type=Path, default=DEFAULT_PUBLISH)
    args = parser.parse_args()
    result = patch_clips(args.library.resolve(), args.publish.resolve(), args.key)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
