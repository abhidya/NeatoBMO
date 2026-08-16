"""Clip-first voice backed by the large, verified BMO sound catalog.

The catalog stores many clips inside SHA-addressed Neato bank modules. This
adapter resolves only high-confidence transcript matches, extracts their PCM
segments without reflashing the bank, and returns an ordinary WAV suitable for
the ESP32 `/speak` runtime-audio path. Synthesis remains the fallback.
"""
import hashlib
import json
import re
from pathlib import Path

from . import tts_bank


ALIASES = {
    "hello": "bmo hello",
    "hi": "bmo hello",
    "hi there": "intro01 hello there",
    "hello there": "intro01 hello there",
    "well hello there": "intro01 hello there",
    "its bmo time": "intro06 its beemo time",
    "bmo time": "intro06 its beemo time",
    "bmo is bmo": "i am bmo",
    "yay": "yaay bmo",
    "yeah": "yaay bmo",
}


def normalize_phrase(text):
    text = text.lower().replace("’", "'").replace("beemo", "bmo")
    text = re.sub(r"[^a-z0-9']+", " ", text).replace("'", "")
    return re.sub(r"\s+", " ", text).strip()


def _spoken_label(label):
    """Remove catalog category/sequence prefixes from a clip label."""
    label = normalize_phrase(label)
    label = re.sub(r"^[a-z ]*?\d+\s+", "", label)
    return re.sub(r"^\d+\s+", "", label)


class SoundboardVoice:
    def __init__(self, catalog_path):
        self.catalog_path = Path(catalog_path)
        self.root = self.catalog_path.parent
        self.sounds = []
        self._phrases = {}
        self._verified_modules = set()
        self._load()

    @property
    def count(self):
        return len(self.sounds)

    def _load(self):
        if not self.catalog_path.is_file():
            return
        try:
            catalog = json.loads(self.catalog_path.read_text())
        except (OSError, ValueError):
            return
        self.sounds = catalog.get("sounds", [])
        for sound in self.sounds:
            for phrase in (normalize_phrase(sound.get("label", "")),
                           _spoken_label(sound.get("label", ""))):
                if phrase:
                    self._phrases.setdefault(phrase, sound)

    def resolve(self, text):
        phrase = normalize_phrase(text)
        phrase = normalize_phrase(ALIASES.get(phrase, phrase))
        return self._phrases.get(phrase)

    def _module_path(self, sound):
        path = (self.root / sound["module"]).resolve()
        if self.root.resolve() not in path.parents:
            raise ValueError("sound module escapes catalog directory")
        return path

    def synth(self, text):
        sound = self.resolve(text)
        if sound is None:
            return None
        try:
            path = self._module_path(sound)
            module = path.read_bytes()
            expected = sound["module_sha256"]
            if expected not in self._verified_modules:
                if hashlib.sha256(module).hexdigest() != expected:
                    return None
                self._verified_modules.add(expected)
            pcm = bytearray()
            for segment in sound["segments"]:
                start = int(segment["pcm_offset"])
                end = start + int(segment["content_bytes"])
                if start < 0 or end > len(module):
                    return None
                pcm.extend(module[start:end])
            return tts_bank.pcm_to_wav_bytes(bytes(pcm)) if pcm else None
        except (KeyError, OSError, TypeError, ValueError):
            return None

