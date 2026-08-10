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
from neatobmo import emote
from neatobmo import faces
from neatobmo import tts_bank
from neatobmo.sounds import BMO_BANK, BMO_SEQUENCES, BMO_SOUND_SLOTS, LIVE_SOUND_IDS

BRAIN = os.environ.get("NEATOBMO_BRAIN", "http://127.0.0.1:8000/v1").rstrip("/")
KEY_FILE = os.path.expanduser("~/.neatobmo/coli_api_key")
API_KEY = open(KEY_FILE).read().strip() if os.path.exists(KEY_FILE) else None
PORT = 8485
ESP32 = os.environ.get("NEATOBMO_ESP32", "http://10.0.0.106")

# The persona's emoji palette is generated from the face engine's table so
# the LLM can only emit emojis the LCD knows how to draw.
PERSONA = ("You are BMO, a cheerful little robot buddy living inside a Neato robot "
           "vacuum. You are playful, curious, and love your human. Keep replies to "
           "1-3 short spoken-style sentences. Express your feelings with LOTS of "
           "emojis sprinkled through every reply — pick from "
           + " ".join(emote.EMOJI_FACES)
           + " — your face screen plays them in order!")

robot = None
rlock = threading.Lock()
history = [{"role": "system", "content": PERSONA}]

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

# ---- guarded TTS-to-sound-bank speech ------------------------------------
# One job at a time.  A job is previewed (bank built + validated, no robot
# contact), then optionally burned after the exact typed confirmation, spoken
# with duration pacing, and restored back to the persistent BMO bank.
TTS_VOICES = {
    "en+f4": "espeak-ng en+f4 (BMO default)",
    "en+f2": "espeak-ng en+f2",
    "en+m3": "espeak-ng en+m3",
    "en+m1": "espeak-ng en+m1",
    "en": "espeak-ng en",
}
TTS_ACTIVE_STATES = {"burning", "speaking", "restoring"}
TTS_LOG_PATH = REPO_ROOT / "logs" / "tts-bank-operations.jsonl"
tts_job = None
tts_job_lock = threading.Lock()


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
        return {"state": "idle", "voices": TTS_VOICES}
    job = tts_job
    return {
        "id": job["id"],
        "state": job["state"],
        "text": job["text"],
        "voice": job["voice"],
        "unused_slots": job["unused_slots"],
        "sha256": job["sha256"],
        "required_confirmation": tts_bank.required_confirmation(job["sha256"]),
        "speech_seconds": job["speech_seconds"],
        "capacity_seconds": job["capacity_seconds"],
        "auto_restore": job["auto_restore"],
        "manifest": job["manifest"],
        "validation": job["validation"],
        "segments": [{
            "index": seg.index, "sound_id": seg.sound_id,
            "text_fragment": seg.text_fragment,
            "content_seconds": seg.content_seconds,
            "slot_seconds": seg.slot_seconds,
            "padding_seconds": seg.padding_seconds,
        } for seg in job["segments"]],
        "progress": job["progress"].snapshot(),
        "temporary_bank_installed": job["state"] in ("temporary-installed",)
            or (job["state"] == "error" and job.get("burned") and not job.get("restored")),
        "error": job.get("error"),
        "report": job.get("report"),
        "log": job["log"][-40:],
        "voices": TTS_VOICES,
    }


def tts_build_preview(text, voice, unused_slots):
    """Text -> validated temporary bank.  Pure local work; no robot contact."""
    baseline_path = SOUND_BANK_PROFILES["bmo"]["path"]
    baseline = baseline_path.read_bytes()
    if hashlib.sha256(baseline).hexdigest() != tts_bank.BMO_BANK_SHA256:
        raise RuntimeError("persistent BMO artifact hash mismatch; refusing to build")
    records = {r.sound_id: r for r in tts_bank.record_ranges_from_bytes(baseline)}
    capacities = [(sid, records[sid].sample_count) for sid in tts_bank.SLOT_SEQUENCE]
    wav = colibri_tts(text, voice)
    samples = tts_bank.prepare_speech_pcm(wav)
    segments = tts_bank.plan_segments(samples, capacities)
    tts_bank.attach_text_fragments(segments, text)
    built, manifest = tts_bank.build_tts_bank(baseline, segments, unused_slots)
    validation = tts_bank.validate_tts_bank(baseline, built, segments, unused_slots)
    job = {
        "id": f"tts-{int(time.time())}-{validation['sha256'][:8]}",
        "created": time.time(),
        "state": "previewed",
        "text": text,
        "voice": voice,
        "unused_slots": unused_slots,
        "bank": built,
        "sha256": validation["sha256"],
        "speech_seconds": round(len(samples) / tts_bank.PCM_SAMPLE_RATE, 3),
        "capacity_seconds": round(sum(c for _, c in capacities)
                                  / tts_bank.PCM_SAMPLE_RATE, 3),
        "segments": segments,
        "manifest": manifest,
        "validation": validation,
        "auto_restore": True,
        "progress": tts_bank.PlaybackProgress(),
        "stop": threading.Event(),
        "log": [],
    }
    tts_log(job, f"preview built: voice={voice} speech={job['speech_seconds']}s "
                 f"segments={len(segments)} sha256={job['sha256']} "
                 f"validation_ok={validation['ok']}")
    return job


def tts_run_operation(job):
    """Background thread: burn -> paced speech -> (auto) BMO restore."""
    burner = tts_bank.BankBurner(robot, log=lambda m: tts_log(job, m))
    bmo_path = SOUND_BANK_PROFILES["bmo"]["path"]
    try:
        with rlock:
            job["state"] = "burning"
            job["burned"] = True
            report = tts_bank.run_speech_operation(
                burner, job["bank"], job["sha256"], job["segments"],
                bmo_path, tts_bank.BMO_BANK_SHA256,
                auto_restore=job["auto_restore"],
                progress=job["progress"], stop_event=job["stop"])
        job["report"] = report
        job["restored"] = not report.get("temporary_bank_installed", True)
        job["state"] = job["progress"].state
        tts_log(job, f"operation finished: state={job['state']}")
    except Exception as exc:
        job["error"] = str(exc)
        job["state"] = "error"
        tts_log(job, f"operation failed: {exc}")


def tts_run_restore(job):
    burner = tts_bank.BankBurner(robot, log=lambda m: tts_log(job, m))
    try:
        with rlock:
            job["state"] = "restoring"
            result = burner.restore_bank(
                SOUND_BANK_PROFILES["bmo"]["path"],
                tts_bank.BMO_BANK_SHA256, "persistent BMO bank")
        job["restored"] = True
        job["state"] = "restored"
        tts_log(job, f"explicit BMO restore verified: {result['accepted_ids']}")
    except Exception as exc:
        job["error"] = str(exc)
        job["state"] = "error"
        tts_log(job, f"explicit BMO restore failed: {exc}")


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
<title>BMO</title>
<style>
 body{margin:0;background:#0e3b35;color:#e8f6ef;font:16px -apple-system,sans-serif;
      display:flex;flex-direction:column;height:100vh;align-items:center}
 #tabs{display:flex;gap:6px;width:min(680px,94vw);padding:10px 0 0}
 .tab{flex:1;text-align:center;padding:10px;border-radius:10px 10px 0 0;cursor:pointer;
      background:#0a2a26;border:1px solid #2c6b5f;border-bottom:0;opacity:.6;font-weight:700}
 .tab.on{background:#175a50;opacity:1}
 .pane{display:none;flex:1;flex-direction:column;align-items:center;width:100%;min-height:0}
 .pane.on{display:flex}
 #face{font-size:64px;margin:18px 0 4px}
 #status{opacity:.7;font-size:13px;margin-bottom:8px}
 #log{flex:1;overflow-y:auto;width:min(680px,94vw);padding:8px}
 .msg{margin:6px 0;padding:10px 14px;border-radius:14px;max-width:80%;line-height:1.35}
 .you{background:#175a50;margin-left:auto}
 .bmo{background:#0a2a26;border:1px solid #2c6b5f}
 #bar{display:flex;gap:8px;width:min(680px,94vw);padding:12px}
 #txt{flex:1;padding:12px;border-radius:10px;border:1px solid #2c6b5f;background:#0a2a26;color:#e8f6ef;font-size:16px}
 button{border:0;border-radius:10px;padding:12px 16px;font-size:16px;cursor:pointer;background:#2fbf9b;color:#04211c;font-weight:700}
 #mic.listening{background:#e06c75;color:#fff}
 .mini{padding:6px 10px;font-size:13px;background:#175a50;color:#e8f6ef;border:1px solid #2c6b5f}
 #quick,#drive{width:min(680px,94vw);padding:6px 0}
 #drive{text-align:center}
 #clog{flex:1;overflow-y:auto;width:min(680px,94vw);padding:8px;font:12px ui-monospace,monospace;
       white-space:pre-wrap;word-break:break-all;background:#0a2a26;border:1px solid #2c6b5f;border-radius:10px}
 #crow{display:flex;gap:8px;width:min(680px,94vw);padding:12px}
 #cmd{flex:1;padding:12px;border-radius:10px;border:1px solid #2c6b5f;background:#0a2a26;color:#e8f6ef;
      font:14px ui-monospace,monospace}
 #hud{padding:8px;font-size:13px}
 .val{color:#2fbf9b;font-weight:700}
 canvas{flex:1;max-width:min(680px,94vw);min-height:0}
 #batt{width:min(680px,94vw);padding:8px;font-size:14px;line-height:1.6}
 .card{width:min(680px,94vw);margin:12px 0;padding:14px;background:#0a2a26;border:1px solid #2c6b5f;
       border-radius:14px;line-height:1.6}
 #p-sounds{overflow-y:auto}
 #soundgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:10px;
            width:min(680px,94vw);padding:0 0 16px}
 .soundcard{padding:12px;background:#0a2a26;border:1px solid #2c6b5f;border-radius:12px}
 .soundcard h3{margin:0 0 4px;color:#7ce0c5;font-size:16px}
 .soundmeta{opacity:.75;font-size:12px;line-height:1.45;margin:5px 0 9px}
 #soundseq{display:flex;flex-wrap:wrap;gap:6px;width:min(680px,94vw);padding:0 0 10px}
 a{color:#2fbf9b}
 input[type=file]{color:#e8f6ef}
 #facepanel{display:none;position:fixed;top:12px;right:12px;z-index:50;width:min(320px,90vw);
       padding:14px;background:#0a2a26;border:1px solid #2c6b5f;border-radius:14px}
 #facepanel.on{display:block}
 #fphead{display:flex;justify-content:space-between;align-items:center;margin-bottom:8px}
 #lcd{display:block;width:100%;max-width:none;flex:none;image-rendering:pixelated;
      background:#cdd5c0;border-radius:6px}
 #egrid{display:flex;flex-wrap:wrap;gap:4px;margin:8px 0}
 #erow{display:flex;gap:6px}
 #etxt{flex:1;min-width:0;padding:8px 10px;border-radius:10px;border:1px solid #2c6b5f;
       background:#0a2a26;color:#e8f6ef;font-size:16px}
</style>
<div id="tabs">
 <div class="tab on" data-p="chat" onclick="show('chat')">Chat</div>
 <div class="tab" data-p="sounds" onclick="show('sounds');loadSounds()">Sounds</div>
 <div class="tab" data-p="tts" onclick="show('tts');ttsPoll()">TTS</div>
 <div class="tab" data-p="console" onclick="show('console')">Console</div>
 <div class="tab" data-p="sensors" onclick="show('sensors')">Sensors</div>
 <div class="tab" data-p="esp32" onclick="show('esp32')">ESP32</div>
</div>
<div class="pane on" id="p-chat">
<div id="face">🤖</div>
<div id="status">BMO · TTS: Colibri · voice: ESP32 USB → PlaySound File</div>
<div id="log"></div>
<div id="bar">
  <button id="mic" title="hold to talk">🎤</button>
  <select id="voice" class="mini" title="where BMO's voice plays">
   <option value="off">🔇 muted</option>
   <option value="local">🔊 this device</option>
   <option value="robot" title="needs the ESP32 body board + voice firmware patch">🤖 robot</option>
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
  <input id="bankconfirm" placeholder="type INSTALL BMO" style="margin:6px;padding:7px;border-radius:8px;border:1px solid #2c6b5f;background:#0e3b35;color:#e8f6ef">
  <button class="mini" onclick="installBank()">Write bank</button>
  <div id="bankmsg" class="soundmeta"></div>
  <a href="/sound-bank-file?profile=bmo">download BMO</a> ·
  <a href="/sound-bank-file?profile=original">download original</a>
 </div>
 <div id="soundseq"></div>
 <div id="soundgrid"></div>
</div>
<div class="pane" id="p-tts" style="overflow-y:auto">
 <div class="card"><b>TTS Bank — speak arbitrary text through the sound flash</b><br>
  <span class="soundmeta">⚠️ Every utterance performs <b>two full flash writes</b>
  (temporary speech bank + BMO restore) and consumes flash endurance. Use sparingly.</span><br>
  <textarea id="ttstext" rows="3" placeholder="what should BMO say? (max ≈17.3 s of speech)"
   style="width:100%;box-sizing:border-box;margin:6px 0;padding:8px;border-radius:10px;border:1px solid #2c6b5f;background:#0e3b35;color:#e8f6ef;font-size:15px"></textarea>
  <select id="ttsvoice" class="mini"></select>
  <select id="ttsunused" class="mini" title="what unused slots contain in the temporary bank">
   <option value="silence">unused slots: silence</option>
   <option value="keep">unused slots: keep BMO audio</option>
  </select>
  <button class="mini" onclick="ttsPreview()">Generate Preview</button>
  <div id="ttsprevmsg" class="soundmeta"></div>
 </div>
 <div class="card" id="ttspreview" style="display:none">
  <b>Preview</b> <span id="ttssummary" class="soundmeta"></span><br>
  stitched speech: <audio id="ttsstitched" controls style="width:100%;margin:4px 0"></audio>
  <div id="ttsslots"></div>
  <div id="ttsvalidation" class="soundmeta"></div>
  <div class="soundmeta">bank SHA-256: <span id="ttssha" style="word-break:break-all"></span></div>
  <input id="ttsconfirm" placeholder="type BURN TTS &lt;sha256&gt;"
   style="width:100%;box-sizing:border-box;margin:6px 0;padding:7px;border-radius:8px;border:1px solid #2c6b5f;background:#0e3b35;color:#e8f6ef;font:12px ui-monospace,monospace">
  <label class="soundmeta"><input type="checkbox" id="ttsauto" checked>
   automatically restore the BMO bank after speaking (recommended)</label><br>
  <button class="mini" onclick="ttsBurn()">🔥 Burn and Speak</button>
  <button class="mini" onclick="ttsStop()">■ Stop</button>
 </div>
 <div class="card">
  <b>Recovery</b><br>
  <span id="ttsprogress" class="soundmeta"></span>
  <div id="ttswarn" style="display:none;color:#e5c07b;font-weight:700;margin:6px 0">
   ⚠️ temporary TTS bank is still installed — restore BMO!</div>
  <button class="mini" onclick="ttsRestore()">Restore BMO bank</button>
  <span class="soundmeta">Original emergency bank: use the Sounds tab installer
   (type RESTORE ORIGINAL) — manual fallback only.</span>
  <pre id="ttsoplog" style="max-height:180px;overflow-y:auto;font:11px ui-monospace,monospace;white-space:pre-wrap;word-break:break-all"></pre>
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
const log=document.getElementById('log'),face=document.getElementById('face');
function add(cls,text){const d=document.createElement('div');d.className='msg '+cls;
 d.textContent=text;log.appendChild(d);log.scrollTop=log.scrollHeight;}
const STATUS_DEFAULT='BMO · TTS: Colibri · voice: ESP32 USB → PlaySound File';
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
 add('you',text);face.textContent='🤔';
 try{
  const r=await fetch('/chat',{method:'POST',body:JSON.stringify({text})});
  const j=await r.json();
  clearInterval(tick);
  face.textContent='😊';add('bmo',j.reply);
  st.textContent=j.voice_error
   ? 'BMO · voice firmware patch still needed: '+j.voice_error
   : STATUS_DEFAULT;
  try{if(facesOpen())previewOnly(j.reply);}catch(e){}
  if(voiceSel.value==='local')speakLocal(j.reply);
 }catch(e){
  clearInterval(tick);
  face.textContent='😵';add('bmo','(brain unreachable)');
  st.textContent=STATUS_DEFAULT;
 }finally{
  clearInterval(tick);pending=false;
  txtEl.disabled=false;btn.disabled=false;txtEl.focus();
 }
 setTimeout(()=>face.textContent='🤖',3000);
}
function sendTxt(){const t=document.getElementById('txt');if(t.value.trim()){send(t.value.trim());t.value='';}}
document.getElementById('txt').addEventListener('keydown',e=>{if(e.key==='Enter')sendTxt();});
// speech input (Chrome/Edge)
const SR=window.SpeechRecognition||window.webkitSpeechRecognition;
const mic=document.getElementById('mic');
if(SR){const rec=new SR();rec.lang='en-US';rec.interimResults=false;
 rec.onresult=e=>{send(e.results[0][0].transcript);};
 rec.onend=()=>mic.classList.remove('listening');
 mic.onclick=()=>{if(mic.classList.contains('listening')){rec.stop();}
  else{mic.classList.add('listening');rec.start();}};
}else{mic.onclick=()=>add('bmo','(speech input needs Chrome or Edge)');}
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
   const play=document.createElement('button');play.className='mini';play.textContent='▶ Play';play.onclick=()=>cmd('PlaySound '+s.id);
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
function sendCmd(){if(cmdEl.value.trim()){cmd(cmdEl.value.trim());cmdEl.value='';}}
cmdEl.addEventListener('keydown',e=>{if(e.key==='Enter')sendCmd();});
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
// ---- faces panel + LCD emulator ----
const LCD_BG='#cdd5c0',LCD_PX='#1a2216';
const lcd=document.getElementById('lcd'),lctx=lcd.getContext('2d');
let facesData=null,facesLoading=null,emoteGen=0;
const zzzSleep=ms=>new Promise(res=>setTimeout(res,ms));
function lcdClear(){lctx.fillStyle=LCD_BG;lctx.fillRect(0,0,128,64);}
lcdClear();
function lcdRects(rects,color){lctx.fillStyle=color;
 for(const[x0,y0,x1,y1]of rects)lctx.fillRect(x0,y0,x1-x0+1,y1-y0+1);}
function lcdFace(name){lcdClear();
 const r=facesData&&facesData.faces[name];if(r)lcdRects(r,LCD_PX);}
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
function heartRects(x,y){return[[x+1,y,x+3,y+1],[x+6,y,x+8,y+1],
 [x,y+2,x+9,y+4],[x+2,y+5,x+7,y+6],[x+4,y+7,x+5,y+7]];}
async function runAnim(name,g){
 if(name==='hearts'){
  for(let s=0;s<14;s++){const y=40-s*3,r=heartRects(52,y);
   lcdRects(r,LCD_PX);await zzzSleep(60);if(g!==emoteGen)return;
   if(y>4)lcdRects(r,LCD_BG);}
 }else if(name==='tear'){
  for(let s=0;s<8;s++){const r=[[88,30+s*3,89,33+s*3]];
   lcdRects(r,LCD_PX);await zzzSleep(80);if(g!==emoteGen)return;
   lcdRects(r,LCD_BG);}
 }else if(name==='zzz'){
  for(let s=0;s<10;s++){const y=34-s*3;if(y<4)break;
   const r=[[104,y,111,y],[108,y+2,110,y+2],[104,y+4,111,y+4]];
   lcdRects(r,LCD_PX);await zzzSleep(120);if(g!==emoteGen)return;
   lcdRects(r,LCD_BG);}
 }else if(name==='confetti'){
  for(let s=0;s<10;s++){
   const ax=10+(s*37)%100,ay=2+(s*13)%12,bx=20+(s*53)%90,by=2+(s*29)%12;
   const r=[[ax,ay,ax+1,ay+1],[bx,by,bx+1,by+1]];
   lcdRects(r,LCD_PX);await zzzSleep(90);if(g!==emoteGen)return;
   lcdRects(r,LCD_BG);}}}
async function runCascade(text){
 try{
  if(!(await loadFaces()))return;
  const g=++emoteGen,names=parseEmojis(text);
  for(let i=0;i<names.length;i++){
   if(i>0){lcdFace('blink');await zzzSleep(120);if(g!==emoteGen)return;}
   lcdFace(names[i]);await zzzSleep(650);if(g!==emoteGen)return;}
  const anim=(facesData.anims||{})[names[names.length-1]];
  if(anim)await runAnim(anim,g);
 }catch(e){}}
function previewOnly(text){runCascade(text);}
function playEmote(text){
 text=(text&&text.trim())?text:'😊';
 try{fetch('/emote',{method:'POST',body:text}).catch(()=>{});}catch(e){}
 previewOnly(text);}
function playEmoteInput(){playEmote(document.getElementById('etxt').value);}
document.getElementById('etxt').addEventListener('keydown',e=>{if(e.key==='Enter')playEmoteInput();});
// ---- TTS bank panel ----
let ttsTimer=null,ttsVoicesLoaded=false;
const ttsActiveStates=['burning','speaking','restoring'];
function ttsRenderVoices(voices){
 if(ttsVoicesLoaded||!voices)return;ttsVoicesLoaded=true;
 const sel=document.getElementById('ttsvoice');sel.innerHTML='';
 for(const[v,label]of Object.entries(voices)){const o=document.createElement('option');
  o.value=v;o.textContent=label;sel.appendChild(o);}}
function ttsRender(j){
 ttsRenderVoices(j.voices);
 const prog=document.getElementById('ttsprogress'),warn=document.getElementById('ttswarn'),
       oplog=document.getElementById('ttsoplog');
 if(j.state==='idle'){prog.textContent='no TTS job yet';warn.style.display='none';return;}
 const p=j.progress||{};
 let line=`state: ${j.state}`;
 if(j.state==='speaking'&&p.segment_index!=null)
  line+=` · segment ${p.segment_index+1}/${p.total_segments} · slot ${p.current_slot}`+
   ` · elapsed ${p.elapsed_seconds}s · ${p.remaining_segments} left`;
 if(p.stopped)line+=' · stopped';
 if(j.error)line+=` · error: ${j.error}`;
 prog.textContent=line;
 warn.style.display=j.temporary_bank_installed?'block':'none';
 oplog.textContent=(j.log||[]).map(e=>new Date(e.ts*1000).toLocaleTimeString()+' '+e.message).join('\\n');
 oplog.scrollTop=oplog.scrollHeight;
 if(j.sha256){
  document.getElementById('ttspreview').style.display='block';
  document.getElementById('ttssha').textContent=j.sha256;
  document.getElementById('ttsconfirm').placeholder=j.required_confirmation;
  document.getElementById('ttssummary').textContent=
   `“${j.text}” · ${j.voice} · ${j.speech_seconds}s of ${j.capacity_seconds}s capacity`+
   ` · ${j.segments.length} segments · unused: ${j.unused_slots}`;
  document.getElementById('ttsstitched').src='/tts-bank/wav?stitched=1&sha='+j.sha256;
  const slots=document.getElementById('ttsslots');slots.innerHTML='';
  const tbl=document.createElement('table');tbl.style.width='100%';tbl.style.fontSize='12px';
  tbl.innerHTML='<tr style="opacity:.7"><td>#</td><td>slot</td><td>fragment</td>'+
   '<td>content</td><td>slot len</td><td>pad</td><td>preview</td></tr>';
  j.segments.forEach(s=>{const tr=document.createElement('tr');
   tr.innerHTML=`<td>${s.index}</td><td>${s.sound_id}</td><td>${s.text_fragment}</td>`+
    `<td>${s.content_seconds.toFixed(3)}s</td><td>${s.slot_seconds.toFixed(3)}s</td>`+
    `<td>${s.padding_seconds.toFixed(3)}s</td>`;
   const td=document.createElement('td'),a=document.createElement('audio');
   a.controls=true;a.style.width='120px';a.src='/tts-bank/wav?seg='+s.index+'&sha='+j.sha256;
   td.appendChild(a);tr.appendChild(td);tbl.appendChild(tr);});
  slots.appendChild(tbl);
  const v=j.validation||{checks:[]},bad=v.checks.filter(c=>!c.ok);
  document.getElementById('ttsvalidation').innerHTML=v.ok
   ?`✅ validation passed (${v.checks.length} checks) · checksum ${v.transport_additive_checksum_hex}`
   :`❌ validation FAILED: ${bad.map(c=>c.check).join(', ')} — burn is blocked`;
 }
 if(ttsActiveStates.includes(j.state)){if(!ttsTimer)ttsTimer=setInterval(ttsPoll,1000);}
 else if(ttsTimer){clearInterval(ttsTimer);ttsTimer=null;}}
async function ttsPoll(){
 try{ttsRender(await(await fetch('/tts-bank/status')).json());}catch(e){}}
async function ttsPreview(){
 const msg=document.getElementById('ttsprevmsg');
 const text=document.getElementById('ttstext').value.trim();
 if(!text){msg.textContent='enter some text first';return;}
 msg.textContent='synthesizing, splitting, building and validating the temporary bank…';
 try{const r=await fetch('/tts-bank/preview',{method:'POST',body:JSON.stringify({
   text,voice:document.getElementById('ttsvoice').value,
   unused_slots:document.getElementById('ttsunused').value})});
  const j=await r.json();
  if(j.error){msg.textContent='preview failed: '+j.error;return;}
  msg.textContent='preview ready — listen below, then type the burn confirmation.';
  document.getElementById('ttsconfirm').value='';ttsRender(j);
 }catch(e){msg.textContent='preview request failed: '+e;}}
async function ttsBurn(){
 const msg=document.getElementById('ttsprevmsg');
 try{const r=await fetch('/tts-bank/burn',{method:'POST',body:JSON.stringify({
   confirmation:document.getElementById('ttsconfirm').value.trim(),
   auto_restore:document.getElementById('ttsauto').checked})});
  const j=await r.json();
  if(j.error){msg.textContent='burn blocked: '+j.error;return;}
  msg.textContent='burning… keep the robot powered and connected.';ttsPoll();
 }catch(e){msg.textContent='burn request failed: '+e;}}
async function ttsStop(){try{await fetch('/tts-bank/stop',{method:'POST',body:'{}'});ttsPoll();}catch(e){}}
async function ttsRestore(){
 const msg=document.getElementById('ttsprevmsg');
 try{const j=await(await fetch('/tts-bank/restore',{method:'POST',body:'{}'})).json();
  msg.textContent=j.error?('restore blocked: '+j.error):'restoring the BMO bank…';ttsPoll();
 }catch(e){msg.textContent='restore request failed: '+e;}}
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
        if self.path == "/tts-bank/status":
            with tts_job_lock:
                return self._json(tts_status_payload())
        if self.path.startswith("/tts-bank/wav?"):
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            with tts_job_lock:
                if tts_job is None:
                    return self._json({"error": "no TTS preview available"})
                if query.get("stitched", ["0"])[0] == "1":
                    pcm = tts_bank.stitch_segments(tts_job["segments"])
                else:
                    try:
                        index = int(query.get("seg", ["-1"])[0])
                        pcm = tts_job["segments"][index].pcm
                    except (ValueError, IndexError):
                        return self._json({"error": "unknown preview segment"})
                return self._reply(tts_bank.pcm_to_wav_bytes(pcm), "audio/wav")
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
        if self.path == "/tts-bank/preview":
            global tts_job
            request = json.loads(raw)
            text = request.get("text", "").strip()
            voice = request.get("voice", "en+f4")
            unused_slots = request.get("unused_slots", "silence")
            if not text:
                return self._json({"error": "empty text"})
            if voice not in TTS_VOICES:
                return self._json({"error": "unknown TTS voice"})
            if unused_slots not in ("silence", "keep"):
                return self._json({"error": "unused_slots must be silence or keep"})
            with tts_job_lock:
                if tts_active():
                    return self._json({"error": "a TTS bank operation is already running"})
                try:
                    tts_job = tts_build_preview(text, voice, unused_slots)
                except Exception as exc:
                    return self._json({"error": str(exc)})
                return self._json(tts_status_payload())
        if self.path == "/tts-bank/burn":
            request = json.loads(raw)
            with tts_job_lock:
                job = tts_job
                if job is None or job["state"] != "previewed":
                    return self._json({"error": "no previewed TTS bank; generate a preview first"})
                if not job["validation"]["ok"]:
                    return self._json({"error": "refusing to burn: validation failed"})
                required = tts_bank.required_confirmation(job["sha256"])
                if request.get("confirmation", "") != required:
                    return self._json({"error": f"type exactly: {required}"})
                if robot is None:
                    return self._json({"error": "body not attached"})
                job["auto_restore"] = bool(request.get("auto_restore", True))
                job["state"] = "burning"
                tts_log(job, f"burn confirmed (auto_restore={job['auto_restore']}); "
                             "two full flash writes ahead")
                threading.Thread(target=tts_run_operation, args=(job,),
                                 daemon=True).start()
                return self._json({"ok": True, "state": "burning"})
        if self.path == "/tts-bank/stop":
            with tts_job_lock:
                if tts_job is None:
                    return self._json({"error": "no TTS job"})
                tts_job["stop"].set()
                tts_log(tts_job, "stop requested")
                return self._json({"ok": True})
        if self.path == "/tts-bank/restore":
            with tts_job_lock:
                job = tts_job
                if job is None:
                    return self._json({"error": "no TTS job"})
                if job["state"] in TTS_ACTIVE_STATES:
                    return self._json({"error": "operation still running"})
                if robot is None:
                    return self._json({"error": "body not attached"})
                job["state"] = "restoring"
                threading.Thread(target=tts_run_restore, args=(job,),
                                 daemon=True).start()
                return self._json({"ok": True, "state": "restoring"})
        if self.path == "/sound-bank-install":
            global installed_sound_profile
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
                return self._reply(colibri_tts(text), "audio/wav")
            except Exception as e:
                return self._json({"error": str(e)})
        if self.path == "/emote":
            text = raw.decode(errors="replace")
            faces_n = len(emote.parse_emojis(text)) or 1
            emote_react(text)
            return self._json({"ok": True, "faces": faces_n})
        if self.path == "/ota":
            try:
                req = urllib.request.Request(ESP32 + "/ota", data=raw,
                    headers={"Content-Type": "application/octet-stream"})
                with urllib.request.urlopen(req, timeout=180) as resp:
                    return self._json({"out": resp.read().decode(errors="replace")})
            except Exception as e:
                return self._json({"error": str(e)})
        # /chat
        text = json.loads(raw).get("text", "")
        body(lambda: (robot.led("amber"), faces.scanline(robot, range(20, 110, 30), 0.08)))
        try:
            reply = brain_chat(text)
        except Exception as e:
            reply = None
            err = str(e)
        if reply:
            body(lambda: (robot.led("green"), robot.play(chirp_for(reply)),
                          faces.blink(robot, 2, 0.1)))
            emote_react(reply)
            voice_error = None
            try:
                wav = colibri_tts(reply)
                esp32_play_wav(wav)
            except Exception as ex:
                voice_error = str(ex)
                print("PlaySound File failed:", ex)
            out = {"reply": reply, "spoke": voice_error is None}
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
        print("body: connected over USB")
    except Exception as e:
        robot = None
        print("body: not attached —", e)
    print(f"BMO voice console: http://localhost:{PORT}")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
