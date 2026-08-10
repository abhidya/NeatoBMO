"""VoiceSynth: the one owner of "how BMO's voice gets made".

Engine ladder: neural BMO clone (Piper prosody -> RVC timbre, served by
tools/bmo_voice_server.py) first, Colibri's espeak endpoint next, local
espeak-ng last.  The cache is keyed by the *requested* voice, so a synth
that fell back still satisfies the next identical request — this is what
makes the routine-layer precache actually hit (the old module-level cache
keyed on the fallback voice and never matched the runtime default).
"""
import json
import subprocess
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

VOICES = {
    "bmo-rvc": "BMO (neural clone)",
    "en+f4": "espeak en+f4",
    "en+f2": "espeak en+f2",
    "en+m3": "espeak en+m3",
    "en+m1": "espeak en+m1",
    "en": "espeak en",
}
# espeak parameters mirror bmo_brain_server defaults so the voice is
# identical whether Colibri answers or the local fallback runs.
ESPEAK_SPEED = 160
ESPEAK_PITCH = 70


class VoiceSynth:
    def __init__(self, voice_server, brain_url=None, api_key=None,
                 default_voice="bmo-rvc"):
        self.voice_server = voice_server.rstrip("/")
        self.brain_url = brain_url.rstrip("/") if brain_url else None
        self.api_key = api_key
        self.default_voice = default_voice
        self._cache = {}          # (sanitized text, requested voice) -> wav

    # ---- engines --------------------------------------------------------
    def _neural(self, text):
        req = urllib.request.Request(
            self.voice_server + "/synth",
            data=json.dumps({"text": text}).encode(),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=300) as resp:
            wav = resp.read()
        if not wav.startswith(b"RIFF"):
            raise RuntimeError("voice server returned a non-WAV response")
        return wav

    def _colibri(self, text, voice):
        if self.brain_url is None:
            raise RuntimeError("no Colibri TTS endpoint configured")
        req = urllib.request.Request(
            self.brain_url + "/audio/speech",
            data=json.dumps({"model": "espeak-ng", "voice": voice,
                             "input": text, "response_format": "wav"}).encode(),
            headers={"Content-Type": "application/json",
                     **({"Authorization": f"Bearer {self.api_key}"}
                        if self.api_key else {})})
        with urllib.request.urlopen(req, timeout=45) as resp:
            wav = resp.read()
        if not wav.startswith(b"RIFF"):
            raise RuntimeError("Colibri TTS returned a non-WAV response")
        return wav

    def _espeak(self, text, voice):
        with tempfile.NamedTemporaryFile(suffix=".wav") as handle:
            proc = subprocess.run(
                ["espeak-ng", "-v", voice, "-s", str(ESPEAK_SPEED),
                 "-p", str(ESPEAK_PITCH), "-w", handle.name, text],
                capture_output=True, timeout=30)
            if proc.returncode:
                raise RuntimeError(proc.stderr.decode(errors="replace").strip()
                                   or "local espeak-ng failed")
            wav = Path(handle.name).read_bytes()
        if not wav.startswith(b"RIFF"):
            raise RuntimeError("local espeak-ng did not produce a WAV")
        return wav

    # ---- the interface --------------------------------------------------
    def synth(self, text, voice=None):
        """Text -> WAV via the first engine that answers."""
        from . import tts_bank
        requested = voice or self.default_voice
        text = tts_bank.sanitize_speech_text(text) or text
        if (cached := self._cache.get((text, requested))) is not None:
            return cached
        engine_voice = requested
        wav = None
        if requested == "bmo-rvc":
            try:
                wav = self._neural(text)
            except (urllib.error.URLError, OSError, RuntimeError) as exc:
                print("voice server unavailable, falling back to espeak:", exc)
                engine_voice = "en+f4"
        if wav is None:
            try:
                wav = self._colibri(text, engine_voice)
            except (urllib.error.URLError, OSError, RuntimeError):
                wav = self._espeak(text, engine_voice)
        self._cache[(text, requested)] = wav
        return wav

    def precache(self, texts, voice=None):
        """Warm the cache (routine layer's instant answers) at startup."""
        from . import tts_bank
        count = 0
        for text in texts:
            speech = tts_bank.sanitize_speech_text(text)
            if not speech or (speech, voice or self.default_voice) in self._cache:
                continue
            try:
                self.synth(speech, voice)
                count += 1
            except Exception:
                return count    # no TTS engine available; cache stays partial
        return count
