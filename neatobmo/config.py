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
    speech_mode: str = "soundboard"                   # NEATOBMO_SPEECH
    speech_burst: float = cues.SPEECH_BURST_SECONDS   # NEATOBMO_SPEECH_BURST
    voice_server: str = "http://127.0.0.1:8486"       # NEATOBMO_VOICE
    brain_engine: str = "/Volumes/2TB/colibri-v1.5.0-macos-arm64/olmoe"  # NEATOBMO_BRAIN_ENGINE
    brain_snap: str = "/Volumes/2TB/models/olmoe-snap"  # NEATOBMO_BRAIN_SNAP
    api_key: str | None = field(default_factory=_read_api_key)
    default_voice: str = "bmo-rvc"
    soundboard_catalog: Path = (REPO_ROOT / "docs" / "bmo-soundboard" /
                                "catalog.json")
    # BMO's "decrypt my software" behaviour: the archived .enc image to work
    # on and where recovered plaintext + reports land (BMO's SSD first).
    decrypt_image: Path = Path(
        "/Volumes/2TB/neato-firmware-archive/sources/"
        "Neato-XV-Series-Cruz-Rev-113-Update/OriginalVorwerkFirmwareFiles/"
        "Firmware25/XV11App.15893.P.bin.enc")          # NEATOBMO_DECRYPT_IMAGE
    decrypt_output_dir: Path = Path(
        "/Volumes/2TB/neato-firmware-archive/work/"
        "plaintext-candidates")                        # NEATOBMO_DECRYPT_OUTPUT_DIR
    miner_address: str = ""                           # NEATOBMO_MINER_ADDRESS
    miner_pool: str = "public-pool.io:3333"           # NEATOBMO_MINER_POOL
    miner_tls: bool = False                           # NEATOBMO_MINER_TLS
    miner_threads: int = 2                            # NEATOBMO_MINER_THREADS
    miner_enabled: bool = True                        # NEATOBMO_MINER_ENABLED

    @classmethod
    def from_env(cls):
        env = os.environ.get

        def truthy(key, default=False):
            return env(key, "1" if default else "0").strip().lower() in (
                "1", "true", "yes", "on")

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
            soundboard_catalog=Path(env("NEATOBMO_SOUNDBOARD_CATALOG",
                                        str(cls.soundboard_catalog))),
            decrypt_image=Path(env("NEATOBMO_DECRYPT_IMAGE",
                                   str(cls.decrypt_image))),
            decrypt_output_dir=Path(env("NEATOBMO_DECRYPT_OUTPUT_DIR",
                                        str(cls.decrypt_output_dir))),
            miner_address=env("NEATOBMO_MINER_ADDRESS", cls.miner_address),
            miner_pool=env("NEATOBMO_MINER_POOL", cls.miner_pool),
            miner_tls=truthy("NEATOBMO_MINER_TLS", cls.miner_tls),
            miner_threads=int(env("NEATOBMO_MINER_THREADS",
                                  str(cls.miner_threads))),
            miner_enabled=truthy("NEATOBMO_MINER_ENABLED", cls.miner_enabled),
        )
