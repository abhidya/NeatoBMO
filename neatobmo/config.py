"""Every NEATOBMO_* knob in one place.

The orchestrator (bmo_web) builds one Config in __main__ and hands it to
the modules it constructs; nothing else reads os.environ, so tests can
build a Config by hand and every default is documented right here.
"""
import os
from dataclasses import dataclass, field
from pathlib import Path

from . import cues

REPO_ROOT = Path(__file__).resolve().parent.parent


def _read_api_key():
    key_file = os.path.expanduser("~/.neatobmo/coli_api_key")
    if os.path.exists(key_file):
        with open(key_file) as handle:
            return handle.read().strip()
    return None


@dataclass
class Config:
    brain: str = "http://127.0.0.1:8000/v1"           # NEATOBMO_BRAIN
    esp32: str = "http://10.0.0.106"                  # NEATOBMO_ESP32
    port: int = 8485                                  # PORT
    speech_mode: str = "soundbyte"                    # NEATOBMO_SPEECH
    speech_burst: float = cues.SPEECH_BURST_SECONDS   # NEATOBMO_SPEECH_BURST
    voice_server: str = "http://127.0.0.1:8486"       # NEATOBMO_VOICE
    brain_engine: str = "/Volumes/2TB/colibri-v1.5.0-macos-arm64/olmoe"  # NEATOBMO_BRAIN_ENGINE
    brain_snap: str = "/Volumes/2TB/models/olmoe-snap"  # NEATOBMO_BRAIN_SNAP
    api_key: str | None = field(default_factory=_read_api_key)
    default_voice: str = "bmo-rvc"

    @classmethod
    def from_env(cls):
        env = os.environ.get
        return cls(
            brain=env("NEATOBMO_BRAIN", cls.brain).rstrip("/"),
            esp32=env("NEATOBMO_ESP32", cls.esp32),
            port=int(env("PORT", str(cls.port))),
            speech_mode=env("NEATOBMO_SPEECH", cls.speech_mode),
            speech_burst=float(env("NEATOBMO_SPEECH_BURST",
                                   str(cues.SPEECH_BURST_SECONDS))),
            voice_server=env("NEATOBMO_VOICE", cls.voice_server),
            brain_engine=env("NEATOBMO_BRAIN_ENGINE", cls.brain_engine),
            brain_snap=env("NEATOBMO_BRAIN_SNAP", cls.brain_snap),
        )
