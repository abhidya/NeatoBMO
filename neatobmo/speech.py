"""SpeechService: text (or a live sentence stream) -> BMO's actual voice.

Owns the speech job state machine that used to live as a module-global
dict in bmo_web: synth pipeline, chunk packing, burn+speak via BankBurner,
thinking-sound vocalizing while the brain/synth is slow, BMO-bank restore,
and the validated profile installs.  Robot access goes through the
injected BodyController (which owns the lock); WAVs come from the injected
VoiceSynth — so the whole machine runs in tests with fakes.
"""
import hashlib
import json
import threading
import time

from . import tts_bank

ACTIVE_STATES = {"synthesizing", "building", "burning", "speaking", "restoring"}

# Thinking feedback is punctuation, not hold music.  BMO stays quiet through
# ordinary latency, gives one curious "hm?" after a real pause, then one small
# reassuring boop only if the wait becomes unusually long.  The assets remain
# reserved in every generated bank, so these cues need no extra flash write.
THINKING_SLOT_FILES = {3: "thinking-blip-a.wav", 19: "thinking-blip-b.wav",
                       9: "thinking-hum.wav"}
THINKING_CUES = ((2.2, 3), (4.5, 19))
SOUNDBOARD_INSTALL_CONFIRMATION = "INSTALL PREGENERATED BMO SOUND BANK"


def load_thinking_sounds(directory):
    sounds = {}
    for sound_id, name in THINKING_SLOT_FILES.items():
        path = directory / name
        if path.exists():
            sounds[sound_id] = tts_bank.decode_wav(path.read_bytes()).tobytes()
    return sounds


class SpeechService:
    def __init__(self, body, voice, log_path=None, thinking_dir=None):
        self.body = body
        self.voice = voice
        self.log_path = log_path
        self.thinking_sounds = (load_thinking_sounds(thinking_dir)
                                if thinking_dir else {})
        self.job = None
        self.lock = threading.Lock()      # guards job/installed_* mutation
        self.installed_profile = "bmo"
        self.installed_sha = None         # sha of whatever bank is in flash
        self.flash_writes = 0             # session flash-write (wear) counter

    # ---- job plumbing ---------------------------------------------------
    def _log(self, job, message):
        entry = {"ts": round(time.time(), 3), "job": job["id"],
                 "message": message}
        job["log"].append(entry)
        if self.log_path is not None:
            try:
                self.log_path.parent.mkdir(parents=True, exist_ok=True)
                with open(self.log_path, "a") as handle:
                    handle.write(json.dumps(entry) + "\n")
            except OSError:
                pass

    def active(self):
        return self.job is not None and self.job["state"] in ACTIVE_STATES

    def status(self):
        if self.job is None:
            return {"state": "idle",
                    "installed_profile": self.installed_profile,
                    "flash_writes": self.flash_writes}
        job = self.job
        return {
            "id": job["id"],
            "state": job["state"],
            "text": job["text"],
            "voice": job["voice"],
            "chunks": job.get("chunk_summaries", []),
            "progress": job["progress"].snapshot(),
            "installed_profile": self.installed_profile,
            "installed_bank_sha256": job.get("installed_sha"),
            "flash_writes": self.flash_writes,
            "error": job.get("error"),
            "log": job["log"][-40:],
        }

    def stop(self):
        with self.lock:
            if self.job is None:
                return "no TTS job"
            self.job["stop"].set()
            self._log(self.job, "stop requested")
            return None

    # ---- thinking sounds ------------------------------------------------
    def _thinking_loop(self, stop):
        """Play at most two delayed, non-repeating thinking cues."""
        if not self.thinking_sounds:
            return
        for wait, sound_id in THINKING_CUES:
            if stop.wait(wait):
                return
            self.body.try_call(
                lambda r, s=sound_id: r.cmd(f"PlaySound {s}", timeout=3),
                lock_timeout=0.2)

    # ---- speaking -------------------------------------------------------
    def _capacities(self, baseline):
        records = {r.sound_id: r
                   for r in tts_bank.record_ranges_from_bytes(baseline)}
        return [(sid, records[sid].sample_count)
                for sid in tts_bank.SLOT_SEQUENCE
                if sid not in self.thinking_sounds]

    def speak(self, text, voice=None, units=None):
        """Create and launch a speech job; returns (job, error).

        ``units``: optional live iterable of sentences (streaming LLM) —
        speech starts on the first sentence while later ones are still
        being generated.
        """
        voice = voice or self.voice.default_voice
        with self.lock:
            if not self.body.attached:
                return None, "body not attached"
            if self.active():
                return None, "already speaking; stop first or wait"
            if units is None:
                # Emojis drive the LCD face, not the speaker: espeak reads
                # them out as long Unicode names and wrecks the speech.
                text = tts_bank.sanitize_speech_text(text)
                if not text:
                    return None, "nothing speakable after removing emoji/symbols"
            job = {
                "id": f"tts-{int(time.time())}",
                "state": "synthesizing",
                "text": text,
                "voice": voice,
                "units": units,
                "progress": tts_bank.PlaybackProgress(),
                "stop": threading.Event(),
                "log": [],
            }
            self.job = job
        self._log(job, f"speak: voice={voice} "
                       f"{'streaming units' if units else repr(text)}")
        threading.Thread(target=self._run_speak, args=(job,),
                         daemon=True).start()
        return job, None

    def _run_speak(self, job):
        """Background thread: streaming synth pipeline -> burn+speak per chunk.

        Sentences (from the job text, or arriving live from a streaming LLM
        via job["units"]) are solo-synthesized and packed into bank-sized
        chunks; while a chunk burns and plays, later sentences keep
        synthesizing.  The finished bank persists; BMO restore is an
        explicit action.  Banks whose hash matches the installed one skip
        the flash write.
        """
        try:
            # Authentic catalog hits take the native runtime-audio path: no
            # synthesis, bank construction, or flash wear. If the Neato
            # firmware lacks PlaySound File, retain the established bank path.
            soundboard = getattr(self.voice, "soundboard", None)
            esp32 = getattr(self.body, "esp32", None)
            if job.get("units") is None and soundboard is not None and esp32:
                clip = soundboard.synth(job["text"])
                if clip is not None:
                    try:
                        job["state"] = "speaking"
                        esp32.speak(clip)
                        job["state"] = "complete"
                        self._log(job, "authentic soundboard clip played via ESP32")
                        return
                    except Exception as exc:
                        self._log(job, f"native soundboard playback unavailable: {exc}")

            profile = tts_bank.BANK_PROFILES["bmo"]
            baseline = profile["path"].read_bytes()
            if hashlib.sha256(baseline).hexdigest() != tts_bank.BMO_BANK_SHA256:
                raise RuntimeError("persistent BMO artifact hash mismatch; refusing")
            capacities = self._capacities(baseline)
            job["state"] = "synthesizing"
            synth = lambda t: tts_bank.prepare_speech_pcm(
                self.voice.synth(t, job["voice"]))
            units = job.get("units") or tts_bank.split_text_units(job["text"])

            def tracked_chunks():
                for chunk in tts_bank.pack_audio_chunks(units, synth, capacities):
                    job["chunk_summaries"].append({
                        "index": len(job["chunk_summaries"]),
                        "text": chunk["text"],
                        "seconds": round(len(chunk["samples"])
                                         / tts_bank.PCM_SAMPLE_RATE, 3),
                        "segments": len(chunk["segments"]),
                    })
                    self._log(job, f"chunk {len(job['chunk_summaries'])} ready: "
                                   f"{job['chunk_summaries'][-1]['seconds']}s "
                                   f"“{chunk['text'][:60]}”")
                    yield chunk

            job["chunk_summaries"] = []

            # Producer thread: synthesis keeps running while chunks burn/play.
            import queue as queue_mod
            chunk_queue = queue_mod.Queue(maxsize=2)

            def produce():
                try:
                    for chunk in tracked_chunks():
                        chunk_queue.put(("chunk", chunk))
                    chunk_queue.put(("done", None))
                except Exception as exc:
                    chunk_queue.put(("error", exc))

            threading.Thread(target=produce, daemon=True).start()

            def queued_chunks(first_item):
                item = first_item
                while True:
                    kind, value = item
                    if kind == "done":
                        return
                    if kind == "error":
                        raise value
                    yield value
                    item = chunk_queue.get()

            # Wait for the first chunk before taking the robot lock, so
            # other robot commands aren't blocked during the initial
            # synthesis.  If that wait is genuinely noticeable, BMO may add
            # one or two restrained character cues rather than looping noise.
            think_stop = threading.Event()
            threading.Thread(target=self._thinking_loop, args=(think_stop,),
                             daemon=True).start()
            try:
                first = chunk_queue.get()
            finally:
                think_stop.set()

            def burn_and_play(robot):
                burner = tts_bank.BankBurner(robot,
                                             log=lambda m: self._log(job, m))
                return tts_bank.speak_chunks_operation(
                    burner, baseline, queued_chunks(first), "silence",
                    progress=job["progress"], stop_event=job["stop"],
                    installed_sha256=self.installed_sha,
                    reserved=self.thinking_sounds)

            report = self.body.call(burn_and_play)
            job["report"] = report
            burns = sum(1 for c in report["chunks"]
                        if not c["burn"].get("skipped"))
            job["installed_sha"] = report.get("installed_bank_sha256")
            with self.lock:
                self.flash_writes += burns
                if job["installed_sha"]:
                    self.installed_sha = job["installed_sha"]
                    self.installed_profile = "tts"
            job["state"] = job["progress"].state
            self._log(job, f"speech finished: state={job['state']} "
                           f"({burns} flash writes, "
                           f"{report['reused_burns']} reused)")
        except Exception as exc:
            job["error"] = str(exc)
            job["state"] = "error"
            self._log(job, f"speech failed: {exc}")

    # ---- restore / profile install --------------------------------------
    def restore(self):
        """Bring the persistent BMO bank back; returns an error string or None."""
        with self.lock:
            if self.active():
                return "operation still running"
            if not self.body.attached:
                return "body not attached"
            if self.job is None:
                self.job = {"id": f"restore-{int(time.time())}",
                            "state": "restoring", "text": "", "voice": "",
                            "progress": tts_bank.PlaybackProgress(),
                            "stop": threading.Event(), "log": []}
            job = self.job
            job["state"] = "restoring"
        threading.Thread(target=self._run_restore, args=(job,),
                         daemon=True).start()
        return None

    def _run_restore(self, job):
        try:
            profile = tts_bank.BANK_PROFILES["bmo"]

            def restore(robot):
                burner = tts_bank.BankBurner(robot,
                                             log=lambda m: self._log(job, m))
                return burner.restore_bank(profile["path"],
                                           tts_bank.BMO_BANK_SHA256,
                                           "persistent BMO bank")

            job["state"] = "restoring"
            result = self.body.call(restore)
            with self.lock:
                self.installed_profile = "bmo"
                self.installed_sha = tts_bank.BMO_BANK_SHA256
                self.flash_writes += 1
            job["installed_sha"] = None
            job["state"] = "restored"
            self._log(job, f"BMO restore verified: {result['accepted_ids']}")
        except Exception as exc:
            job["error"] = str(exc)
            job["state"] = "error"
            self._log(job, f"BMO restore failed: {exc}")

    def install_profile(self, profile_name, confirmation):
        """Install one of the two validated bank images (blocking).

        Routed through BankBurner.restore_bank so the install path shares
        the tested guards: size+hash re-proof, pre-burn identity check,
        post-burn identity poll, and the audible slot sweep.
        """
        if self.active():
            return {"error": "a TTS bank operation is running"}
        profile = tts_bank.BANK_PROFILES.get(profile_name)
        if profile is None:
            return {"error": "unknown sound-bank profile"}
        if confirmation != profile["confirmation"]:
            return {"error": f"type {profile['confirmation']} exactly"}
        if not self.body.attached:
            return {"error": "body not attached"}
        try:
            def install(robot):
                burner = tts_bank.BankBurner(robot)
                return burner.restore_bank(profile["path"], profile["sha256"],
                                           profile["label"])
            result = self.body.call(install)
            with self.lock:
                self.installed_profile = profile_name
                self.installed_sha = profile["sha256"]
                self.flash_writes += 1
            return {"ok": True, "profile": profile_name,
                    "label": profile["label"], "sha256": result["sha256"],
                    "accepted_ids": result["accepted_ids"],
                    "receiver_hex": result["receiver_hex"]}
        except Exception as exc:
            return {"error": str(exc)}

    def install_soundboard_module(self, soundboard, key, confirmation):
        """Install an approved catalog page exactly as prepared on disk.

        This is an explicit offline/fallback operation. Normal clip playback
        uses ESP32 runtime WAV audio and never flashes a bank per utterance.
        """
        if self.active():
            return {"error": "a TTS bank operation is running"}
        if confirmation != SOUNDBOARD_INSTALL_CONFIRMATION:
            return {"error": f"type {SOUNDBOARD_INSTALL_CONFIRMATION} exactly"}
        if not self.body.attached:
            return {"error": "body not attached"}
        artifact = soundboard.module_artifact(key)
        if artifact is None:
            return {"error": "unknown, rejected, or invalid soundboard key"}
        try:
            def install(robot):
                burner = tts_bank.BankBurner(robot)
                return burner.restore_bank(
                    artifact["path"], artifact["sha256"],
                    f"pre-generated BMO page for {artifact['label']}")
            result = self.body.call(install)
            with self.lock:
                self.installed_profile = f"soundboard:{key}"
                self.installed_sha = artifact["sha256"]
                self.flash_writes += 1
            return {"ok": True, "key": key, "label": artifact["label"],
                    "sha256": result["sha256"],
                    "accepted_ids": result["accepted_ids"],
                    "receiver_hex": result["receiver_hex"],
                    "pre_generated": True}
        except Exception as exc:
            return {"error": str(exc)}
