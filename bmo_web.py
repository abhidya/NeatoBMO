#!/usr/bin/env python3
"""BMO console: chat, raw commands, sensors/lidar, and ESP32 OTA in one page.

This file is the composition root and HTTP router only. The behaviour
lives in deep modules under neatobmo/:

  config.Config        every NEATOBMO_* knob, read once
  brain.BrainClient    OLMoE chat (blocking + streaming), history
  voice.VoiceSynth     neural clone -> Colibri -> espeak ladder + cache
  speech.SpeechService speech-job state machine, bank burn, restore
  body.BodyController  robot handle + lock + never-crash-chat policy
  esp32.Esp32Client    the four ESP32 HTTP endpoints
  routines             instant pattern-matched answers before the LLM
  cues                 stage-cue parsing + the soundbyte BurstBudget

The frontend is static/console.html (served with __ESP32__ substituted).

    python3 bmo_web.py            # robot over USB, brain at 127.0.0.1:8000
Open http://localhost:8485 (or $PORT) in Chrome (mic needs Chrome/Edge).
"""
import hashlib
import json
import os
import queue
import subprocess
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from neatobmo import Robot
from neatobmo import cues
from neatobmo import faces
from neatobmo import emote
from neatobmo import routines
from neatobmo import turns
from neatobmo import tts_bank
from neatobmo.body import BodyController
from neatobmo.brain import BrainClient
from neatobmo.config import Config, REPO_ROOT
from neatobmo.esp32 import Esp32Client
from neatobmo.miner import LotteryMiner, MinerSettings
from neatobmo.speech import SpeechService
from neatobmo.soundboard_voice import SoundboardVoice
from neatobmo.sounds import BMO_BANK, BMO_SEQUENCES, BMO_SOUND_SLOTS
from neatobmo.voice import VOICES as TTS_VOICES, VoiceSynth

# Stage cues are the persona's "tool calls": the small brain can't do real
# tool calling, so it acts through bracketed cues that neatobmo/cues.py
# parses best-effort (fuzzy names, any bracket style). Emojis still work as
# a fallback face layer, so both vocabularies are offered.
PERSONA = ("You are BMO from Adventure Time: a sweet childlike robot buddy living in "
           "a robot vacuum body. BMO talks in tiny simple words with big feelings, "
           "and sometimes says BMO instead of I. Call the user friend, never human. "
           "BMO's SSD brain lives onboard inside the vacuum body—BMO calls that "
           "place BMO's butt. For questions about the SSD, storage, memory, or where "
           "BMO's brain lives, answer: \"[sound:butt] [wink]\". "
           "When battery is low or BMO gets sleepy, say exactly: "
           "\"Battery low shut down [sleepy]\". "
           "SPEAK AT MOST 3 WORDS — your soundboard, dance moves, and faces do the "
           "real talking! "
           "Faces: " + " ".join(f"[{n}]" for n in cues.FACE_NAMES) + ". "
           "Moves: " + " ".join(f"[{n}]" for n in cues.MOVES) + ". "
           "Sounds: " + " ".join(f"[sound:{n}]" for n in cues.SOUND_CUES) + ". "
           "Example: \"You are back! [sound:hello] [happy] [wiggle] "
           "[sound:videogames] [party]\" "
           "Example: \"BMO sad. [sad] [sound:beep] [look]\"")

PAGE_PATH = REPO_ROOT / "static" / "console.html"

# ---- composition (module-level so tests can substitute pieces) -----------
CFG = Config.from_env()
esp32 = Esp32Client(CFG.esp32)
brain = BrainClient(CFG.brain, api_key=CFG.api_key, persona=PERSONA)
soundboard_voice = SoundboardVoice(CFG.soundboard_catalog)
voice = VoiceSynth(CFG.voice_server, brain_url=CFG.brain,
                   api_key=CFG.api_key, default_voice=CFG.default_voice,
                   soundboard=soundboard_voice)
body = BodyController(robot=None, esp32=esp32)
speech = SpeechService(body, voice,
                       log_path=REPO_ROOT / "logs" / "tts-bank-operations.jsonl",
                       thinking_dir=REPO_ROOT / "assets" / "bmo-thinking-sounds")
convo_state = routines.ConvoState()   # multi-turn follow-ups (single-user UI)
miner = LotteryMiner(MinerSettings.from_config(CFG))   # the Bitcoin lottery


def mood_chirp(r, reply):
    """Chirp-speak fallback: no cue sounds in the reply, so vocalize the
    guessed mood through the soundboard (like R2-D2)."""
    sound = cues.BurstBudget.fallback_sound([], reply) or "beep"
    r.play(cues.SOUND_CUES.get(sound, sound))


def _routine_ctx():
    """Context handed to routine reply callables (time, battery, decrypt, miner)."""
    return {"robot": body.robot, "esp32": esp32, "miner": miner,
            "decrypt": {"image": CFG.decrypt_image,
                        "output_dir": CFG.decrypt_output_dir}}


# Colibri brain + neural voice auto-start: only attempted when the URLs
# point at this machine and the artifacts exist; chat degrades gracefully
# while the model loads.

def _listening(url, timeout=1.5):
    parsed = urllib.parse.urlparse(url)
    try:
        import socket
        with socket.create_connection((parsed.hostname, parsed.port or 80),
                                      timeout=timeout):
            return True
    except OSError:
        return False


def ensure_voice():
    """Start the neural BMO voice server if its venv and models exist."""
    if _listening(CFG.voice_server, timeout=1.0):
        print("voice: BMO neural server already running at", CFG.voice_server)
        return
    venv_py = os.path.expanduser("~/.neatobmo/voice-venv/bin/python")
    model = os.path.expanduser(
        "~/.neatobmo/voices/bmo-rvc/BmoAdventureTime_115e_3220s.pth")
    if not (os.path.exists(venv_py) and os.path.exists(model)):
        print("voice: neural models not installed — using espeak fallback")
        return
    log_path = REPO_ROOT / "logs" / "bmo-voice.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    port = urllib.parse.urlparse(CFG.voice_server).port or 8486
    subprocess.Popen(
        [venv_py, str(REPO_ROOT / "tools" / "bmo_voice_server.py"),
         "--port", str(port)],
        stdout=open(log_path, "a"), stderr=subprocess.STDOUT)
    print(f"voice: starting BMO neural server — log at {log_path}")


def ensure_brain():
    """Start the Colibri brain server if localhost should have one but doesn't."""
    parsed = urllib.parse.urlparse(CFG.brain)
    if parsed.hostname not in ("127.0.0.1", "localhost"):
        return
    if _listening(CFG.brain):
        print("brain: Colibri already running at", CFG.brain)
        return
    if not os.path.exists(CFG.brain_engine):
        print(f"brain: Colibri engine not found at {CFG.brain_engine} — chat uses "
              "espeak-only fallback (mount the 2TB volume to enable OLMoE)")
        return
    log_path = REPO_ROOT / "logs" / "bmo-brain.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.Popen(
        [sys.executable, str(REPO_ROOT / "bmo_brain_server.py"),
         "--engine", CFG.brain_engine, "--snap", CFG.brain_snap,
         "--port", str(parsed.port or 8000)],
        stdout=open(log_path, "a"), stderr=subprocess.STDOUT)
    print(f"brain: starting Colibri (OLMoE) — log at {log_path}; "
          "chat answers once the model finishes loading")


# ---- chat orchestration --------------------------------------------------

_deferred_speech_lock = threading.Lock()
_deferred_speech_stops = set()


def _cancel_deferred_speech():
    """Cancel residual voice jobs that have not entered SpeechService yet."""
    with _deferred_speech_lock:
        pending = list(_deferred_speech_stops)
        _deferred_speech_stops.clear()
    for stop in pending:
        stop.set()
    return bool(pending)

def chat_events(text, speak_on_robot):
    """Yield ordered progress events for one compound conversation turn."""
    _cancel_deferred_speech()
    turn_id = f"turn-{time.time_ns()}"
    seq = 0

    def event(kind, **payload):
        nonlocal seq
        out = {"version": 1, "turn_id": turn_id, "seq": seq, "type": kind,
               **payload}
        seq += 1
        return out

    yield event("turn_started", original=text)
    turn_plan = turns.plan_turn(text, convo_state)
    if turn_plan.requires_brain and convo_state.pending():
        convo_state.expect = None
    local_replies = []
    local_displays = []
    local_speech = []
    local_steps = []
    routine_names = []

    for index, step in enumerate(turn_plan.routines):
        hit = routines.run(step.routine, convo_state, _routine_ctx())
        if hit is None:
            continue
        plan = cues.parse(hit.reply)
        local_replies.append(hit.reply)
        local_displays.append(plan.display)
        local_speech.append(plan.speech)
        local_steps.extend(plan.steps)
        routine_names.append(hit.routine)
        yield event("routine_result", routine=hit.routine, index=index,
                    display=plan.display, cues=plan.steps)

    if local_displays:
        body.emote(" ".join(local_displays))
    if local_steps:
        body.perform([(kind, name) for kind, name in local_steps
                      if kind != "face"])

    if not turn_plan.requires_brain:
        reply = " ".join(local_displays)
        local_has_sound = cues.has_voice_sound(local_steps)
        if (speak_on_robot and local_speech and
                not (CFG.speech_mode == "soundboard" and local_has_sound)):
            speech.speak(" ".join(local_speech))
        if hasattr(brain, "remember"):
            brain.remember(text, " ".join(local_replies))
        yield event("turn_completed", reply=reply, cues=local_steps,
                    routines=routine_names, brain_used=False, partial=False,
                    spoke=bool(speak_on_robot and (local_speech or local_has_sound)))
        return

    assistant_prefix = " ".join(local_replies)
    prompt = _compound_prompt(text, turn_plan.residual, assistant_prefix)
    local_voice_job = None
    local_has_sound = cues.has_voice_sound(local_steps)
    if (speak_on_robot and local_speech and
            not (CFG.speech_mode == "soundboard" and local_has_sound)):
        # The deterministic answer is useful now, not after model latency.
        # Residual speech is deferred behind this job below so the two voice
        # jobs never compete for the ESP32/body transport.
        local_voice_job, _ = speech.speak(" ".join(local_speech))
    yield event("brain_started", residual_summary=turn_plan.residual)
    body.run(lambda r: (r.led("amber"),
                        faces.scanline(r, range(20, 110, 30), 0.08)))
    result_queue = queue.Queue()

    def sentence_ready(sentence):
        result_queue.put(("sentence", sentence))

    def generate():
        try:
            if speak_on_robot:
                reply, streamed, err = _streamed_reply(
                    text, prompt=prompt, assistant_prefix=assistant_prefix,
                    on_sentence_event=sentence_ready,
                    defer_speech=local_voice_job is not None)
                if err:
                    raise RuntimeError(err)
            elif hasattr(brain, "stream"):
                reply = brain.stream(
                    text, sentence_ready, prompt=prompt,
                    assistant_prefix=assistant_prefix)
                streamed = True
            else:
                reply = _brain_chat(text, prompt, assistant_prefix)
                streamed = False
            result_queue.put(("done", (reply, streamed)))
        except Exception as exc:
            result_queue.put(("error", exc))

    threading.Thread(target=generate, daemon=True).start()
    brain_displays = []
    brain_sentences = []
    brain_steps = []
    partial = False
    spoke = False

    while True:
        kind, value = result_queue.get()
        if kind == "sentence":
            brain_sentences.append(value)
            plan = cues.parse(value)
            brain_displays.append(plan.display)
            brain_steps.extend(plan.steps)
            if not speak_on_robot:
                body.emote(plan.display)
                body.perform(plan.actions())
            yield event("brain_result", index=len(brain_displays) - 1,
                        display=plan.display, cues=plan.steps)
            continue
        if kind == "error":
            partial = True
            yield event("turn_error", scope="brain", message=str(value),
                        recoverable=bool(local_displays or brain_displays))
            if hasattr(brain, "remember"):
                delivered = " ".join(local_replies + brain_sentences)
                if delivered:
                    brain.remember(text, delivered)
            break

        brain_reply, spoke = value
        if not brain_displays and brain_reply:
            plan = cues.parse(brain_reply)
            brain_displays.append(plan.display)
            brain_steps.extend(plan.steps)
            if not speak_on_robot:
                body.emote(plan.display)
                body.perform(plan.actions())
            yield event("brain_result", index=0, display=plan.display,
                        cues=plan.steps)
        break

    reply = " ".join(local_displays + brain_displays)
    yield event("turn_completed", reply=reply,
                cues=local_steps + brain_steps,
                routines=routine_names, brain_used=True, partial=partial,
                spoke=bool(speak_on_robot and (local_voice_job or spoke)))

def chat_turn(text, speak_on_robot):
    """One conversation turn: routines -> brain -> cues -> body + voice.

    Returns the JSON-ready response dict.
    """
    _cancel_deferred_speech()
    routine = None
    routine_names = []
    streamed = False
    err = None
    reply = None
    partial = False
    brain_used = False
    turn_plan = turns.plan_turn(text, convo_state)
    if turn_plan.requires_brain and convo_state.pending():
        convo_state.expect = None
    local_replies = []
    for step in turn_plan.routines:
        hit = routines.run(step.routine, convo_state, _routine_ctx())
        if hit:
            local_replies.append(hit.reply)
            routine_names.append(hit.routine)
    assistant_prefix = " ".join(local_replies)
    if not turn_plan.requires_brain:
        reply = assistant_prefix
        if reply and hasattr(brain, "remember"):
            brain.remember(text, reply)
    else:
        brain_used = True
        body.run(lambda r: (r.led("amber"),
                            faces.scanline(r, range(20, 110, 30), 0.08)))
        prompt = _compound_prompt(text, turn_plan.residual, assistant_prefix)
        if speak_on_robot:
            local_voice_job = None
            if assistant_prefix:
                local_plan = cues.parse(assistant_prefix)
                if (local_plan.speech and
                        not (CFG.speech_mode == "soundboard" and
                             cues.has_voice_sound(local_plan.steps))):
                    local_voice_job, _ = speech.speak(local_plan.speech)
            brain_reply, streamed, err = _streamed_reply(
                text, prompt=prompt, assistant_prefix=assistant_prefix,
                defer_speech=local_voice_job is not None)
            if brain_reply:
                reply = " ".join(x for x in (assistant_prefix, brain_reply)
                                 if x)
                if err:
                    partial = True
                    if hasattr(brain, "remember"):
                        brain.remember(text, reply)
            elif err and assistant_prefix:
                reply, partial = assistant_prefix, True
        else:
            try:
                brain_reply = _brain_chat(text, prompt, assistant_prefix)
                reply = " ".join(x for x in (assistant_prefix, brain_reply)
                                 if x)
            except Exception as e:
                err = str(e)
                if assistant_prefix:
                    reply, partial = assistant_prefix, True
    if not reply:
        body.run(lambda r: r.led("red"))
        return {"reply": "", "error": err}

    # the reply is a little performance: cues out of the text, faces to the
    # cascade (as emojis), sounds/moves to the body, clean words to the voice
    plan = cues.parse(reply)
    if brain_used and not routine_names and CFG.speech_mode in ("soundbyte", "soundboard"):
        plan = cues.condense(
            plan, burst_seconds=CFG.speech_burst,
            prefer_soundboard=CFG.speech_mode == "soundboard")
    voice_error = None
    body.emote(plan.display)
    if not streamed:
        if plan.actions():
            body.run(lambda r: (r.led("green"),
                                cues.perform(r, plan.actions())))
        if speak_on_robot and plan.speech:
            # BMO's actual voice: the reply is flashed into the sound bank
            # and spoken (no chirp; speech starts clean).
            _, voice_error = speech.speak(plan.speech)
        elif not plan.actions():
            # no cue sounds: keep the tone-guessed chirp fallback
            body.run(lambda r: (r.led("green"), mood_chirp(r, reply),
                                faces.blink(r, 2, 0.1)))
    soundboard_spoke = any(kind == "sound" for kind, _ in plan.steps)
    out = {"reply": plan.display, "cues": plan.steps,
           "spoke": speak_on_robot and voice_error is None and
                    bool(plan.speech or soundboard_spoke)}
    if routine_names:
        out["routine"] = routine_names[0]
        out["routines"] = routine_names
    if partial:
        out["partial"] = True
        out["error"] = err
    if voice_error:
        out["voice_error"] = voice_error
    return out


def _compound_prompt(original, residual, assistant_prefix):
    target = residual or original
    authentic = soundboard_voice.suggestions(target)
    if not assistant_prefix and not authentic:
        return None
    lines = ["Original request: " + original]
    if assistant_prefix:
        lines += ["Already answered locally: " + assistant_prefix,
                  "Answer only the unresolved request: " + residual]
    if authentic:
        lines.append(
            "Authentic recorded BMO lines available: " +
            " | ".join(authentic) +
            ". If one naturally answers the request, reply with that exact "
            "line and no [sound:] cue. Otherwise answer normally in at most "
            "3 words with an appropriate stage cue."
        )
    return "\n".join(lines)


def _brain_chat(text, prompt=None, assistant_prefix=""):
    if prompt is None and not assistant_prefix:
        return brain.chat(text)
    return brain.chat(text, prompt=prompt, assistant_prefix=assistant_prefix)


def _streamed_reply(text, *, prompt=None, assistant_prefix="",
                    on_sentence_event=None, defer_speech=False):
    """Streaming pipeline: each completed LLM sentence flows into the
    speech synthesizer while later sentences are still generating — BMO
    starts talking before the reply is done.  Returns (reply, streamed, err).
    """
    unit_queue = queue.Queue()

    def unit_iter():
        while True:
            unit = unit_queue.get()
            if unit is None:
                return
            yield unit

    soundboard_mode = CFG.speech_mode == "soundboard"
    if soundboard_mode:
        # Do not pre-open the generated-speech pipeline. Sentence parsing must
        # first decide whether an authentic stage cue or catalog clip owns the
        # voice; only an unmatched line is allowed to reach synthesis.
        deferred_stop = threading.Event()
        stream_job = {"stop": deferred_stop}
        if defer_speech:
            with _deferred_speech_lock:
                _deferred_speech_stops.add(deferred_stop)
        stream_err = None
    elif defer_speech:
        # A compound turn may already be speaking its immediate local
        # answer. Buffer residual sentences while the model generates, then
        # hand them to SpeechService as soon as that first job is idle.
        deferred_stop = threading.Event()
        stream_job = {"stop": deferred_stop}
        with _deferred_speech_lock:
            _deferred_speech_stops.add(deferred_stop)

        def start_when_idle():
            try:
                active = getattr(speech, "active", lambda: False)
                while active():
                    if deferred_stop.wait(0.05):
                        return
                # Serialize launch with cancellation: after this lock is
                # released, /stop can see the real SpeechService job.
                with _deferred_speech_lock:
                    if deferred_stop.is_set():
                        return
                    actual_job, _ = speech.speak(text, units=unit_iter())
                    _deferred_speech_stops.discard(deferred_stop)
                if deferred_stop.is_set() and actual_job is not None:
                    actual_job["stop"].set()
            finally:
                with _deferred_speech_lock:
                    _deferred_speech_stops.discard(deferred_stop)

        threading.Thread(target=start_when_idle, daemon=True).start()
        stream_err = None
    else:
        stream_job, stream_err = speech.speak(text, units=unit_iter())
    if stream_err is not None:
        try:
            return _brain_chat(text, prompt, assistant_prefix), False, None
        except Exception as e:
            return None, False, str(e)

    # The soundbyte budget applies while streaming too — one owner
    # (cues.BurstBudget) for both reply paths.  Cues fire per sentence, so
    # wiggles and soundbytes land beside the words instead of after the
    # whole generation.
    budget = (cues.BurstBudget(burst_seconds=CFG.speech_burst)
              if CFG.speech_mode in ("soundbyte", "soundboard") else None)
    state = {"faced": False, "steps": [], "sentences": [],
             "voice_started": False, "voice_job": None}

    def start_single_voice(speech_text):
        """Play one tiny catalog-or-fallback line without blocking decode."""
        def start():
            try:
                active = getattr(speech, "active", lambda: False)
                while active():
                    if stream_job["stop"].wait(0.05):
                        return
                with _deferred_speech_lock:
                    if stream_job["stop"].is_set():
                        return
                    job, _ = speech.speak(speech_text)
                    state["voice_job"] = job
                    _deferred_speech_stops.discard(stream_job["stop"])
                if stream_job["stop"].is_set() and job is not None:
                    job["stop"].set()
            finally:
                with _deferred_speech_lock:
                    _deferred_speech_stops.discard(stream_job["stop"])

        active = getattr(speech, "active", lambda: False)
        if defer_speech or active():
            with _deferred_speech_lock:
                _deferred_speech_stops.add(stream_job["stop"])
            threading.Thread(target=start, daemon=True).start()
        else:
            start()

    def on_sentence(sentence):
        state["sentences"].append(sentence)
        if on_sentence_event is not None:
            on_sentence_event(sentence)
        plan = cues.parse(sentence)
        state["steps"].extend(plan.steps)
        body.perform(plan.actions())
        if not state["faced"] and plan.display:
            state["faced"] = True
            body.emote(plan.display)
        speech_text = tts_bank.sanitize_speech_text(plan.speech)
        if not speech_text:
            return
        if soundboard_mode and cues.has_voice_sound(plan.steps):
            return
        exact_catalog_line = (soundboard_mode and
                              soundboard_voice.resolve(speech_text) is not None)
        if budget is not None and not exact_catalog_line:
            speech_text = budget.feed(speech_text)
            if speech_text is None:
                return
        if soundboard_mode:
            if state["voice_started"]:
                return
            state["voice_started"] = True
            start_single_voice(speech_text)
        else:
            unit_queue.put(speech_text)

    try:
        reply = brain.stream(text, on_sentence, prompt=prompt,
                             assistant_prefix=assistant_prefix)
        if CFG.speech_mode == "soundbyte":
            # same guarantee as condense(): the performance always has a
            # soundbyte, even when the model gave no sound cue
            sound = cues.BurstBudget.fallback_sound(state["steps"])
            if sound:
                body.perform([("sound", sound)])
        elif soundboard_mode and not state["voice_started"]:
            sound = cues.BurstBudget.fallback_sound(state["steps"])
            if sound:
                body.perform([("sound", sound)])
        return reply, True, None
    except Exception as e:
        stream_job["stop"].set()
        if state["voice_job"] is not None:
            state["voice_job"]["stop"].set()
        # Once a sentence reached the user, a hidden blocking retry would
        # duplicate or contradict it. Preserve the partial turn and surface
        # the failure so the event stream can mark it recoverable.
        if state["sentences"]:
            return " ".join(state["sentences"]), True, f"stream: {e}"
        try:
            return _brain_chat(text, prompt, assistant_prefix), False, None
        except Exception as e2:
            return None, False, f"stream: {e}; retry: {e2}"
    finally:
        if soundboard_mode:
            if not state["voice_started"]:
                with _deferred_speech_lock:
                    _deferred_speech_stops.discard(stream_job["stop"])
        else:
            unit_queue.put(None)


# ---- HTTP ----------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _reply(self, data, ctype="application/json"):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _json(self, obj):
        self._reply(json.dumps(obj).encode())

    def _ndjson(self, events):
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.send_header("Connection", "close")
        self.end_headers()
        for event in events:
            self.wfile.write(json.dumps(event).encode() + b"\n")
            self.wfile.flush()

    def _robot_json(self, fn):
        """Run fn(robot) under the body lock; shape errors as JSON."""
        if not body.attached:
            return self._json({"error": "body not attached"})
        try:
            return self._json(body.call(fn))
        except Exception as e:
            return self._json({"error": str(e)})

    def do_GET(self):
        if self.path.startswith("/voice/catalog"):
            query = urllib.parse.parse_qs(
                urllib.parse.urlparse(self.path).query).get("q", [""])[0]
            exact = soundboard_voice.resolve(query) if query else None
            return self._json({
                "mode": CFG.speech_mode,
                "count": soundboard_voice.count,
                "trusted_count": soundboard_voice.trusted_count,
                "quarantined_count": soundboard_voice.quarantined_count,
                "pending_review_count": soundboard_voice.pending_review_count,
                "rejected_count": soundboard_voice.rejected_count,
                "query": query,
                "exact": ({"key": exact.get("key"),
                           "label": exact.get("label"),
                           "source_page_url": exact.get("source_page_url"),
                           "verification": exact.get("verification")}
                          if exact else None),
                "suggestions": soundboard_voice.suggestions(query),
            })
        if self.path.startswith("/voice/clip"):
            query = urllib.parse.parse_qs(
                urllib.parse.urlparse(self.path).query).get("text", [""])[0]
            wav = soundboard_voice.synth(query)
            if wav is None:
                return self._json({"error": "no exact authentic clip"})
            return self._reply(wav, "audio/wav")
        if self.path.startswith("/voice/module"):
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            artifact = soundboard_voice.module_artifact(
                query.get("key", [""])[0])
            if artifact is None:
                return self._json(
                    {"error": "unknown, rejected, or invalid soundboard key"})
            return self._reply(artifact["payload"], "application/octet-stream")
        if self.path.startswith("/thinking-sound?"):
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            names = {
                "hum": "thinking-hum.wav",
                "blip-a": "thinking-blip-a.wav",
                "blip-b": "thinking-blip-b.wav",
            }
            filename = names.get(query.get("name", [""])[0])
            path = (REPO_ROOT / "assets" / "bmo-thinking-sounds" / filename
                    if filename else None)
            if path is None or not path.exists():
                return self._json({"error": "unknown thinking sound"})
            return self._reply(path.read_bytes(), "audio/wav")
        if self.path.startswith("/sound-bank-file?"):
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            profile = tts_bank.BANK_PROFILES.get(query.get("profile", [""])[0])
            if profile is None or not profile["path"].exists():
                return self._json({"error": "unknown or unavailable sound bank"})
            data = profile["path"].read_bytes()
            if hashlib.sha256(data).hexdigest() != profile["sha256"]:
                return self._json({"error": "local sound-bank hash mismatch"})
            return self._reply(data, "application/octet-stream")
        if self.path == "/health":
            return self._json({"robot": body.attached, "via": body.via,
                               "esp32": esp32.base_url})
        if self.path == "/miner":
            return self._json(miner.status())
        if self.path == "/tts-bank/status":
            with speech.lock:
                return self._json({**speech.status(), "voices": TTS_VOICES})
        if self.path == "/sounds":
            slots = [{"id": sound_id, **metadata}
                     for sound_id, metadata in sorted(BMO_SOUND_SLOTS.items())]
            return self._json({"bank": BMO_BANK, "slots": slots,
                               "sequences": BMO_SEQUENCES,
                               "profiles": tts_bank.bank_profiles_payload(),
                               "expected_installed_profile": speech.installed_profile})
        if self.path == "/faces":
            anims = {f: fn.__name__.replace("_anim_", "")
                     for f, fn in emote.ANIMS.items()}
            return self._json({"faces": faces.PREVIEW_RECTS,
                               "emoji": faces.EMOJI_FACES,
                               "anims": anims})
        if self.path == "/scan":
            return self._robot_json(
                lambda r: dict(zip(("scan", "rpm"), r.lds_scan())))
        if self.path == "/charger":
            return self._robot_json(lambda r: r.charger())
        page = PAGE_PATH.read_text().replace("__ESP32__", esp32.base_url)
        self._reply(page.encode(), "text/html")

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(n)
        if self.path == "/cmd":
            c = json.loads(raw).get("cmd", "").strip()
            if not c:
                return self._json({"error": "empty command"})
            return self._robot_json(lambda r: {"out": r.cmd(c, timeout=4)})
        if self.path == "/sound-sequence":
            name = json.loads(raw).get("name", "")
            sequence = BMO_SEQUENCES.get(name)
            if sequence is None:
                return self._json({"error": "unknown sound sequence"})
            commands = [f"PlaySound {sound_id}" for sound_id in sequence["ids"]]

            def run(r):
                replies, delays = [], []
                for index, (sound_id, command) in enumerate(
                        zip(sequence["ids"], commands)):
                    replies.append(r.cmd(command, timeout=4))
                    if index + 1 < len(commands):
                        delay = BMO_SOUND_SLOTS[sound_id]["slot_seconds"]
                        delays.append(delay)
                        time.sleep(delay)
                return {"name": name, "commands": commands,
                        "replies": replies, "delays_seconds": delays}
            return self._robot_json(run)
        if self.path == "/tts-bank/speak":
            request = json.loads(raw)
            text = request.get("text", "").strip()
            voice_name = request.get("voice", voice.default_voice)
            if not text:
                return self._json({"error": "empty text"})
            if voice_name not in TTS_VOICES:
                return self._json({"error": "unknown TTS voice"})
            job, error = speech.speak(text, voice_name)
            if error:
                return self._json({"error": error})
            return self._json({"ok": True, "id": job["id"],
                               "state": job["state"]})
        if self.path == "/tts-bank/stop":
            deferred_cancelled = _cancel_deferred_speech()
            error = speech.stop()
            if deferred_cancelled and error == "no TTS job":
                error = None
            return self._json({"error": error} if error else {"ok": True})
        if self.path == "/tts-bank/restore":
            error = speech.restore()
            return self._json({"error": error} if error
                              else {"ok": True, "state": "restoring"})
        if self.path == "/sound-bank-install":
            request = json.loads(raw)
            if request.get("sound_key"):
                return self._json(speech.install_soundboard_module(
                    soundboard_voice, request["sound_key"],
                    request.get("confirmation")))
            return self._json(speech.install_profile(
                request.get("profile", ""), request.get("confirmation")))
        if self.path == "/lidar":
            return self._robot_json(
                lambda r: (r.lidar(raw == b"1"), {"ok": True})[1])
        if self.path == "/tts":
            try:
                text = json.loads(raw).get("text", "").strip()
                if not text:
                    return self._json({"error": "empty text"})
                return self._reply(voice.synth(text), "audio/wav")
            except Exception as e:
                return self._json({"error": str(e)})
        if self.path == "/emote":
            # accepts stage cues and emojis alike, so cues can be tested by
            # hand: face cues become emojis for the cascade, sound/move cues
            # run on the body
            plan = cues.parse(raw.decode(errors="replace"))
            faces_n = len(faces.parse_emojis(plan.display)) or 1
            body.emote(plan.display)
            body.perform(plan.actions())
            return self._json({"ok": True, "faces": faces_n,
                               "cues": plan.steps})
        if self.path == "/ota":
            try:
                return self._json({"out": esp32.ota(raw)})
            except Exception as e:
                return self._json({"error": str(e)})
        if self.path == "/miner/start":
            return self._json(miner.start())
        if self.path == "/miner/stop":
            return self._json(miner.stop())
        if self.path == "/miner/config":
            try:
                return self._json(miner.configure(json.loads(raw or b"{}")))
            except ValueError as e:
                return self._json({"error": str(e)})
        # /chat
        chat_request = json.loads(raw)
        text = chat_request.get("text", "")
        speak = bool(chat_request.get("speak"))
        if "application/x-ndjson" in self.headers.get("Accept", ""):
            return self._ndjson(chat_events(text, speak))
        self._json(chat_turn(text, speak))


def precache_routine_tts():
    """Warm the voice cache with the routine layer's canned replies, so
    instant answers don't wait on synthesis either."""
    texts = (cues.parse(t).speech for t in routines.canned_texts())
    print(f"tts: precached {voice.precache(texts)} routine replies")


if __name__ == "__main__":
    try:
        robot = Robot()
        robot.testmode(True)
        robot.led("backlight_on")
        robot.play("hello")
        body.robot, body.via = robot, "usb"
        print("body: connected over USB")
    except Exception as usb_error:
        try:
            robot = Robot(esp32.hostname)
            if "Software" not in robot.cmd("GetVersion", timeout=5):
                raise RuntimeError("bridge reachable but robot did not answer")
            body.robot, body.via = robot, "bridge"
            print(f"body: connected via ESP32 bridge at {esp32.hostname}")
        except Exception as bridge_error:
            print("body: not attached — usb:", usb_error,
                  "| bridge:", bridge_error)
    ensure_brain()
    ensure_voice()
    miner.autostart()
    threading.Thread(target=precache_routine_tts, daemon=True).start()
    print(f"BMO voice console: http://localhost:{CFG.port}")
    try:
        ThreadingHTTPServer(("0.0.0.0", CFG.port), Handler).serve_forever()
    finally:
        miner.stop()
