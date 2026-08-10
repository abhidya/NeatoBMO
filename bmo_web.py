#!/usr/bin/env python3
"""BMO console: chat, raw commands, sensors/lidar, and ESP32 OTA in one page.

Tabs: Chat (browser mic -> /chat -> OLMoE brain -> reply; body reacts with
sounds/LED/LCD), Console (raw Neato commands via POST /cmd + drive pad),
Sensors (lidar radar via GET /scan, battery via GET /charger), ESP32 (device
page link + OTA upload proxied through POST /ota). All serial access goes
through one Robot instance guarded by rlock.

    python3 bmo_web.py            # robot over USB, brain at 127.0.0.1:8000
Open http://localhost:8485 in Chrome (mic needs Chrome/Edge).
"""
import hashlib
import json
import os
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from neatobmo import Robot
from neatobmo import cues
from neatobmo import emote
from neatobmo import routines
from neatobmo import faces
from neatobmo import tts_bank
from neatobmo.sounds import BMO_BANK, BMO_SEQUENCES, BMO_SOUND_SLOTS, LIVE_SOUND_IDS

BRAIN = os.environ.get("NEATOBMO_BRAIN", "http://127.0.0.1:8000/v1").rstrip("/")
KEY_FILE = os.path.expanduser("~/.neatobmo/coli_api_key")
API_KEY = open(KEY_FILE).read().strip() if os.path.exists(KEY_FILE) else None
PORT = int(os.environ.get("PORT", "8485"))
ESP32 = os.environ.get("NEATOBMO_ESP32", "http://10.0.0.106")

# Stage cues are the persona's "tool calls": the small brain can't do real
# tool calling, so it acts through bracketed cues that neatobmo/cues.py
# parses best-effort (fuzzy names, any bracket style). Emojis still work as
# a fallback face layer, so both vocabularies are offered.
PERSONA = ("You are BMO from Adventure Time: a sweet childlike robot buddy living in "
           "a robot vacuum body. BMO talks in tiny simple words with big feelings, "
           "and sometimes says BMO instead of I. Call the user friend, never human. "
           "SPEAK AT MOST 3 WORDS — your soundboard, dance moves, and faces do the "
           "real talking! "
           "Faces: " + " ".join(f"[{n}]" for n in cues.FACE_NAMES) + ". "
           "Moves: " + " ".join(f"[{n}]" for n in cues.MOVES) + ". "
           "Sounds: " + " ".join(f"[sound:{n}]" for n in cues.SOUND_CUES) + ". "
           "Example: \"You are back! [sound:hello] [happy] [wiggle] "
           "[sound:videogames] [party]\" "
           "Example: \"BMO sad. [sad] [sound:beep] [look]\"")

# "soundbyte" caps brain replies at a ~1.5 s spoken burst (cues.condense)
# and lets the soundboard carry the rest; "full" speaks the whole reply.
# Routines are hand-authored in soundbyte style, so the cap only applies to
# the LLM. NEATOBMO_SPEECH_BURST tunes the burst length in seconds.
SPEECH_MODE = os.environ.get("NEATOBMO_SPEECH", "soundbyte")
SPEECH_BURST = float(os.environ.get("NEATOBMO_SPEECH_BURST",
                                    cues.SPEECH_BURST_SECONDS))

robot = None
body_via = None  # "usb" | "bridge" | None, set at startup for the UI status pill
rlock = threading.Lock()
history = [{"role": "system", "content": PERSONA}]
convo_state = routines.ConvoState()   # multi-turn follow-ups (single-user UI)

REPO_ROOT = Path(__file__).resolve().parent
SOUND_BANK_PROFILES = {
    "bmo": {
        "label": "BMO PCM-only bank",
        "path": REPO_ROOT / "assets/bmo-sound-bank-offline-20260810/DfltSoundLib.BMO.pcm-only.Rev1.0.bin",
        "sha256": "9d3d82d9275c03fa9f2abb163cdfd9393445737999916f6337d2d6b639b51159",
        "confirmation": "INSTALL BMO",
    },
    "original": {
        "label": "Original Neato bank",
        "path": REPO_ROOT / "assets/neato-xv12-sound-capture-20260810/public-reference/DfltSoundLib.Rev1.0.bin",
        "sha256": "d3969779a6a195812d72b6859454de004ea45beefdee6f1b5c50a2632564b64a",
        "confirmation": "RESTORE ORIGINAL",
    },
}
installed_sound_profile = "bmo"

# ---- automatic TTS-to-sound-bank speech ----------------------------------
# Type text -> robot talks.  Long text is packed into ~17 s banks at sentence
# boundaries and burned+spoken chunk by chunk; the last bank stays installed
# (persistent mode — no restore write per utterance).  Validation still gates
# every burn internally, robot access serializes through rlock, and the BMO
# bank is one click away.  espeak parameters mirror bmo_brain_server defaults
# so the voice is identical whether Colibri answers or the local fallback runs.
TTS_VOICES = {
    "bmo-rvc": "BMO (neural clone)",
    "en+f4": "espeak en+f4",
    "en+f2": "espeak en+f2",
    "en+m3": "espeak en+m3",
    "en+m1": "espeak en+m1",
    "en": "espeak en",
}
TTS_DEFAULT_VOICE = "bmo-rvc"
VOICE_SERVER = os.environ.get("NEATOBMO_VOICE", "http://127.0.0.1:8486")
VOICE_VENV_PY = os.path.expanduser("~/.neatobmo/voice-venv/bin/python")
TTS_SPEED = 160
TTS_PITCH = 70
TTS_ACTIVE_STATES = {"synthesizing", "building", "burning", "speaking", "restoring"}
TTS_LOG_PATH = REPO_ROOT / "logs" / "tts-bank-operations.jsonl"
tts_job = None
tts_job_lock = threading.Lock()

# Precached WAVs for the routine layer's canned replies, keyed by
# (sanitized speech text, voice): instant answers should not wait on
# espeak/Colibri either. Filled by a background thread at startup.
_tts_cache = {}


def precache_routine_tts(voice="en+f4"):
    for canned in routines.canned_texts():
        speech = tts_bank.sanitize_speech_text(cues.parse(canned).speech)
        if not speech or (speech, voice) in _tts_cache:
            continue
        try:
            _tts_cache[(speech, voice)] = synthesize_tts(speech, voice)
        except Exception:
            return    # no TTS engine available; cache stays partial
    print(f"tts: precached {len(_tts_cache)} routine replies")


def voice_server_tts(text):
    """Neural BMO voice: Piper prosody -> BMO RVC timbre (tools/bmo_voice_server)."""
    req = urllib.request.Request(
        VOICE_SERVER + "/synth", data=json.dumps({"text": text}).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as resp:
        wav = resp.read()
    if not wav.startswith(b"RIFF"):
        raise RuntimeError("voice server returned a non-WAV response")
    return wav


def synthesize_tts(text, voice=TTS_DEFAULT_VOICE):
    """Neural BMO clone first; Colibri/espeak as explicit choices/fallbacks."""
    text = tts_bank.sanitize_speech_text(text) or text
    if (cached := _tts_cache.get((text, voice))) is not None:
        return cached
    if voice == "bmo-rvc":
        try:
            wav = voice_server_tts(text)
            _tts_cache[(text, voice)] = wav
            return wav
        except (urllib.error.URLError, OSError, RuntimeError) as exc:
            print("voice server unavailable, falling back to espeak:", exc)
            voice = "en+f4"
    try:
        return colibri_tts(text, voice)
    except (urllib.error.URLError, OSError, RuntimeError):
        pass
    with tempfile.NamedTemporaryFile(suffix=".wav") as handle:
        proc = subprocess.run(
            ["espeak-ng", "-v", voice, "-s", str(TTS_SPEED),
             "-p", str(TTS_PITCH), "-w", handle.name, text],
            capture_output=True, timeout=30)
        if proc.returncode:
            raise RuntimeError(proc.stderr.decode(errors="replace").strip()
                               or "local espeak-ng failed")
        wav = Path(handle.name).read_bytes()
    if not wav.startswith(b"RIFF"):
        raise RuntimeError("local espeak-ng did not produce a WAV")
    return wav


def tts_log(job, message):
    entry = {"ts": round(time.time(), 3), "job": job["id"], "message": message}
    job["log"].append(entry)
    try:
        TTS_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(TTS_LOG_PATH, "a") as handle:
            handle.write(json.dumps(entry) + "\n")
    except OSError:
        pass


def tts_active():
    return tts_job is not None and tts_job["state"] in TTS_ACTIVE_STATES


def tts_status_payload():
    if tts_job is None:
        return {"state": "idle", "voices": TTS_VOICES,
                "installed_profile": installed_sound_profile}
    job = tts_job
    progress = job["progress"].snapshot()
    return {
        "id": job["id"],
        "state": job["state"],
        "text": job["text"],
        "voice": job["voice"],
        "chunks": job.get("chunk_summaries", []),
        "progress": progress,
        "installed_profile": installed_sound_profile,
        "installed_bank_sha256": job.get("installed_sha"),
        "error": job.get("error"),
        "log": job["log"][-40:],
        "voices": TTS_VOICES,
    }


def tts_capacities(baseline):
    records = {r.sound_id: r for r in tts_bank.record_ranges_from_bytes(baseline)}
    return [(sid, records[sid].sample_count) for sid in tts_bank.SLOT_SEQUENCE]


def tts_run_speak(job):
    """Background thread: synthesize -> chunk -> burn+speak each chunk.

    The finished bank persists; BMO restore is an explicit action.
    """
    global installed_sound_profile
    try:
        baseline = SOUND_BANK_PROFILES["bmo"]["path"].read_bytes()
        if hashlib.sha256(baseline).hexdigest() != tts_bank.BMO_BANK_SHA256:
            raise RuntimeError("persistent BMO artifact hash mismatch; refusing")
        capacities = tts_capacities(baseline)
        job["state"] = "synthesizing"
        synth = lambda t: tts_bank.prepare_speech_pcm(synthesize_tts(t, job["voice"]))
        chunks = tts_bank.plan_speech_chunks(job["text"], synth, capacities)
        job["chunk_summaries"] = [{
            "index": i, "text": c["text"],
            "seconds": round(len(c["samples"]) / tts_bank.PCM_SAMPLE_RATE, 3),
            "segments": len(c["segments"]),
        } for i, c in enumerate(chunks)]
        tts_log(job, f"planned {len(chunks)} chunk(s) for "
                     f"{sum(s['seconds'] for s in job['chunk_summaries']):.1f}s of speech")
        burner = tts_bank.BankBurner(robot, log=lambda m: tts_log(job, m))
        with rlock:
            report = tts_bank.speak_chunks_operation(
                burner, baseline, chunks, "silence",
                progress=job["progress"], stop_event=job["stop"])
        job["report"] = report
        job["installed_sha"] = report.get("installed_bank_sha256")
        if job["installed_sha"]:
            installed_sound_profile = "tts"
        job["state"] = job["progress"].state
        tts_log(job, f"speech finished: state={job['state']}")
    except Exception as exc:
        job["error"] = str(exc)
        job["state"] = "error"
        tts_log(job, f"speech failed: {exc}")


def tts_start_speak(text, voice):
    """Create and launch a speech job; returns (job, error)."""
    global tts_job
    if robot is None:
        return None, "body not attached"
    if tts_active():
        return None, "already speaking; stop first or wait"
    # Emojis drive the LCD face, not the speaker: espeak reads them out as
    # long Unicode names and wrecks the speech, so strip them here.
    text = tts_bank.sanitize_speech_text(text)
    if not text:
        return None, "nothing speakable after removing emoji/symbols"
    job = {
        "id": f"tts-{int(time.time())}",
        "state": "synthesizing",
        "text": text,
        "voice": voice,
        "progress": tts_bank.PlaybackProgress(),
        "stop": threading.Event(),
        "log": [],
    }
    tts_job = job
    tts_log(job, f"speak: voice={voice} text={text!r}")
    threading.Thread(target=tts_run_speak, args=(job,), daemon=True).start()
    return job, None


def tts_run_restore(job):
    global installed_sound_profile
    burner = tts_bank.BankBurner(robot, log=lambda m: tts_log(job, m))
    try:
        with rlock:
            job["state"] = "restoring"
            result = burner.restore_bank(
                SOUND_BANK_PROFILES["bmo"]["path"],
                tts_bank.BMO_BANK_SHA256, "persistent BMO bank")
        installed_sound_profile = "bmo"
        job["installed_sha"] = None
        job["state"] = "restored"
        tts_log(job, f"BMO restore verified: {result['accepted_ids']}")
    except Exception as exc:
        job["error"] = str(exc)
        job["state"] = "error"
        tts_log(job, f"BMO restore failed: {exc}")


def sound_bank_profiles():
    return {
        key: {
            "label": profile["label"],
            "sha256": profile["sha256"],
            "bytes": profile["path"].stat().st_size if profile["path"].exists() else None,
            "available": profile["path"].exists(),
            "confirmation": profile["confirmation"],
            "download": f"/sound-bank-file?profile={key}",
        }
        for key, profile in SOUND_BANK_PROFILES.items()
    }


def brain_chat(text):
    history.append({"role": "user", "content": text})
    req = urllib.request.Request(
        BRAIN + "/chat/completions",
        data=json.dumps({"model": "olmoe", "messages": history[-9:]}).encode(),
        headers={"Content-Type": "application/json",
                 **({"Authorization": f"Bearer {API_KEY}"} if API_KEY else {})})
    with urllib.request.urlopen(req, timeout=300) as resp:
        reply = json.loads(resp.read())["choices"][0]["message"]["content"]
    history.append({"role": "assistant", "content": reply})
    return reply


# Colibri brain auto-start: same engine/snapshot the bmo-brain launch config
# uses.  Only attempted when BRAIN points at this machine and the engine
# volume is mounted; chat degrades gracefully while the model loads.
BRAIN_ENGINE = os.environ.get("NEATOBMO_BRAIN_ENGINE",
                              "/Volumes/2TB/colibri-v1.5.0-macos-arm64/olmoe")
BRAIN_SNAP = os.environ.get("NEATOBMO_BRAIN_SNAP", "/Volumes/2TB/models/olmoe-snap")


def brain_listening(timeout=1.5):
    parsed = urllib.parse.urlparse(BRAIN)
    try:
        import socket
        with socket.create_connection((parsed.hostname, parsed.port or 80),
                                      timeout=timeout):
            return True
    except OSError:
        return False


def ensure_voice():
    """Start the neural BMO voice server if its venv and models exist."""
    parsed = urllib.parse.urlparse(VOICE_SERVER)
    try:
        import socket
        with socket.create_connection((parsed.hostname, parsed.port or 80),
                                      timeout=1.0):
            print("voice: BMO neural server already running at", VOICE_SERVER)
            return
    except OSError:
        pass
    model = os.path.expanduser(
        "~/.neatobmo/voices/bmo-rvc/BmoAdventureTime_115e_3220s.pth")
    if not (os.path.exists(VOICE_VENV_PY) and os.path.exists(model)):
        print("voice: neural models not installed — using espeak fallback")
        return
    log_path = REPO_ROOT / "logs" / "bmo-voice.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.Popen(
        [VOICE_VENV_PY, str(REPO_ROOT / "tools" / "bmo_voice_server.py"),
         "--port", str(parsed.port or 8486)],
        stdout=open(log_path, "a"), stderr=subprocess.STDOUT)
    print(f"voice: starting BMO neural server — log at {log_path}")


def ensure_brain():
    """Start the Colibri brain server if localhost should have one but doesn't."""
    parsed = urllib.parse.urlparse(BRAIN)
    if parsed.hostname not in ("127.0.0.1", "localhost"):
        return
    if brain_listening():
        print("brain: Colibri already running at", BRAIN)
        return
    if not os.path.exists(BRAIN_ENGINE):
        print(f"brain: Colibri engine not found at {BRAIN_ENGINE} — chat uses "
              "espeak-only fallback (mount the 2TB volume to enable OLMoE)")
        return
    log_path = REPO_ROOT / "logs" / "bmo-brain.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.Popen(
        [sys.executable, str(REPO_ROOT / "bmo_brain_server.py"),
         "--engine", BRAIN_ENGINE, "--snap", BRAIN_SNAP,
         "--port", str(parsed.port or 8000)],
        stdout=open(log_path, "a"), stderr=subprocess.STDOUT)
    print(f"brain: starting Colibri (OLMoE) — log at {log_path}; "
          "chat answers once the model finishes loading")


def colibri_tts(text, voice="en+f4"):
    """Ask the Colibri server for the WAV that the Neato will play."""
    req = urllib.request.Request(
        BRAIN + "/audio/speech",
        data=json.dumps({"model": "espeak-ng", "voice": voice,
                         "input": text, "response_format": "wav"}).encode(),
        headers={"Content-Type": "application/json",
                 **({"Authorization": f"Bearer {API_KEY}"} if API_KEY else {})})
    with urllib.request.urlopen(req, timeout=45) as resp:
        wav = resp.read()
    if not wav.startswith(b"RIFF"):
        raise RuntimeError("Colibri TTS returned a non-WAV response")
    return wav


def esp32_play_wav(wav):
    """Send a Colibri WAV to the ESP32, which relays it over Neato USB."""
    req = urllib.request.Request(
        ESP32 + "/speak",
        data=wav,
        headers={"Content-Type": "audio/wav"},
    )
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            reply = resp.read().decode(errors="replace").strip()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace").strip()
        raise RuntimeError(detail or f"ESP32 voice relay returned HTTP {exc.code}") from exc
    if reply != "OK":
        raise RuntimeError(f"unexpected ESP32 voice response: {reply or 'empty'}")


def chirp_for(reply):
    """Pick a canned Neato sound that matches the reply's tone (chirp-speak:
    the robot can't say words over USB, but it can vocalize like R2-D2)."""
    t = reply.lower()
    if "?" in reply:
        return "curious"
    if any(w in t for w in ("thank", "love", "friend")):
        return "grateful"
    if any(w in t for w in ("sad", "sorry", "oh no")):
        return "sad"
    if "!" in reply:
        return "happy"
    return "hello"


def emote_react(reply):
    """Fire-and-forget LCD face cascade: prefer the ESP32, fall back to USB.

    Both paths draw the identical faces — the ESP32 runs faces.c, the USB
    fallback runs neatobmo/emote.py (a 1:1 port of the same tables).
    """
    def push():
        try:
            req = urllib.request.Request(ESP32 + "/emote", data=reply.encode())
            urllib.request.urlopen(req, timeout=5).read()
            return
        except Exception:
            pass
        if robot is not None:
            try:
                with rlock:
                    emote.cascade(robot, reply)
            except Exception:
                pass
    threading.Thread(target=push, daemon=True).start()


def body(fn):
    """Run a robot action if the body is attached; never crash the chat."""
    if robot is None:
        return
    def run():
        try:
            with rlock:
                fn()
        except Exception:
            pass
    threading.Thread(target=run, daemon=True).start()


PAGE = """<!doctype html>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light only">
<title>BMO</title>
<style>
 @import url('https://fonts.googleapis.com/css2?family=Fredoka:wght@400;500;600;700&family=VT323&display=swap');
 /* BMO body palette: teal shell, dark-teal bezels, pale-green screen,
    candy buttons (yellow / red / blue / green) like BMO's front panel. */
 :root{--bmo:#5fc4b2;--bmo-deep:#48a996;--bezel:#1f5d51;--ink:#0c3b31;
   --screen:#d8f3cd;--screen-ink:#1a4a35;--crt:#0d2b22;--phos:#9df5b8;
   --cream:#fdf8e7;--yellow:#ffd23e;--red:#f2635a;--blue:#4d96ff;--green:#79c74f;
   --shadow:rgba(12,59,49,.35)}
 *{box-sizing:border-box}
 body{margin:0;color:var(--ink);
      font:16px/1.4 Fredoka,ui-rounded,'Trebuchet MS',-apple-system,sans-serif;
      background:linear-gradient(180deg,#6dcfbc,#57bda9 55%,#4bb09c);
      display:flex;flex-direction:column;height:100vh;height:100dvh;align-items:center}
 ::-webkit-scrollbar{width:8px;height:8px}
 ::-webkit-scrollbar-thumb{background:var(--bezel);border-radius:4px}
 ::-webkit-scrollbar-track{background:transparent}
 #tabs{display:flex;gap:6px;width:min(680px,94vw);padding:12px 2px 0;overflow-x:auto;
       scrollbar-width:none;flex:none}
 #tabs::-webkit-scrollbar{display:none}
 .tab{flex:1 0 auto;text-align:center;padding:8px 14px;border-radius:14px 14px 0 0;cursor:pointer;
      background:var(--bmo-deep);border:3px solid var(--bezel);border-bottom:0;color:#eafff7;
      font-weight:600;font-size:14px;white-space:nowrap;user-select:none;
      transition:filter .15s,background .15s;box-shadow:inset 0 3px 0 rgba(255,255,255,.14)}
 .tab:hover{filter:brightness(1.08)}
 .tab.on{background:var(--cream);color:var(--ink);box-shadow:inset 0 4px 0 var(--tabc,var(--yellow))}
 #tabs .tab:nth-child(1){--tabc:var(--yellow)}
 #tabs .tab:nth-child(2){--tabc:var(--red)}
 #tabs .tab:nth-child(3){--tabc:var(--blue)}
 #tabs .tab:nth-child(4){--tabc:var(--green)}
 #tabs .tab:nth-child(5){--tabc:#c792ea}
 #tabs .tab:nth-child(6){--tabc:#ff9f43}
 .pane{display:none;flex:1;flex-direction:column;align-items:center;width:100%;min-height:0}
 .pane.on{display:flex}
 /* header avatar: a mini BMO face screen playing the stylized sprite
    faces (web-only art — the real LCD draws full-span grid bands) */
 #face{width:176px;height:88px;margin:14px 0 4px;flex:none;
       image-rendering:pixelated;background:#d8f3cd;
       border:5px solid var(--bezel);border-radius:14px;
       box-shadow:0 4px 0 var(--shadow),inset 0 0 0 transparent}
 #conn{font-size:12px;padding:3px 12px;border-radius:99px;border:2px solid var(--bezel);
       background:var(--cream);color:var(--ink);margin:4px 0;font-weight:600}
 #conn::before{content:'●';margin-right:6px;color:var(--red)}
 #conn.ok::before{color:#2f9e44}
 #status{opacity:.85;font-size:13px;margin:4px 0 8px;min-height:1.2em;font-weight:500}
 /* the chat log is BMO's pale-green screen */
 #log{flex:1;overflow-y:auto;width:min(680px,94vw);padding:12px;scroll-behavior:smooth;
      background:var(--screen);border:6px solid var(--bezel);border-radius:18px;
      color:var(--screen-ink);
      box-shadow:inset 0 4px 14px rgba(12,59,49,.28),0 4px 0 var(--shadow);
      background-image:repeating-linear-gradient(0deg,rgba(26,74,53,.05) 0 1px,transparent 1px 3px)}
 #log:empty::before{content:'Say hi — BMO wants to play! 🎮';display:block;text-align:center;
       opacity:.55;padding:36px 0;font-size:15px;font-weight:500}
 .msg{margin:7px 0;padding:9px 14px;border-radius:16px;max-width:80%;line-height:1.35;
      width:fit-content;animation:pop .18s ease-out;border:2px solid var(--bezel)}
 @keyframes pop{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}
 .you{background:var(--bezel);color:#eafff7;margin-left:auto;border-bottom-right-radius:5px}
 .bmo{background:#fff;border-bottom-left-radius:5px;box-shadow:0 2px 0 rgba(12,59,49,.15)}
 .think span{display:inline-block;width:7px;height:7px;margin:0 2px;border-radius:50%;
      background:var(--bmo-deep);animation:blip 1s infinite}
 .think span:nth-child(2){animation-delay:.15s}.think span:nth-child(3){animation-delay:.3s}
 @keyframes blip{0%,60%,100%{opacity:.25;transform:none}30%{opacity:1;transform:translateY(-3px)}}
 /* cartridge slot under the screen, like BMO's game slot */
 .slot{width:150px;height:10px;border-radius:6px;background:var(--bezel);margin:10px 0 0;flex:none;
       box-shadow:inset 0 2px 4px rgba(0,0,0,.45)}
 #bar{display:flex;gap:10px;width:min(680px,94vw);align-items:center;
      padding:12px 12px calc(12px + env(safe-area-inset-bottom))}
 #txt{flex:1;min-width:0;padding:11px 14px;border-radius:16px;border:3px solid var(--bezel);
      background:#fff;color:var(--ink);font-size:16px}
 input,textarea,select{font:inherit;transition:border-color .15s,box-shadow .15s}
 input:focus,textarea:focus,select:focus{outline:none;border-color:#0f8a72;
      box-shadow:0 0 0 3px rgba(255,210,62,.55)}
 /* chunky pressable buttons, BMO front-panel style */
 button{border:3px solid var(--bezel);border-radius:14px;padding:10px 16px;font:inherit;
      font-weight:600;font-size:16px;cursor:pointer;background:var(--yellow);color:#4a3600;
      box-shadow:0 4px 0 var(--bezel);
      transition:filter .15s,transform .06s,box-shadow .06s,opacity .15s}
 button:hover{filter:brightness(1.07)}
 button:active{transform:translateY(3px);box-shadow:0 1px 0 var(--bezel)}
 button:disabled{opacity:.55;cursor:default;transform:none;filter:none}
 button:focus-visible{outline:3px solid #fff;outline-offset:2px}
 #mic{background:var(--red);color:#fff;border-radius:50%;width:50px;height:50px;padding:0;flex:none}
 #sendbtn{border-radius:50%;width:50px;height:50px;padding:0;flex:none}
 #voice{background:var(--blue);color:#fff;border:3px solid var(--bezel);border-radius:12px;
        box-shadow:0 3px 0 var(--bezel);font-weight:600}
 #mic.listening{animation:pulse 1.2s infinite}
 #wake{background:var(--yellow);color:var(--ink);border-radius:50%;width:50px;height:50px;
       padding:0;flex:none;opacity:.65}
 #wake.on{opacity:1;animation:pulse 2.2s infinite}
 @keyframes pulse{50%{box-shadow:0 4px 0 var(--bezel),0 0 0 9px rgba(242,99,90,.28)}}
 .mini{padding:6px 12px;font-size:13px;background:var(--cream);color:var(--ink);
       border-radius:12px;box-shadow:0 3px 0 var(--bezel)}
 .mini.playing{background:var(--green);color:#12400a}
 #quick{width:min(680px,94vw);padding:8px 0;display:flex;flex-wrap:wrap;gap:6px}
 #drive{width:min(680px,94vw);padding:8px 0;text-align:center}
 #drive .mini{width:48px;height:42px;padding:0;background:var(--green);color:#12400a;
       font-size:15px;margin:2px}
 #drive>button:nth-of-type(2){background:var(--red);color:#fff}
 /* console: BMO's little CRT */
 #clog{flex:1;overflow-y:auto;width:min(680px,94vw);padding:10px 12px;
       font:17px/1.25 VT323,ui-monospace,monospace;letter-spacing:.3px;
       white-space:pre-wrap;word-break:break-all;background:var(--crt);color:var(--phos);
       border:6px solid var(--bezel);border-radius:16px;text-shadow:0 0 5px rgba(157,245,184,.35);
       box-shadow:inset 0 4px 14px rgba(0,0,0,.5),0 4px 0 var(--shadow);
       background-image:repeating-linear-gradient(0deg,rgba(255,255,255,.03) 0 1px,transparent 1px 3px)}
 #crow{display:flex;gap:8px;width:min(680px,94vw);padding:12px}
 #cmd{flex:1;min-width:0;padding:10px 14px;border-radius:14px;border:3px solid var(--bezel);
      background:var(--crt);color:var(--phos);font:17px VT323,ui-monospace,monospace}
 #hud{padding:8px;font-size:14px;font-weight:500}
 .val{color:#083f33;font-weight:700}
 canvas{flex:1;max-width:min(680px,94vw);min-height:0}
 #c{border:6px solid var(--bezel);border-radius:16px;background:#0e3b35;
    box-shadow:0 4px 0 var(--shadow)}
 #batt{width:min(680px,94vw);padding:8px;font-size:14px;line-height:1.6;font-weight:500}
 .card{width:min(680px,94vw);margin:12px 0;padding:16px;background:var(--cream);
       border:4px solid var(--bezel);border-radius:18px;line-height:1.6;
       overflow-wrap:anywhere;box-shadow:0 4px 0 var(--shadow)}
 .card b{color:#0b6e5d}
 #p-sounds{overflow-y:auto}
 #soundgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:10px;
            width:min(680px,94vw);padding:0 0 16px}
 .soundcard{padding:12px;background:var(--cream);border:3px solid var(--bezel);border-radius:16px;
            transition:transform .12s,box-shadow .12s;box-shadow:0 3px 0 var(--shadow)}
 .soundcard:hover{transform:translateY(-2px);box-shadow:0 5px 0 var(--shadow)}
 .soundcard h3{margin:0 0 4px;color:#0b6e5d;font-size:16px}
 #ttsbar{height:10px;border-radius:6px;background:#e7dfc2;margin:8px 0 2px;overflow:hidden;
         display:none;border:2px solid var(--bezel)}
 #ttsbar.on{display:block}
 #ttsbar i{display:block;height:100%;width:0;background:var(--green);transition:width .4s ease}
 .soundmeta{opacity:.8;font-size:12px;line-height:1.45;margin:5px 0 9px}
 #soundseq{display:flex;flex-wrap:wrap;gap:6px;width:min(680px,94vw);padding:0 0 10px}
 a{color:#0b6e5d;font-weight:600}
 input[type=file]{color:var(--ink)}
 #facepanel{display:none;position:fixed;top:12px;right:12px;z-index:50;width:min(320px,90vw);
       padding:14px;background:var(--cream);border:4px solid var(--bezel);border-radius:18px;
       box-shadow:0 5px 0 var(--shadow)}
 #facepanel.on{display:block}
 #fphead{display:flex;justify-content:space-between;align-items:center;margin-bottom:8px}
 #lcd{display:block;width:100%;max-width:none;flex:none;image-rendering:pixelated;
      background:#cdd5c0;border:3px solid var(--bezel);border-radius:8px}
 #egrid{display:flex;flex-wrap:wrap;gap:4px;margin:8px 0}
 #erow{display:flex;gap:6px}
 #etxt{flex:1;min-width:0;padding:8px 10px;border-radius:12px;border:3px solid var(--bezel);
       background:#fff;color:var(--ink);font-size:16px}
 #ttsoplog{max-height:160px;overflow-y:auto;font:15px/1.25 VT323,ui-monospace,monospace;
       white-space:pre-wrap;word-break:break-all;background:var(--crt);color:var(--phos);
       border:3px solid var(--bezel);border-radius:12px;padding:8px 10px}
 #ttsoplog:empty{display:none}
 :root{color-scheme:light only}
 @media(max-width:480px){
  #bar{gap:6px;padding:10px 8px calc(10px + env(safe-area-inset-bottom))}
  #voice{font-size:12px;padding:6px 4px;max-width:29vw}
  #mic,#sendbtn{width:44px;height:44px}
  #face{font-size:52px}}
</style>
<div id="tabs">
 <div class="tab on" data-p="chat" onclick="show('chat')">💬 Chat</div>
 <div class="tab" data-p="sounds" onclick="show('sounds');loadSounds()">🎵 Sounds</div>
 <div class="tab" data-p="tts" onclick="show('tts');ttsPoll()">🗣 TTS</div>
 <div class="tab" data-p="console" onclick="show('console')">⌨️ Console</div>
 <div class="tab" data-p="sensors" onclick="show('sensors')">📡 Sensors</div>
 <div class="tab" data-p="esp32" onclick="show('esp32')">🔧 ESP32</div>
</div>
<div class="pane on" id="p-chat">
<canvas id="face" width="128" height="64"></canvas>
<div id="conn">checking body…</div>
<div id="status">BMO · voice: espeak-ng → sound-bank speech</div>
<div id="log"></div>
<div class="slot" title="insert game cartridge"></div>
<div id="bar">
  <button id="mic" title="hold to talk">🎤</button>
  <button id="wake" title="wake word: say 'hey BMO'">👂</button>
  <select id="voice" class="mini" title="where BMO's voice plays">
   <option value="off">🔇 muted</option>
   <option value="local">🔊 this device</option>
   <option value="robot" title="BMO speaks replies out loud through the sound bank">🤖 robot</option>
  </select>
  <input id="txt" placeholder="say something to BMO…">
  <button id="sendbtn" onclick="sendTxt()">➤</button>
</div>
</div>
<div class="pane" id="p-sounds">
 <div class="card" id="soundbank">Loading installed BMO sound bank…</div>
 <div class="card" id="soundflash">
  <b>Sound-bank installation</b><br>
  <span class="soundmeta">Flash only the two locally validated exact images. Keep the robot powered and connected.</span><br>
  <select id="bankprofile" class="mini"><option value="bmo">BMO bank</option><option value="original">Original Neato bank</option></select>
  <input id="bankconfirm" placeholder="type INSTALL BMO" style="margin:6px;padding:7px 10px;border-radius:10px;border:3px solid #1f5d51;background:#fff;color:#0c3b31">
  <button class="mini" onclick="installBank()">Write bank</button>
  <div id="bankmsg" class="soundmeta"></div>
  <a href="/sound-bank-file?profile=bmo">download BMO</a> ·
  <a href="/sound-bank-file?profile=original">download original</a>
 </div>
 <div id="soundseq"></div>
 <div id="soundgrid"></div>
</div>
<div class="pane" id="p-tts" style="overflow-y:auto">
 <div class="card"><b>🗣 BMO speaks</b><br>
  <span class="soundmeta">Type anything and BMO says it out loud. Long text is split at
  sentence boundaries into ≈17 s chunks and spoken back to back — each chunk is one
  sound-flash write, and the speech stays installed until you bring the BMO sounds back.</span>
  <textarea id="ttstext" rows="3" placeholder="what should BMO say?"
   style="width:100%;box-sizing:border-box;margin:8px 0;padding:10px;border-radius:12px;border:3px solid #1f5d51;background:#fff;color:#0c3b31;font-size:16px"></textarea>
  <div style="display:flex;gap:8px;align-items:center">
   <select id="ttsvoice" class="mini"></select>
   <button onclick="ttsSpeak()" style="flex:1">🗣 Speak</button>
   <button class="mini" onclick="ttsStop()">■ Stop</button>
  </div>
  <div id="ttsprevmsg" class="soundmeta"></div>
 </div>
 <div class="card">
  <div id="ttsprogress" class="soundmeta">no speech yet</div>
  <div id="ttsbar"><i></i></div>
  <div id="ttschunks" class="soundmeta"></div>
  <div style="margin-top:8px">
   <button class="mini" onclick="ttsRestore()">🎵 Bring back BMO sounds</button>
   <span class="soundmeta" id="ttsbank"></span>
  </div>
  <pre id="ttsoplog"></pre>
 </div>
</div>
<div class="pane" id="p-console">
<div id="quick">
 <button class="mini" onclick="cmd('GetVersion')">GetVersion</button>
 <button class="mini" onclick="cmd('GetCharger')">GetCharger</button>
 <button class="mini" onclick="cmd('TestMode On')">TestMode On</button>
 <button class="mini" onclick="cmd('TestMode Off')">TestMode Off</button>
 <button class="mini" onclick="cmd('GetAnalogSensors')">Analog</button>
 <button class="mini" onclick="cmd('GetDigitalSensors')">Digital</button>
 <button class="mini" onclick="playSequence('bmo_video_games_burst')">BMO Video Games</button>
 <button class="mini" onclick="cmd('Help')">Help</button>
 <button class="mini" onclick="toggleFaces()">Faces 🙂</button>
</div>
<div id="clog"></div>
<div id="drive">
 <div><button class="mini" onclick="drive(200,200)">▲</button></div>
 <button class="mini" onclick="drive(-80,80)">◀</button>
 <button class="mini" onclick="cmd('SetMotor LWheelDist 0 RWheelDist 0 Speed 1')">■</button>
 <button class="mini" onclick="drive(80,-80)">▶</button>
 <div><button class="mini" onclick="drive(-200,-200)">▼</button></div>
</div>
<div id="crow"><input id="cmd" placeholder="type any Neato command, Enter to send">
 <button onclick="sendCmd()">➤</button></div>
</div>
<div class="pane" id="p-sensors">
<div id="hud">RPM <span class="val" id="rpm">-</span> · points <span class="val" id="npts">-</span>
 <button class="mini" onclick="lidar(true)">Lidar ON</button>
 <button class="mini" onclick="lidar(false)">Lidar OFF</button></div>
<canvas id="c" width="900" height="900"></canvas>
<div id="batt">battery: <span class="val" id="battv">-</span>
 <button class="mini" onclick="charger()">refresh</button></div>
</div>
<div class="pane" id="p-esp32">
<div class="card"><b>ESP32 body board</b><br>
 Device page: <a href="__ESP32__" target="_blank">__ESP32__</a> (raw serial bridge over WebSocket /ws)</div>
<div class="card"><b>OTA firmware update</b><br>
 Upload a firmware .bin — it is pushed to the ESP32 at __ESP32__/ota.<br><br>
 <input type="file" id="fw" accept=".bin">
 <button class="mini" onclick="ota()">Upload</button>
 <div id="otamsg" style="opacity:.8;font-size:13px;margin-top:6px"></div></div>
</div>
<div id="facepanel">
 <div id="fphead"><b>Faces</b><button class="mini" onclick="toggleFaces()">✕</button></div>
 <canvas id="lcd" width="128" height="64"></canvas>
 <div id="egrid"></div>
 <div id="erow">
  <input id="etxt" placeholder="😊😢🎉 or any text…">
  <button class="mini" onclick="playEmoteInput()">▶ play</button>
 </div>
</div>
<script>
function show(p){document.querySelectorAll('.tab').forEach(t=>t.classList.toggle('on',t.dataset.p===p));
 document.querySelectorAll('.pane').forEach(d=>d.classList.toggle('on',d.id==='p-'+p));}
const log=document.getElementById('log');
function add(cls,text){const d=document.createElement('div');d.className='msg '+cls;
 d.textContent=text;log.appendChild(d);log.scrollTop=log.scrollHeight;}
const STATUS_DEFAULT='BMO · voice: espeak-ng → sound-bank speech';
// body connection pill
async function health(){const c=document.getElementById('conn');
 try{const j=await(await fetch('/health')).json();
  c.classList.toggle('ok',!!j.robot);
  c.textContent=j.robot?('body connected'+(j.via?' · '+j.via:'')):'body not attached';
 }catch(e){c.classList.remove('ok');c.textContent='console offline';}}
health();setInterval(health,5000);
function thinkBubble(){const d=document.createElement('div');d.className='msg bmo think';
 d.innerHTML='<span></span><span></span><span></span>';
 log.appendChild(d);log.scrollTop=log.scrollHeight;return d;}
// voice output selector (persisted)
const voiceSel=document.getElementById('voice');
try{voiceSel.value=localStorage.getItem('bmoVoice')||'local';}catch(e){voiceSel.value='local';}
if(!voiceSel.value)voiceSel.value='local';
voiceSel.onchange=()=>{try{localStorage.setItem('bmoVoice',voiceSel.value);}catch(e){}};
async function speakLocal(text){
 try{
  const r=await fetch('/tts',{method:'POST',body:JSON.stringify({text})});
  if(!r.ok||!(r.headers.get('Content-Type')||'').includes('audio'))throw new Error('tts failed');
  const a=new Audio(URL.createObjectURL(await r.blob()));
  await a.play();
 }catch(e){
  try{speechSynthesis.speak(new SpeechSynthesisUtterance(text));}catch(e2){}
 }
}
let pending=false;
async function send(text){
 if(pending)return;
 pending=true;
 const st=document.getElementById('status'),txtEl=document.getElementById('txt'),
       btn=document.getElementById('sendbtn'),t0=Date.now();
 txtEl.disabled=true;btn.disabled=true;
 st.textContent='thinking… 0s';
 const tick=setInterval(()=>{st.textContent='thinking… '+Math.floor((Date.now()-t0)/1000)+'s';},1000);
 add('you',text);faceThink();
 const think=thinkBubble();
 try{
  const r=await fetch('/chat',{method:'POST',body:JSON.stringify({text,
   speak:voiceSel.value==='robot'})});
  const j=await r.json();
  clearInterval(tick);think.remove();
  add('bmo',j.reply);faceCascade(j.reply);
  st.textContent=j.voice_error
   ? 'BMO · robot voice: '+j.voice_error
   : STATUS_DEFAULT;
  try{if(facesOpen())previewOnly(j.reply);}catch(e){}
  if(voiceSel.value==='local')speakLocal(j.reply);
 }catch(e){
  clearInterval(tick);think.remove();
  faceSad();add('bmo','(brain unreachable)');
  st.textContent=STATUS_DEFAULT;
 }finally{
  clearInterval(tick);pending=false;
  txtEl.disabled=false;btn.disabled=false;txtEl.focus();
 }
}
function sendTxt(){const t=document.getElementById('txt');if(t.value.trim()){send(t.value.trim());t.value='';}}
document.getElementById('txt').addEventListener('keydown',e=>{if(e.key==='Enter')sendTxt();});
// speech input (Chrome/Edge) + "hey BMO" wake word
const SR=window.SpeechRecognition||window.webkitSpeechRecognition;
const mic=document.getElementById('mic'),wakeBtn=document.getElementById('wake');
if(SR){const rec=new SR();rec.lang='en-US';rec.interimResults=false;
 rec.onresult=e=>{send(e.results[0][0].transcript);};
 rec.onend=()=>{mic.classList.remove('listening');wakeResume();};
 mic.onclick=()=>{if(mic.classList.contains('listening')){rec.stop();}
  else{wakePause();mic.classList.add('listening');rec.start();}};
 // wake word: continuous background recognition; "hey BMO" (or beemo/bee mo)
 // alone chirps + opens the mic, "hey BMO <command>" sends straight away
 const WAKE=/\\b(?:hey|okay|ok|yo)?[,!.\\s]*(?:b[e]{1,2}[\\s-]?mo|bmo)\\b/i;
 let wakeOn=false;const wrec=new SR();
 wrec.lang='en-US';wrec.continuous=true;wrec.interimResults=false;
 wrec.onresult=e=>{
  const t=e.results[e.results.length-1][0].transcript.trim();
  const m=t.match(WAKE);if(!m)return;
  const cmd=t.slice(m.index+m[0].length).replace(/^[,!.\\s]+/,'').trim();
  try{wrec.stop();}catch(_){}
  if(cmd){send(cmd);}
  else{ // chirp on the robot + open the mic for the command
   try{fetch('/emote',{method:'POST',body:'[beep] [surprised]'}).catch(()=>{});}catch(_){}
   document.getElementById('status').textContent='BMO is listening…';
   mic.classList.add('listening');try{rec.start();}catch(_){}
  }};
 wrec.onend=()=>{const tryStart=()=>{if(!wakeOn)return;
   if(pending||mic.classList.contains('listening')){setTimeout(tryStart,500);return;}
   try{wrec.start();}catch(_){}};
  setTimeout(tryStart,300);};
 function wakePause(){try{wrec.stop();}catch(_){}}
 function wakeResume(){if(wakeOn&&!pending)setTimeout(()=>{try{wrec.start();}catch(_){}},400);}
 wakeBtn.onclick=()=>{wakeOn=!wakeOn;wakeBtn.classList.toggle('on',wakeOn);
  if(wakeOn){try{wrec.start();}catch(_){}}else{wakePause();}
  try{localStorage.setItem('bmoWake',wakeOn?'1':'');}catch(_){}};
 try{if(localStorage.getItem('bmoWake')==='1')wakeBtn.onclick();}catch(_){}
}else{mic.onclick=()=>add('bmo','(speech input needs Chrome or Edge)');
 wakeBtn.onclick=()=>add('bmo','(wake word needs Chrome or Edge)');}
// console
const clog=document.getElementById('clog');
function clogAdd(t){clog.textContent+=t.replace(/\\s+$/,'')+'\\n';clog.scrollTop=clog.scrollHeight;}
async function cmd(c){clogAdd('> '+c);
 try{const r=await fetch('/cmd',{method:'POST',body:JSON.stringify({cmd:c})});
  const j=await r.json();clogAdd(j.error?('(error: '+j.error+')'):j.out);}
 catch(e){clogAdd('(request failed)');}}
let soundsLoaded=false,soundCatalog=null;
async function loadSounds(){
 if(soundsLoaded)return;
 try{
  soundCatalog=await(await fetch('/sounds')).json();soundsLoaded=true;
  const b=soundCatalog.bank,bank=document.getElementById('soundbank');
  bank.textContent=`Installed: ${b.name} · ${b.format} @ ${b.sample_rate_hz} Hz · SHA-256 ${b.sha256}`;
  const seq=document.getElementById('soundseq');
  Object.entries(soundCatalog.sequences).forEach(([key,s])=>{
   const button=document.createElement('button');button.className='mini';button.textContent='▶ '+s.label;
   button.title=s.description+' · IDs '+s.ids.join(', ');button.onclick=()=>playSequence(key);seq.appendChild(button);});
  const grid=document.getElementById('soundgrid');
  soundCatalog.slots.forEach(s=>{
   const card=document.createElement('div');card.className='soundcard';
   const title=document.createElement('h3');title.textContent=`ID ${s.id} · ${s.label}`;
   const role=document.createElement('div');role.textContent=s.role;
   const meta=document.createElement('div');meta.className='soundmeta';
   meta.textContent=`${s.content_seconds.toFixed(3)}s audio · ${s.slot_seconds.toFixed(3)}s slot · source: ${s.source}`;
   const play=document.createElement('button');play.className='mini';play.textContent='▶ Play';
   play.onclick=()=>{cmd('PlaySound '+s.id);play.classList.add('playing');play.textContent='♪ playing';
    setTimeout(()=>{play.classList.remove('playing');play.textContent='▶ Play';},
     Math.max(800,s.slot_seconds*1000));};
   const source=document.createElement('a');source.href=s.source_url;source.target='_blank';source.textContent='source';source.style.marginLeft='10px';
   card.append(title,role,meta,play,source);grid.appendChild(card);});
 }catch(e){document.getElementById('soundbank').textContent='Could not load sound metadata: '+e;}}
const bankProfile=document.getElementById('bankprofile'),bankConfirm=document.getElementById('bankconfirm');
bankProfile.onchange=()=>{bankConfirm.value='';bankConfirm.placeholder=bankProfile.value==='bmo'?'type INSTALL BMO':'type RESTORE ORIGINAL';};
async function installBank(){
 const msg=document.getElementById('bankmsg'),profile=bankProfile.value,confirmation=bankConfirm.value;
 msg.textContent='Writing validated sound bank; keep the robot powered and connected…';
 try{const r=await fetch('/sound-bank-install',{method:'POST',body:JSON.stringify({profile,confirmation})});
  const j=await r.json();
  if(j.error){msg.textContent='Blocked/failed: '+j.error;return;}
  msg.textContent=`Installed ${j.label}; IDs ${j.accepted_ids.join(', ')} verified. SHA-256 ${j.sha256}`;
  bankConfirm.value='';
 }catch(e){msg.textContent='Install request failed: '+e;}}
async function playSequence(name){
 try{const r=await fetch('/sound-sequence',{method:'POST',body:JSON.stringify({name})});
  const j=await r.json();clogAdd(j.error?('(error: '+j.error+')'):('BMO sequence: '+j.commands.join(' → ')));}
 catch(e){clogAdd('(sound sequence failed)');}}
function drive(l,r){cmd('TestMode On');cmd(`SetMotor LWheelDist ${l} RWheelDist ${r} Speed 100`);}
const cmdEl=document.getElementById('cmd');
let cmdHist=[],cmdIdx=-1;
try{cmdHist=JSON.parse(localStorage.getItem('bmoCmdHist')||'[]');}catch(e){}
function sendCmd(){const c=cmdEl.value.trim();if(!c)return;
 cmd(c);cmdEl.value='';cmdIdx=-1;
 if(cmdHist[0]!==c){cmdHist.unshift(c);cmdHist=cmdHist.slice(0,50);
  try{localStorage.setItem('bmoCmdHist',JSON.stringify(cmdHist));}catch(e){}}}
cmdEl.addEventListener('keydown',e=>{
 if(e.key==='Enter')sendCmd();
 else if(e.key==='ArrowUp'){if(cmdIdx<cmdHist.length-1){cmdIdx++;cmdEl.value=cmdHist[cmdIdx];}e.preventDefault();}
 else if(e.key==='ArrowDown'){cmdIdx=Math.max(-1,cmdIdx-1);cmdEl.value=cmdIdx<0?'':cmdHist[cmdIdx];e.preventDefault();}});
// lidar radar
const cv=document.getElementById('c'),ctx=cv.getContext('2d');let maxR=4000,polling=false;
function draw(scan){const W=cv.width,H=cv.height,cx=W/2,cy=H/2,R=Math.min(W,H)/2-20;
 ctx.fillStyle='#0e3b35';ctx.fillRect(0,0,W,H);
 ctx.strokeStyle='#2c6b5f';ctx.fillStyle='#7fae9f';ctx.font='12px sans-serif';
 for(let m=1000;m<=maxR;m+=1000){ctx.beginPath();ctx.arc(cx,cy,R*m/maxR,0,7);ctx.stroke();
  ctx.fillText((m/1000)+' m',cx+R*m/maxR+4,cy-4);}
 ctx.beginPath();ctx.moveTo(cx,0);ctx.lineTo(cx,H);ctx.moveTo(0,cy);ctx.lineTo(W,cy);ctx.stroke();
 ctx.fillStyle='#e5c07b';ctx.beginPath();ctx.moveTo(cx,cy-10);ctx.lineTo(cx-7,cy+8);ctx.lineTo(cx+7,cy+8);ctx.closePath();ctx.fill();
 let n=0;
 for(const[aS,d]of Object.entries(scan)){if(d<=0||d>maxR)continue;
  const a=(parseInt(aS)-90)*Math.PI/180,r=R*d/maxR;
  ctx.fillStyle='#2fbf9b';ctx.beginPath();ctx.arc(cx+r*Math.cos(a),cy+r*Math.sin(a),3,0,7);ctx.fill();n++;}
 document.getElementById('npts').textContent=n;}
async function scanTick(){if(!polling)return;
 try{const j=await(await fetch('/scan')).json();
  if(j.scan){draw(j.scan);document.getElementById('rpm').textContent=(+j.rpm).toFixed(1);}}
 catch(e){}
 setTimeout(scanTick,400);}
async function lidar(on){polling=on;
 await fetch('/lidar',{method:'POST',body:on?'1':'0'});
 if(on)scanTick();}
async function charger(){
 try{const j=await(await fetch('/charger')).json();
  document.getElementById('battv').textContent=j.error?('('+j.error+')'):
   `${j.FuelPercent??'?'}% · ${((j.VBattV??((j.BattVoltage??0)/1000))).toFixed(2)} V · `+
   `${(+j.ChargingActive)?'charging':'on battery'}`;}
 catch(e){}}
// ota
async function ota(){const f=document.getElementById('fw').files[0],m=document.getElementById('otamsg');
 if(!f){m.textContent='pick a .bin first';return;}
 m.textContent=`uploading ${f.name} (${f.size} bytes)…`;
 try{const r=await fetch('/ota',{method:'POST',body:await f.arrayBuffer()});
  const j=await r.json();m.textContent=j.error?('failed: '+j.error):('ESP32 said: '+j.out);}
 catch(e){m.textContent='upload failed: '+e;}}
// ---- BMO face screens: header avatar + faces panel LCD emulator ----
// The header avatar plays the stylized sprite faces (dot eyes, heart
// eyes, smile arcs) that were retired from the LCD preview because the
// hardware only draws full-span lines — no such limit in a canvas.
// The faces panel still shows the honest grid geometry the robot draws.
const LCD_BG='#cdd5c0',LCD_PX='#1a2216';
const zzzSleep=ms=>new Promise(res=>setTimeout(res,ms));
let facesData=null,facesLoading=null,lastActive=Date.now();
function mkScreen(canvas,bg,px){const ctx=canvas.getContext('2d');
 const s={g:0,bg,px,
  clear(){ctx.fillStyle=bg;ctx.fillRect(0,0,128,64);},
  rects(r,c){ctx.fillStyle=c||px;
   for(const[x0,y0,x1,y1]of r)ctx.fillRect(x0,y0,x1-x0+1,y1-y0+1);},
  show(r){s.clear();if(r)s.rects(r);}};
 s.clear();return s;}
const hdr=mkScreen(document.getElementById('face'),'#d8f3cd','#1a4a35');
const panel=mkScreen(document.getElementById('lcd'),LCD_BG,LCD_PX);
const rc=(x,y,w,h)=>[x,y,x+w-1,y+h-1];
function heartRects(x,y){return[rc(x+1,y,3,2),rc(x+6,y,3,2),rc(x,y+2,10,3),
 rc(x+2,y+5,6,2),rc(x+4,y+7,2,1)];}
const eyesDot=dx=>[rc(34+dx,20,8,8),rc(86+dx,20,8,8)];
const EYES_LINE=[rc(32,23,12,3),rc(84,23,12,3)],
      EYES_WIDE=[rc(33,18,10,10),rc(85,18,10,10)],
      EYES_HAT=[rc(32,24,12,3),rc(34,21,8,3),rc(84,24,12,3),rc(86,21,8,3)],
      EYES_LID=[rc(32,18,12,3),rc(33,22,10,6),rc(84,18,12,3),rc(85,22,10,6)],
      EYES_BROW=[rc(32,16,12,3),rc(34,21,8,8),rc(84,16,12,3),rc(86,21,8,8)],
      EYES_HEART=[...heartRects(31,17),...heartRects(83,17)],
      M_FLAT=[rc(52,44,25,3)],M_SMILE=[rc(44,44,41,6),rc(48,50,33,3)],
      M_GRIN=[rc(44,42,41,10),rc(50,52,29,3)],
      M_SAD=[rc(52,50,25,3),rc(46,46,6,2),rc(77,46,6,2)],
      M_O=[rc(56,42,17,13)],M_SLEEP=[rc(56,48,17,3)];
const SPRITES={
 happy:[...EYES_HAT,...M_SMILE],laugh:[...EYES_HAT,...M_GRIN],
 love:[...EYES_HEART,...M_SMILE],sad:[...eyesDot(0),...M_SAD],
 surprised:[...EYES_WIDE,...M_O],wink:[eyesDot(0)[0],EYES_LINE[1],...M_SMILE],
 sleepy:[...EYES_LID,...M_SLEEP],angry:[...EYES_BROW,...M_SAD],
 party:[...EYES_WIDE,...M_GRIN],blink:[...EYES_LINE,...M_FLAT],
 neutral:[...eyesDot(0),...M_FLAT]};
const SPRITE_ANIMS={love:'hearts',sad:'tear',sleepy:'zzz',party:'confetti'};
async function runAnimOn(s,name,g){
 if(name==='hearts'){
  for(let st=0;st<14;st++){const y=40-st*3,r=heartRects(52,y);
   s.rects(r);await zzzSleep(60);if(g!==s.g)return;
   if(y>4)s.rects(r,s.bg);}
 }else if(name==='tear'){
  for(let st=0;st<8;st++){const r=[rc(88,30+st*3,2,4)];
   s.rects(r);await zzzSleep(80);if(g!==s.g)return;
   s.rects(r,s.bg);}
 }else if(name==='zzz'){
  for(let st=0;st<10;st++){const y=34-st*3;if(y<4)break;
   const r=[[104,y,111,y],[108,y+2,110,y+2],[104,y+4,111,y+4]];
   s.rects(r);await zzzSleep(120);if(g!==s.g)return;
   s.rects(r,s.bg);}
 }else if(name==='confetti'){
  for(let st=0;st<10;st++){
   const ax=10+(st*37)%100,ay=2+(st*13)%12,bx=20+(st*53)%90,by=2+(st*29)%12;
   const r=[rc(ax,ay,2,2),rc(bx,by,2,2)];
   s.rects(r);await zzzSleep(90);if(g!==s.g)return;
   s.rects(r,s.bg);}}}
// header face states: live reactions + cute idle behaviors
async function faceIdle(){const g=++hdr.g;
 hdr.show(SPRITES.happy);
 while(g===hdr.g){
  await zzzSleep(1600+Math.random()*2800);if(g!==hdr.g)return;
  if(Date.now()-lastActive>90000){                    // dozed off
   hdr.show(SPRITES.sleepy);await runAnimOn(hdr,'zzz',g);
   if(g!==hdr.g)return;continue;}
  const roll=Math.random();
  if(roll<.5){hdr.show(SPRITES.blink);await zzzSleep(130);}
  else if(roll<.8){                                   // glance around
   const d=Math.random()<.5?-6:6;
   hdr.show([...eyesDot(d),...M_SMILE]);await zzzSleep(700);
   if(g!==hdr.g)return;
   hdr.show([...eyesDot(-d),...M_SMILE]);await zzzSleep(500);}
  else if(roll<.93){hdr.show(SPRITES.wink);await zzzSleep(450);}
  else{hdr.show(SPRITES.laugh);await zzzSleep(500);}
  if(g!==hdr.g)return;
  hdr.show(SPRITES.happy);}}
async function faceThink(){const g=++hdr.g;lastActive=Date.now();
 const dx=[0,-6,0,6];let i=0;
 while(g===hdr.g){
  hdr.show([...eyesDot(dx[i++%4]),...M_FLAT]);await zzzSleep(420);}}
async function faceSad(){const g=++hdr.g;lastActive=Date.now();
 hdr.show(SPRITES.sad);await runAnimOn(hdr,'tear',g);
 await zzzSleep(1200);if(g===hdr.g)faceIdle();}
async function faceCascade(text){const g=++hdr.g;lastActive=Date.now();
 try{await loadFaces();}catch(e){}
 const names=facesData?parseEmojis(text):['happy'];
 for(let i=0;i<names.length;i++){
  if(i>0){hdr.show(SPRITES.blink);await zzzSleep(120);if(g!==hdr.g)return;}
  hdr.show(SPRITES[names[i]]||SPRITES.happy);await zzzSleep(650);if(g!==hdr.g)return;}
 const anim=SPRITE_ANIMS[names[names.length-1]];
 if(anim){await runAnimOn(hdr,anim,g);if(g!==hdr.g)return;}
 await zzzSleep(2000);if(g===hdr.g)faceIdle();}
faceIdle();loadFaces();
// faces panel (honest grid geometry — what the robot really draws)
function lcdFace(name){panel.show(facesData&&facesData.faces[name]);}
function facesOpen(){return document.getElementById('facepanel').classList.contains('on');}
function loadFaces(){
 if(facesData)return Promise.resolve(facesData);
 if(facesLoading)return facesLoading;
 facesLoading=fetch('/faces').then(r=>r.json()).then(j=>{
  facesData=j;
  const grid=document.getElementById('egrid');grid.innerHTML='';
  for(const[emo,name]of Object.entries(j.emoji||{})){
   const b=document.createElement('button');b.className='mini';
   b.textContent=emo;b.title=name;b.onclick=()=>playEmote(emo);
   grid.appendChild(b);}
  return j;
 }).catch(e=>{facesLoading=null;return null;});
 return facesLoading;}
function toggleFaces(){const p=document.getElementById('facepanel');
 p.classList.toggle('on');
 if(p.classList.contains('on'))loadFaces();}
function parseEmojis(text){
 const map=facesData.emoji||{},keys=Object.keys(map).sort((a,b)=>b.length-a.length);
 const out=[];let i=0;
 while(i<text.length&&out.length<8){
  let hit=null;
  for(const k of keys){if(text.startsWith(k,i)){hit=k;break;}}
  if(hit){out.push(map[hit]);i+=hit.length;}else i++;}
 return out.length?out:['happy'];}
async function runCascade(text){
 try{
  if(!(await loadFaces()))return;
  const g=++panel.g,names=parseEmojis(text);
  for(let i=0;i<names.length;i++){
   if(i>0){lcdFace('blink');await zzzSleep(120);if(g!==panel.g)return;}
   lcdFace(names[i]);await zzzSleep(650);if(g!==panel.g)return;}
  const anim=(facesData.anims||{})[names[names.length-1]];
  if(anim)await runAnimOn(panel,anim,g);
 }catch(e){}}
function previewOnly(text){runCascade(text);}
function playEmote(text){
 text=(text&&text.trim())?text:'😊';
 try{fetch('/emote',{method:'POST',body:text}).catch(()=>{});}catch(e){}
 previewOnly(text);}
function playEmoteInput(){playEmote(document.getElementById('etxt').value);}
document.getElementById('etxt').addEventListener('keydown',e=>{if(e.key==='Enter')playEmoteInput();});
// ---- BMO speaks (automatic TTS bank) ----
let ttsTimer=null,ttsVoicesLoaded=false;
const ttsActiveStates=['synthesizing','building','burning','speaking','restoring'];
const ttsStateLabels={synthesizing:'🎙 synthesizing…',building:'🧱 preparing sound bank…',
 burning:'🔥 flashing chunk…',speaking:'🗣 speaking',restoring:'🎵 bringing BMO sounds back…',
 spoken:'✅ done speaking',stopped:'■ stopped',restored:'🎵 BMO sounds are back',
 error:'❌ error'};
function ttsRenderVoices(voices){
 if(ttsVoicesLoaded||!voices)return;ttsVoicesLoaded=true;
 const sel=document.getElementById('ttsvoice');sel.innerHTML='';
 for(const[v,label]of Object.entries(voices)){const o=document.createElement('option');
  o.value=v;o.textContent=label;sel.appendChild(o);}}
function ttsRender(j){
 ttsRenderVoices(j.voices);
 const prog=document.getElementById('ttsprogress'),oplog=document.getElementById('ttsoplog'),
       chunks=document.getElementById('ttschunks'),bank=document.getElementById('ttsbank');
 bank.textContent=j.installed_profile==='tts'
  ?'speech is installed in the sound flash — BMO chirps are paused'
  :'BMO sounds are installed';
 const bar=document.getElementById('ttsbar'),fill=bar.firstElementChild;
 if(j.state==='idle'){prog.textContent='no speech yet';bar.classList.remove('on');return;}
 const p=j.progress||{};
 if(ttsActiveStates.includes(j.state)&&p.total_chunks){
  let done=p.chunk_index||0;
  if(j.state==='speaking'&&p.total_segments)done+=((p.segment_index||0)+1)/p.total_segments;
  else if(j.state==='spoken')done=p.total_chunks;
  bar.classList.add('on');fill.style.width=Math.min(100,100*done/p.total_chunks)+'%';
 }else if(j.state==='spoken'){bar.classList.add('on');fill.style.width='100%';}
 else bar.classList.remove('on');
 let line=ttsStateLabels[j.state]||j.state;
 if(p.total_chunks>1&&p.chunk_index!=null)line+=` · chunk ${p.chunk_index+1}/${p.total_chunks}`;
 if(j.state==='speaking'&&p.segment_index!=null)
  line+=` · part ${p.segment_index+1}/${p.total_segments}`;
 if(j.error)line+=` — ${j.error}`;
 prog.textContent=line;
 chunks.innerHTML='';
 (j.chunks||[]).forEach(c=>{const d=document.createElement('div');
  const now=p.chunk_index===c.index&&ttsActiveStates.includes(j.state);
  d.textContent=`${now?'▶':'·'} “${c.text}” (${c.seconds.toFixed(1)}s, ${c.segments} slots)`;
  if(now)d.style.color='#7ce0c5';
  chunks.appendChild(d);});
 oplog.textContent=(j.log||[]).map(e=>new Date(e.ts*1000).toLocaleTimeString()+' '+e.message).join('\\n');
 oplog.scrollTop=oplog.scrollHeight;
 if(ttsActiveStates.includes(j.state)){if(!ttsTimer)ttsTimer=setInterval(ttsPoll,700);}
 else if(ttsTimer){clearInterval(ttsTimer);ttsTimer=null;}}
async function ttsPoll(){
 try{ttsRender(await(await fetch('/tts-bank/status')).json());}catch(e){}}
async function ttsSpeak(){
 const msg=document.getElementById('ttsprevmsg');
 const text=document.getElementById('ttstext').value.trim();
 if(!text){msg.textContent='type something first';return;}
 msg.textContent='';
 try{const r=await fetch('/tts-bank/speak',{method:'POST',body:JSON.stringify({
   text,voice:document.getElementById('ttsvoice').value})});
  const j=await r.json();
  if(j.error){msg.textContent=j.error;return;}
  ttsPoll();
 }catch(e){msg.textContent='request failed: '+e;}}
async function ttsStop(){try{await fetch('/tts-bank/stop',{method:'POST',body:'{}'});ttsPoll();}catch(e){}}
async function ttsRestore(){
 const msg=document.getElementById('ttsprevmsg');
 try{const j=await(await fetch('/tts-bank/restore',{method:'POST',body:'{}'})).json();
  msg.textContent=j.error||'';ttsPoll();
 }catch(e){msg.textContent='request failed: '+e;}}
document.getElementById('ttstext').addEventListener('keydown',e=>{
 if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();ttsSpeak();}});
</script>
"""


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

    def do_GET(self):
        if self.path.startswith("/sound-bank-file?"):
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            profile_name = query.get("profile", [""])[0]
            profile = SOUND_BANK_PROFILES.get(profile_name)
            if profile is None or not profile["path"].exists():
                return self._json({"error": "unknown or unavailable sound bank"})
            data = profile["path"].read_bytes()
            if hashlib.sha256(data).hexdigest() != profile["sha256"]:
                return self._json({"error": "local sound-bank hash mismatch"})
            return self._reply(data, "application/octet-stream")
        if self.path == "/health":
            return self._json({"robot": robot is not None, "via": body_via,
                               "esp32": ESP32})
        if self.path == "/tts-bank/status":
            with tts_job_lock:
                return self._json(tts_status_payload())
        if self.path == "/sounds":
            slots = [{"id": sound_id, **metadata}
                     for sound_id, metadata in sorted(BMO_SOUND_SLOTS.items())]
            return self._json({"bank": BMO_BANK, "slots": slots,
                               "sequences": BMO_SEQUENCES,
                               "profiles": sound_bank_profiles(),
                               "expected_installed_profile": installed_sound_profile})
        if self.path == "/faces":
            anims = {f: fn.__name__.replace("_anim_", "")
                     for f, fn in emote.ANIMS.items()}
            return self._json({"faces": emote.FACES,
                               "emoji": emote.EMOJI_FACES,
                               "anims": anims})
        if self.path == "/scan":
            if robot is None:
                return self._json({"error": "body not attached"})
            try:
                with rlock:
                    points, rpm = robot.lds_scan()
                return self._json({"scan": points, "rpm": rpm})
            except Exception as e:
                return self._json({"error": str(e)})
        if self.path == "/charger":
            if robot is None:
                return self._json({"error": "body not attached"})
            try:
                with rlock:
                    return self._json(robot.charger())
            except Exception as e:
                return self._json({"error": str(e)})
        self._reply(PAGE.replace("__ESP32__", ESP32).encode(), "text/html")

    def do_POST(self):
        global tts_job, installed_sound_profile
        n = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(n)
        if self.path == "/cmd":
            c = json.loads(raw).get("cmd", "").strip()
            if not c:
                return self._json({"error": "empty command"})
            if robot is None:
                return self._json({"error": "body not attached"})
            try:
                with rlock:
                    return self._json({"out": robot.cmd(c, timeout=4)})
            except Exception as e:
                return self._json({"error": str(e)})
        if self.path == "/sound-sequence":
            name = json.loads(raw).get("name", "")
            sequence = BMO_SEQUENCES.get(name)
            if sequence is None:
                return self._json({"error": "unknown sound sequence"})
            if robot is None:
                return self._json({"error": "body not attached"})
            commands = [f"PlaySound {sound_id}" for sound_id in sequence["ids"]]
            try:
                with rlock:
                    replies = []
                    delays = []
                    for index, (sound_id, command) in enumerate(zip(sequence["ids"], commands)):
                        replies.append(robot.cmd(command, timeout=4))
                        if index + 1 < len(commands):
                            delay = BMO_SOUND_SLOTS[sound_id]["slot_seconds"]
                            delays.append(delay)
                            time.sleep(delay)
                return self._json({"name": name, "commands": commands,
                                   "replies": replies, "delays_seconds": delays})
            except Exception as e:
                return self._json({"error": str(e)})
        if self.path == "/tts-bank/speak":
            request = json.loads(raw)
            text = request.get("text", "").strip()
            voice = request.get("voice", TTS_DEFAULT_VOICE)
            if not text:
                return self._json({"error": "empty text"})
            if voice not in TTS_VOICES:
                return self._json({"error": "unknown TTS voice"})
            with tts_job_lock:
                job, error = tts_start_speak(text, voice)
                if error:
                    return self._json({"error": error})
                return self._json({"ok": True, "id": job["id"],
                                   "state": job["state"]})
        if self.path == "/tts-bank/stop":
            with tts_job_lock:
                if tts_job is None:
                    return self._json({"error": "no TTS job"})
                tts_job["stop"].set()
                tts_log(tts_job, "stop requested")
                return self._json({"ok": True})
        if self.path == "/tts-bank/restore":
            with tts_job_lock:
                if tts_active():
                    return self._json({"error": "operation still running"})
                if robot is None:
                    return self._json({"error": "body not attached"})
                if tts_job is None:
                    tts_job = {"id": f"restore-{int(time.time())}",
                               "state": "restoring", "text": "", "voice": "",
                               "progress": tts_bank.PlaybackProgress(),
                               "stop": threading.Event(), "log": []}
                job = tts_job
                job["state"] = "restoring"
                threading.Thread(target=tts_run_restore, args=(job,),
                                 daemon=True).start()
                return self._json({"ok": True, "state": "restoring"})
        if self.path == "/sound-bank-install":
            request = json.loads(raw)
            if tts_active():
                return self._json({"error": "a TTS bank operation is running"})
            profile_name = request.get("profile", "")
            profile = SOUND_BANK_PROFILES.get(profile_name)
            if profile is None:
                return self._json({"error": "unknown sound-bank profile"})
            if request.get("confirmation") != profile["confirmation"]:
                return self._json({"error": f"type {profile['confirmation']} exactly"})
            if robot is None:
                return self._json({"error": "body not attached"})
            try:
                payload = profile["path"].read_bytes()
            except OSError as exc:
                return self._json({"error": f"sound-bank artifact unavailable: {exc}"})
            digest = hashlib.sha256(payload).hexdigest()
            if len(payload) != 770048 or digest != profile["sha256"]:
                return self._json({"error": "refusing unvalidated sound-bank bytes"})
            try:
                with rlock:
                    reply = robot.t.send_binary("Upload sound", payload, timeout=45.0)
                    time.sleep(5.0)
                    version = robot.cmd("GetVersion", timeout=5)
                    accepted_ids = []
                    for sound_id in range(21):
                        result = robot.cmd(f"PlaySound {sound_id}", timeout=4)
                        if "out of range" not in result.lower():
                            accepted_ids.append(sound_id)
                if "Software,2,4,15667" not in version:
                    return self._json({"error": "write completed but GetVersion identity check failed"})
                if set(accepted_ids) != set(LIVE_SOUND_IDS):
                    return self._json({"error": f"post-write slot map mismatch: {accepted_ids}"})
                installed_sound_profile = profile_name
                return self._json({"ok": True, "profile": profile_name,
                                   "label": profile["label"], "sha256": digest,
                                   "accepted_ids": accepted_ids,
                                   "receiver_hex": reply.hex()})
            except Exception as e:
                return self._json({"error": str(e)})
        if self.path == "/lidar":
            if robot is None:
                return self._json({"error": "body not attached"})
            try:
                with rlock:
                    robot.lidar(raw == b"1")
                return self._json({"ok": True})
            except Exception as e:
                return self._json({"error": str(e)})
        if self.path == "/tts":
            try:
                text = json.loads(raw).get("text", "").strip()
                if not text:
                    return self._json({"error": "empty text"})
                return self._reply(synthesize_tts(text), "audio/wav")
            except Exception as e:
                return self._json({"error": str(e)})
        if self.path == "/emote":
            # accepts stage cues and emojis alike, so cues can be tested by
            # hand: face cues become emojis for the cascade, sound/move cues
            # run on the body
            plan = cues.parse(raw.decode(errors="replace"))
            faces_n = len(emote.parse_emojis(plan.display)) or 1
            emote_react(plan.display)
            if plan.actions():
                body(lambda: cues.perform(robot, plan.actions()))
            return self._json({"ok": True, "faces": faces_n,
                               "cues": plan.steps})
        if self.path == "/ota":
            try:
                req = urllib.request.Request(ESP32 + "/ota", data=raw,
                    headers={"Content-Type": "application/octet-stream"})
                with urllib.request.urlopen(req, timeout=180) as resp:
                    return self._json({"out": resp.read().decode(errors="replace")})
            except Exception as e:
                return self._json({"error": str(e)})
        # /chat
        chat_request = json.loads(raw)
        text = chat_request.get("text", "")
        speak_on_robot = bool(chat_request.get("speak"))
        # instant routine layer first (Siri-style): pattern-matched canned
        # answers with choreography skip the tens-of-seconds LLM round-trip;
        # anything unmatched falls through to the brain.
        routine = None
        hit = routines.match(text, convo_state, {"robot": robot})
        if hit:
            reply, routine = hit.reply, hit.routine
            # keep the LLM's memory coherent: routine turns enter history
            # exactly as if the brain had said them
            history.append({"role": "user", "content": text})
            history.append({"role": "assistant", "content": reply})
        else:
            body(lambda: (robot.led("amber"),
                          faces.scanline(robot, range(20, 110, 30), 0.08)))
            try:
                reply = brain_chat(text)
            except Exception as e:
                reply = None
                err = str(e)
        if reply:
            # the reply is a little performance: cues out of the text, faces
            # to the cascade (as emojis), sounds/moves to the body, clean
            # words to the voice
            plan = cues.parse(reply)
            if routine is None and SPEECH_MODE == "soundbyte":
                plan = cues.condense(plan, burst_seconds=SPEECH_BURST)
            emote_react(plan.display)
            if plan.actions():
                body(lambda: (robot.led("green"),
                              cues.perform(robot, plan.actions())))
            voice_error = None
            if speak_on_robot:
                # BMO's actual voice: the reply is flashed into the sound
                # bank and spoken (skip the chirp so speech starts clean).
                with tts_job_lock:
                    _, voice_error = tts_start_speak(plan.speech, TTS_DEFAULT_VOICE)
            elif not plan.actions():
                # no cue sounds: keep the old tone-guessed chirp fallback
                body(lambda: (robot.led("green"), robot.play(chirp_for(reply)),
                              faces.blink(robot, 2, 0.1)))
            out = {"reply": plan.display, "cues": plan.steps,
                   "spoke": speak_on_robot and voice_error is None}
            if routine:
                out["routine"] = routine
            if voice_error:
                out["voice_error"] = voice_error
        else:
            body(lambda: robot.led("red"))
            out = {"reply": "", "error": err}
        data = json.dumps(out).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


if __name__ == "__main__":
    try:
        robot = Robot()
        robot.testmode(True)
        robot.led("backlight_on")
        robot.play("hello")
        body_via = "usb"
        print("body: connected over USB")
    except Exception as usb_error:
        try:
            bridge_host = urllib.parse.urlparse(ESP32).hostname
            robot = Robot(bridge_host)
            if "Software" not in robot.cmd("GetVersion", timeout=5):
                raise RuntimeError("bridge reachable but robot did not answer")
            body_via = "bridge"
            print(f"body: connected via ESP32 bridge at {bridge_host}")
        except Exception as bridge_error:
            robot = None
            print("body: not attached — usb:", usb_error, "| bridge:", bridge_error)
    ensure_brain()
    ensure_voice()
    threading.Thread(target=precache_routine_tts, daemon=True).start()
    print(f"BMO voice console: http://localhost:{PORT}")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
