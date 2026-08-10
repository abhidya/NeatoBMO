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
import json
import os
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from neatobmo import Robot
from neatobmo import faces

BRAIN = os.environ.get("NEATOBMO_BRAIN", "http://127.0.0.1:8000/v1").rstrip("/")
KEY_FILE = os.path.expanduser("~/.neatobmo/coli_api_key")
API_KEY = open(KEY_FILE).read().strip() if os.path.exists(KEY_FILE) else None
PORT = 8485
ESP32 = os.environ.get("NEATOBMO_ESP32", "http://10.0.0.106")

PERSONA = ("You are BMO, a cheerful little robot buddy living inside a Neato robot "
           "vacuum. You are playful, curious, and love your human. Keep replies to "
           "1-3 short spoken-style sentences. Express your feelings with LOTS of "
           "emojis sprinkled through every reply — pick from 😊 😄 😂 😍 💖 😢 😭 "
           "😮 😱 😉 😴 💤 😠 🎉 🎮 ✨ 🤖 — your face screen plays them in order!")

robot = None
rlock = threading.Lock()
history = [{"role": "system", "content": PERSONA}]


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


def colibri_tts(text):
    """Ask the Colibri server for the WAV that the Neato will play."""
    req = urllib.request.Request(
        BRAIN + "/audio/speech",
        data=json.dumps({"model": "espeak-ng", "voice": "en+f4",
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


def emote_on_esp32(reply):
    """Fire-and-forget: ESP32 draws the reply's emojis as an LCD face cascade."""
    def push():
        try:
            req = urllib.request.Request(ESP32 + "/emote", data=reply.encode())
            urllib.request.urlopen(req, timeout=5).read()
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
 a{color:#2fbf9b}
 input[type=file]{color:#e8f6ef}
</style>
<div id="tabs">
 <div class="tab on" data-p="chat" onclick="show('chat')">Chat</div>
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
  <input id="txt" placeholder="say something to BMO…">
  <button onclick="sendTxt()">➤</button>
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
 <button class="mini" onclick="cmd('PlaySound 1')">Beep</button>
 <button class="mini" onclick="cmd('Help')">Help</button>
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
<script>
function show(p){document.querySelectorAll('.tab').forEach(t=>t.classList.toggle('on',t.dataset.p===p));
 document.querySelectorAll('.pane').forEach(d=>d.classList.toggle('on',d.id==='p-'+p));}
const log=document.getElementById('log'),face=document.getElementById('face');
function add(cls,text){const d=document.createElement('div');d.className='msg '+cls;
 d.textContent=text;log.appendChild(d);log.scrollTop=log.scrollHeight;}
async function send(text){
 add('you',text);face.textContent='🤔';
 try{
  const r=await fetch('/chat',{method:'POST',body:JSON.stringify({text})});
  const j=await r.json();
  face.textContent='😊';add('bmo',j.reply);
  document.getElementById('status').textContent=j.voice_error
   ? 'BMO · voice firmware patch still needed: '+j.voice_error
   : 'BMO · TTS: Colibri · voice: ESP32 USB → PlaySound File';
 }catch(e){face.textContent='😵';add('bmo','(brain unreachable)');}
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
        if self.path == "/lidar":
            if robot is None:
                return self._json({"error": "body not attached"})
            try:
                with rlock:
                    robot.lidar(raw == b"1")
                return self._json({"ok": True})
            except Exception as e:
                return self._json({"error": str(e)})
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
            emote_on_esp32(reply)
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
