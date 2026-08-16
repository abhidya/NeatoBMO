#!/usr/bin/env python3
"""Build an offline, runtime-ready BMO sound library.

The importer reads 101soundboards' public ``board_data_inline`` metadata,
downloads each referenced MP3, normalizes it once to the Neato runtime format,
and emits a deterministic aligned pack plus a JSON catalog.  Existing reviewed
Myinstants WAVs can be included without re-downloading them.

This tool never writes robot or ESP32 flash.  Its output is ordinary offline
assets that firmware/web code can consume later.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import shutil
import struct
import subprocess
import urllib.request
import wave
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "assets" / "bmo-soundboard-library-20260810"
REVIEWED_ROOT = ROOT / "assets" / "bmo-sound-bank-offline-20260810"
PACK_MAGIC = b"BMOSND1\0"
PACK_ALIGNMENT = 4
SAMPLE_RATE = 22_050
OFFICIAL_PREFIX = "Payload/Beemo.app/www/"
EDITORIAL_PATH = ROOT / "docs" / "bmo-audio-editorial.json"


def load_editorial() -> dict:
    if not EDITORIAL_PATH.is_file():
        return {"trim_start_seconds": {}, "labels": {}}
    return json.loads(EDITORIAL_PATH.read_text())


EDITORIAL = load_editorial()
EDITORIAL_TRIM_START_SECONDS = EDITORIAL.get("trim_start_seconds", {})
EDITORIAL_LABELS = EDITORIAL.get("labels", {})


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def slug(value: str) -> str:
    value = value.lower().replace("’", "").replace("”", "").replace("“", "")
    value = re.sub(r"[^a-z0-9]+", "-", value).strip("-")
    return value or "sound"


def extract_board(html_path: Path) -> dict:
    text = html_path.read_text(encoding="utf-8")
    match = re.search(r"var board_data_inline = (\{.*?\});\s*</script>", text, re.S)
    if not match:
        raise ValueError("board_data_inline metadata was not found")
    return json.loads(html.unescape(match.group(1)))


def download(url: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": "NeatoBMO/1.0"})
    with urllib.request.urlopen(request, timeout=60) as response:
        data = response.read()
    if not data.startswith((b"ID3", b"\xff\xfb", b"\xff\xf3", b"\xff\xf2")):
        raise ValueError(f"download for {destination.name} is not an MP3")
    destination.write_bytes(data)


def normalize(source: Path, destination: Path, trim_start: float = 0.0) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    filters = []
    if trim_start:
        filters += [f"atrim=start={trim_start}", "asetpts=PTS-STARTPTS"]
    filters += ["highpass=f=70", "lowpass=f=10000",
                "alimiter=limit=0.891251"]
    command = [
        "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(source), "-map_metadata", "-1", "-ac", "1", "-ar", str(SAMPLE_RATE),
        "-c:a", "pcm_s16le", "-af", ",".join(filters),
        str(destination),
    ]
    subprocess.run(command, check=True)


def encode_runtime_mp3(source_wav: Path, destination: Path) -> None:
    """Create the small, uniform ESP32 payload (mono 22.05 kHz, 32 kbps)."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    command = [
        "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(source_wav), "-map_metadata", "-1", "-ac", "1",
        "-ar", str(SAMPLE_RATE), "-c:a", "libmp3lame", "-b:a", "32k",
        "-write_xing", "0", str(destination),
    ]
    subprocess.run(command, check=True)


def wav_metadata(path: Path) -> dict:
    with wave.open(str(path), "rb") as wav:
        channels = wav.getnchannels()
        width = wav.getsampwidth()
        rate = wav.getframerate()
        frames = wav.getnframes()
        pcm = wav.readframes(frames)
    if (channels, width, rate) != (1, 2, SAMPLE_RATE):
        raise ValueError(f"unexpected normalized WAV format for {path}")
    return {
        "bytes": path.stat().st_size,
        "sha256": sha256(path),
        "pcm_sha256": hashlib.sha256(pcm).hexdigest(),
        "frames": frames,
        "duration_seconds": round(frames / rate, 6),
        "sample_rate_hz": rate,
        "channels": channels,
        "bits_per_sample": width * 8,
    }


def reviewed_entries() -> list[dict]:
    inventory = json.loads((REVIEWED_ROOT / "source-inventory.json").read_text())
    previews = json.loads((REVIEWED_ROOT / "normalized-preview-manifest.json").read_text())
    slots_by_url: dict[str, list[dict]] = {}
    for slot in previews["slots"]:
        slots_by_url.setdefault(slot["source_url"], []).append(slot)
    entries = []
    for source in inventory["approved_sources"]:
        url = source["source_url"]
        for index, slot_info in enumerate(slots_by_url.get(url, []), start=1):
            source_wav = REVIEWED_ROOT / slot_info["normalized_wav"]
            label = slot_info["role"].replace(" approved excerpt", "").replace(" excerpt", "")
            entries.append({
                "key": f"reviewed-{slot_info['sound_id']:02d}-{slug(label)}",
                "label": label,
                "verification": "existing-manually-reviewed",
                "source_board": "Myinstants reviewed sources",
                "source_page_url": url,
                "source_audio_url": None,
                "source_id": f"myinstants-slot-{slot_info['sound_id']}",
                "source_local_path": source_wav,
                "reported_duration_ms": round(slot_info["normalized_duration_seconds"] * 1000),
            })
    return entries


def board_entries(board: dict) -> list[dict]:
    entries = []
    for sound in board["sounds"]:
        label = sound["sound_transcript"].strip()
        entries.append({
            "key": f"101-{sound['id']}-{slug(label)}",
            "label": label,
            "verification": "dedicated-bmo-board-metadata",
            "source_board": board["board_title"],
            "source_board_url": board["link"],
            "source_page_url": sound["link"],
            "source_audio_url": sound["sound_file_url"],
            "source_id": str(sound["id"]),
            "reported_duration_ms": sound["sound_duration"],
            "plays": sound.get("sound_hits"),
            "downloads": sound.get("sound_downloads"),
        })
    return entries


def official_member_allowed(member: str) -> bool:
    if not member.startswith(OFFICIAL_PREFIX) or not member.endswith(".mp3"):
        return False
    relative = member[len(OFFICIAL_PREFIX):]
    if relative.startswith("audio/ui/"):
        return False
    if relative.startswith(("audio/", "camera/audio/", "help/audio/",
                            "parade/audio/", "wallpaper/audio/", "alarms/audio/")):
        return True
    if relative.startswith("music/audio/") and relative.count("/") == 2:
        return True
    if relative.startswith("football/audio/Game_Select"):
        return True
    if relative.startswith(("kompy/audio/Game_Select",
                            "kompy/audio/General_Games")):
        return True
    if relative.startswith("ringtones/audio/Ringtones"):
        return True
    return relative.startswith("soundboard/audio/bmo/")


def official_entries(ipa_path: Path) -> list[dict]:
    entries = []
    with zipfile.ZipFile(ipa_path) as archive:
        for member in sorted(archive.namelist()):
            if not official_member_allowed(member):
                continue
            relative = member[len(OFFICIAL_PREFIX):]
            stem = Path(relative).stem
            category = str(Path(relative).parent).replace("/audio", "")
            label = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", stem)
            label = label.replace("_", " ").strip()
            entries.append({
                "key": f"official-{slug(category)}-{slug(stem)}",
                "label": label,
                "category": category,
                "verification": "official-cartoon-network-beemo-app",
                "source_board": "Beemo v2.2.5 iOS app",
                "source_page_url": "https://archive.org/details/cartoon-network-ios-archive",
                "source_audio_url": None,
                "source_id": member,
                "source_ipa_member": member,
                "reported_duration_ms": None,
            })
    return entries


def write_pack(output: Path, entries: list[dict], filename: str,
               payload_key: str, metadata_key: str) -> dict:
    pack_path = output / filename
    offsets_by_hash: dict[str, dict] = {}
    with pack_path.open("wb") as pack:
        pack.write(PACK_MAGIC)
        pack.write(struct.pack("<II", len(entries), PACK_ALIGNMENT))
        for entry in entries:
            payload = output / entry[payload_key]
            payload_hash = sha256(payload)
            existing = offsets_by_hash.get(payload_hash)
            if existing is not None:
                entry[metadata_key] = {**existing, "shared": True}
                continue
            padding = (-pack.tell()) % PACK_ALIGNMENT
            if padding:
                pack.write(bytes(padding))
            location = {"offset": pack.tell(), "length": payload.stat().st_size,
                        "sha256": payload_hash}
            entry[metadata_key] = location
            offsets_by_hash[payload_hash] = location
            pack.write(payload.read_bytes())
    return {
        "path": pack_path.name,
        "magic": PACK_MAGIC.decode("ascii", errors="replace"),
        "alignment": PACK_ALIGNMENT,
        "bytes": pack_path.stat().st_size,
        "sha256": sha256(pack_path),
        "unique_payloads": len(offsets_by_hash),
    }


def build(output: Path, board_htmls: list[Path], beemo_ipa: Path | None) -> dict:
    source_dir = output / "source-mp3"
    wav_dir = output / "neato-wav"
    runtime_dir = output / "esp32-mp3"
    output.mkdir(parents=True, exist_ok=True)
    boards = [extract_board(path) for path in board_htmls]
    entries = []
    seen_source_ids = set()
    for board in boards:
        for entry in board_entries(board):
            source_key = (entry["source_board"], entry["source_id"])
            if source_key not in seen_source_ids:
                entries.append(entry)
                seen_source_ids.add(source_key)
    if beemo_ipa is not None:
        entries.extend(official_entries(beemo_ipa))
    entries.extend(reviewed_entries())

    for entry in entries:
        entry["label"] = EDITORIAL_LABELS.get(entry["key"], entry["label"])

    ipa = zipfile.ZipFile(beemo_ipa) if beemo_ipa is not None else None
    try:
        for entry in entries:
            wav_path = wav_dir / f"{entry['key']}.wav"
            if "source_local_path" in entry:
                if not wav_path.exists():
                    shutil.copyfile(entry.pop("source_local_path"), wav_path)
                else:
                    entry.pop("source_local_path")
                entry["source_mp3"] = None
            else:
                mp3_path = source_dir / f"{entry['key']}.mp3"
                if not mp3_path.exists():
                    member = entry.get("source_ipa_member")
                    if member:
                        mp3_path.parent.mkdir(parents=True, exist_ok=True)
                        mp3_path.write_bytes(ipa.read(member))
                    else:
                        download(entry["source_audio_url"], mp3_path)
                entry["source_mp3"] = str(mp3_path.relative_to(output))
                entry["source_mp3_bytes"] = mp3_path.stat().st_size
                entry["source_mp3_sha256"] = sha256(mp3_path)
                if not wav_path.exists():
                    normalize(mp3_path, wav_path,
                              EDITORIAL_TRIM_START_SECONDS.get(entry["key"], 0))
            trim_start = EDITORIAL_TRIM_START_SECONDS.get(entry["key"])
            if trim_start is not None:
                entry["editorial_trim_start_seconds"] = trim_start
            entry["wav"] = str(wav_path.relative_to(output))
            entry["audio"] = wav_metadata(wav_path)
            runtime_mp3 = runtime_dir / f"{entry['key']}.mp3"
            if not runtime_mp3.exists():
                encode_runtime_mp3(wav_path, runtime_mp3)
            entry["esp32_mp3"] = str(runtime_mp3.relative_to(output))
            entry["esp32_mp3_bytes"] = runtime_mp3.stat().st_size
            entry["esp32_mp3_sha256"] = sha256(runtime_mp3)
    finally:
        if ipa is not None:
            ipa.close()

    canonical_by_pcm: dict[str, str] = {}
    for entry in entries:
        pcm_hash = entry["audio"]["pcm_sha256"]
        if pcm_hash in canonical_by_pcm:
            entry["duplicate_of"] = canonical_by_pcm[pcm_hash]
        else:
            canonical_by_pcm[pcm_hash] = entry["key"]

    wav_pack = write_pack(output, entries, "bmo-sounds.wav.pack", "wav", "wav_pack")
    esp32_pack = write_pack(output, entries, "bmo-sounds.mp3.pack",
                            "esp32_mp3", "esp32_pack")

    catalog = {
        "schema": 1,
        "format": "RIFF/WAVE mono signed 16-bit PCM at 22050 Hz",
        "packs": {"web_wav": wav_pack, "esp32_mp3": esp32_pack},
        "source_boards": [
            {"name": board["board_title"], "url": board["link"],
             "declared_sound_count": board["sounds_count"]}
            for board in boards
        ] + [{"name": "Myinstants reviewed sources",
              "url": "https://www.myinstants.com/en/search/?name=bmo"}],
        "sound_count": len(entries),
        "unique_pcm_count": len(canonical_by_pcm),
        "sounds": entries,
    }
    catalog_path = output / "catalog.json"
    catalog_path.write_text(json.dumps(catalog, indent=2, ensure_ascii=False) + "\n")
    return catalog


def refresh_existing(output: Path) -> dict:
    """Reapply editorial trims and rebuild metadata/packs without the web inputs."""
    catalog_path = output / "catalog.json"
    catalog = json.loads(catalog_path.read_text())
    entries = catalog["sounds"]
    for entry in entries:
        entry["label"] = EDITORIAL_LABELS.get(entry["key"], entry["label"])
        trim_start = EDITORIAL_TRIM_START_SECONDS.get(entry["key"])
        wav_path = output / entry["wav"]
        if trim_start is not None:
            source = output / entry["source_mp3"]
            normalize(source, wav_path, trim_start)
            entry["editorial_trim_start_seconds"] = trim_start
            runtime_mp3 = output / entry["esp32_mp3"]
            encode_runtime_mp3(wav_path, runtime_mp3)
        entry["audio"] = wav_metadata(wav_path)
        runtime_mp3 = output / entry["esp32_mp3"]
        entry["esp32_mp3_bytes"] = runtime_mp3.stat().st_size
        entry["esp32_mp3_sha256"] = sha256(runtime_mp3)
        entry.pop("duplicate_of", None)

    canonical_by_pcm = {}
    for entry in entries:
        pcm_hash = entry["audio"]["pcm_sha256"]
        if pcm_hash in canonical_by_pcm:
            entry["duplicate_of"] = canonical_by_pcm[pcm_hash]
        else:
            canonical_by_pcm[pcm_hash] = entry["key"]
    catalog["unique_pcm_count"] = len(canonical_by_pcm)
    catalog["packs"] = {
        "web_wav": write_pack(output, entries, "bmo-sounds.wav.pack",
                              "wav", "wav_pack"),
        "esp32_mp3": write_pack(output, entries, "bmo-sounds.mp3.pack",
                                "esp32_mp3", "esp32_pack"),
    }
    catalog_path.write_text(json.dumps(catalog, indent=2,
                                       ensure_ascii=False) + "\n")
    return catalog


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--board-html", type=Path, action="append")
    parser.add_argument("--beemo-ipa", type=Path)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--refresh-existing", action="store_true")
    args = parser.parse_args()
    if args.refresh_existing:
        catalog = refresh_existing(args.output.resolve())
    else:
        if not args.board_html:
            parser.error("--board-html is required unless --refresh-existing is used")
        catalog = build(args.output.resolve(),
                        [path.resolve() for path in args.board_html],
                        args.beemo_ipa.resolve() if args.beemo_ipa else None)
    print(json.dumps({
        "output": str(args.output.resolve()),
        "sounds": catalog["sound_count"],
        "unique_pcm": catalog["unique_pcm_count"],
        "web_pack_bytes": catalog["packs"]["web_wav"]["bytes"],
        "esp32_pack_bytes": catalog["packs"]["esp32_mp3"]["bytes"],
        "esp32_pack_sha256": catalog["packs"]["esp32_mp3"]["sha256"],
    }, indent=2))


if __name__ == "__main__":
    main()
