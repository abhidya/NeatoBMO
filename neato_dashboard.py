#!/usr/bin/env python3
# DEPRECATED: consolidated into bmo_web.py (http://localhost:8485) — use its
# Console and Sensors tabs instead. Kept for reference only.
"""Neato web dashboard.

Talks to the ESP32 body board over WiFi (command bridge on :3333) and serves
a browser UI: live lidar radar, command console with the full Neato command
set, quick buttons, and drive controls.
"""
import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ESP32_IP = "10.0.0.106"
CMD_PORT = 3333
HTTP_PORT = 8484

lock = threading.Lock()
state = {
    "connected": False,
    "console": [],   # recent console lines
    "scan": None,    # {angle: dist_mm}
    "rpm": 0,
    "scan_ts": 0,
    "poll_lidar": False,
}
_sock = None


def console_add(text):
    with lock:
        for ln in text.splitlines():
            if ln.strip():
                state["console"].append(ln)
        state["console"] = state["console"][-400:]


def send_cmd(cmd):
    global _sock
    try:
        if _sock:
            _sock.sendall((cmd + "\n").encode())
            return True
    except OSError:
        pass
    return False


def parse_block(block):
    """Route one response block (ends at ctrl-Z) to scan or console."""
    if block.lstrip().startswith("GetLDSScan"):
        points, rpm = {}, 0
        for line in block.splitlines():
            parts = line.split(",")
            if parts[0] == "ROTATION_SPEED" and len(parts) > 1:
                try:
                    rpm = float(parts[1])
                except ValueError:
                    pass
            elif len(parts) == 4 and parts[0].isdigit():
                try:
                    a, d, _, err = (int(p, 0) for p in parts)
                except ValueError:
                    continue
                if err == 0 and 0 < d < 10000:
                    points[a] = d
        if points:
            with lock:
                state["scan"] = points
                state["rpm"] = rpm
                state["scan_ts"] = time.time()
    else:
        console_add(block)


def bridge_loop():
    """Maintain the TCP link to the ESP32; split replies on ctrl-Z (0x1A)."""
    global _sock
    buf = b""
    while True:
        try:
            s = socket.create_connection((ESP32_IP, CMD_PORT), timeout=3)
            s.settimeout(0.5)
            _sock = s
            with lock:
                state["connected"] = True
            console_add(f"[bridge] connected to {ESP32_IP}:{CMD_PORT}")
            while True:
                try:
                    data = s.recv(8192)
                    if not data:
                        raise OSError("closed")
                    buf += data
                    while b"\x1a" in buf:
                        block, buf = buf.split(b"\x1a", 1)
                        parse_block(block.decode(errors="replace"))
                except socket.timeout:
                    pass
        except OSError:
            _sock = None
            with lock:
                state["connected"] = False
            time.sleep(2)


def lidar_poll_loop():
    while True:
        if state["poll_lidar"] and state["connected"]:
            send_cmd("GetLDSScan")
            time.sleep(0.4)
        else:
            time.sleep(0.3)


PAGE = """<!doctype html>
<meta charset="utf-8">
<title>Neato Dashboard</title>
<style>
  * { box-sizing: border-box; }
  body { margin:0; background:#0b0e14; color:#d5dbe5;
         font:14px -apple-system,sans-serif; display:flex; height:100vh; }
  #left { flex:1; display:flex; flex-direction:column; align-items:center; padding:8px; min-width:0; }
  #right { width:420px; display:flex; flex-direction:column; border-left:1px solid #232a36; }
  canvas { max-width:100%; flex:1; min-height:0; }
  #hud { padding:6px; }
  .val { color:#4cc38a; font-weight:600; }
  #console { flex:1; overflow-y:auto; font:12px ui-monospace,monospace;
             padding:8px; white-space:pre-wrap; word-break:break-all; }
  #cmdrow { display:flex; border-top:1px solid #232a36; }
  #cmd { flex:1; background:#11151d; color:#d5dbe5; border:0; padding:10px; font:13px ui-monospace,monospace; outline:none; }
  button { background:#1a2230; color:#d5dbe5; border:1px solid #2c3547; border-radius:6px;
           padding:6px 10px; margin:2px; cursor:pointer; font-size:12px; }
  button:hover { background:#243046; }
  #quick { padding:6px; border-bottom:1px solid #232a36; }
  #status { padding:6px 10px; font-weight:600; }
  .ok { color:#4cc38a; } .bad { color:#e06c75; }
  #drive { text-align:center; padding:6px; border-top:1px solid #232a36; }
</style>
<div id="left">
  <div id="hud">RPM <span class="val" id="rpm">-</span> · points <span class="val" id="npts">-</span>
    <button onclick="lidar(true)">Lidar ON</button>
    <button onclick="lidar(false)">Lidar OFF</button></div>
  <canvas id="c" width="900" height="900"></canvas>
  <div id="drive">
    <div><button onclick="drive(200,200)">▲</button></div>
    <button onclick="drive(-80,80)">◀</button>
    <button onclick="send('SetMotor 0 0 0')">■</button>
    <button onclick="drive(80,-80)">▶</button>
    <div><button onclick="drive(-200,-200)">▼</button></div>
  </div>
</div>
<div id="right">
  <div id="status">bridge: <span id="conn" class="bad">connecting…</span></div>
  <div id="quick">
    <button onclick="send('GetVersion')">GetVersion</button>
    <button onclick="send('GetCharger')">GetCharger</button>
    <button onclick="send('TestMode On')">TestMode On</button>
    <button onclick="send('TestMode Off')">TestMode Off</button>
    <button onclick="send('GetAnalogSensors')">Analog</button>
    <button onclick="send('GetDigitalSensors')">Digital</button>
    <button onclick="send('PlaySound 1')">Beep</button>
    <button onclick="send('Help')">Help</button>
  </div>
  <div id="console"></div>
  <div id="cmdrow"><input id="cmd" placeholder="type any Neato command, Enter to send"></div>
</div>
<script>
const cv=document.getElementById('c'),ctx=cv.getContext('2d');let maxR=4000;
function draw(scan){const W=cv.width,H=cv.height,cx=W/2,cy=H/2,R=Math.min(W,H)/2-20;
 ctx.fillStyle='#0b0e14';ctx.fillRect(0,0,W,H);
 ctx.strokeStyle='#232a36';ctx.fillStyle='#5b6676';ctx.font='12px sans-serif';
 for(let m=1000;m<=maxR;m+=1000){ctx.beginPath();ctx.arc(cx,cy,R*m/maxR,0,7);ctx.stroke();
  ctx.fillText((m/1000)+' m',cx+R*m/maxR+4,cy-4);}
 ctx.beginPath();ctx.moveTo(cx,0);ctx.lineTo(cx,H);ctx.moveTo(0,cy);ctx.lineTo(W,cy);ctx.stroke();
 ctx.fillStyle='#e5c07b';ctx.beginPath();ctx.moveTo(cx,cy-10);ctx.lineTo(cx-7,cy+8);ctx.lineTo(cx+7,cy+8);ctx.closePath();ctx.fill();
 let n=0;
 for(const[aS,d]of Object.entries(scan)){if(d<=0||d>maxR)continue;
  const a=(parseInt(aS)-90)*Math.PI/180,r=R*d/maxR;
  ctx.fillStyle='#4cc38a';ctx.beginPath();ctx.arc(cx+r*Math.cos(a),cy+r*Math.sin(a),3,0,7);ctx.fill();n++;}
 document.getElementById('npts').textContent=n;}
async function send(c){await fetch('/send',{method:'POST',body:c});}
async function lidar(on){
 if(on){await send('TestMode On');await send('SetLDSRotation On');}
 await fetch('/poll',{method:'POST',body:on?'1':'0'});
 if(!on){await send('SetLDSRotation Off');}}
function drive(l,r){send('TestMode On');send(`SetMotor ${l} ${r} 100`);}
const cmdEl=document.getElementById('cmd');
cmdEl.addEventListener('keydown',e=>{if(e.key==='Enter'&&cmdEl.value.trim()){send(cmdEl.value.trim());cmdEl.value='';}});
let lastLen=0;
async function tick(){try{
 const j=await(await fetch('/state')).json();
 const conn=document.getElementById('conn');
 conn.textContent=j.connected?'connected':'disconnected — check ESP32 power';
 conn.className=j.connected?'ok':'bad';
 if(j.scan)draw(j.scan);
 document.getElementById('rpm').textContent=(+j.rpm).toFixed(1);
 const con=document.getElementById('console');
 con.textContent=j.console.join('\\n');
 if(j.console.length!==lastLen){con.scrollTop=con.scrollHeight;lastLen=j.console.length;}
}catch(e){}setTimeout(tick,350);}
tick();
</script>
"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _reply(self, body, ctype="application/json"):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/state":
            with lock:
                body = json.dumps(state).encode()
            self._reply(body)
        else:
            self._reply(PAGE.encode(), "text/html")

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        data = self.rfile.read(n).decode()
        if self.path == "/send":
            console_add("> " + data)
            send_cmd(data)
        elif self.path == "/poll":
            state["poll_lidar"] = data == "1"
        self._reply(b"{}")


if __name__ == "__main__":
    threading.Thread(target=bridge_loop, daemon=True).start()
    threading.Thread(target=lidar_poll_loop, daemon=True).start()
    print(f"dashboard on http://localhost:{HTTP_PORT}")
    ThreadingHTTPServer(("0.0.0.0", HTTP_PORT), Handler).serve_forever()
