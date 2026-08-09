#!/usr/bin/env python3
"""Live lidar viewer for Neato XV series over USB serial.

Uses brannonvann/neato-driver-python to poll GetLDSScan and serves a
browser page that renders the 360-degree point cloud in real time.
"""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import serial

PORT = 8484
SERIAL_PORT = "/dev/cu.usbmodem143301"

latest = {"scan": None, "ts": 0, "rpm": 0, "error": None}
lock = threading.Lock()


def cmd(s, c, wait=0.3):
    s.reset_input_buffer()
    s.write((c + "\n").encode())
    time.sleep(wait)
    return s.read(60000).decode(errors="replace")


def scan_loop():
    try:
        s = serial.Serial(SERIAL_PORT, 115200, timeout=3)
        cmd(s, "TestMode On")
        cmd(s, "SetLDSRotation On")
        time.sleep(4)  # let the dome spin up
    except Exception as e:
        with lock:
            latest["error"] = str(e)
        return
    while True:
        try:
            out = cmd(s, "GetLDSScan", wait=1.8)
        except Exception:
            time.sleep(0.3)
            continue
        points, rpm = {}, 0
        for line in out.splitlines():
            parts = line.split(",")
            if parts[0] == "ROTATION_SPEED" and len(parts) > 1:
                try:
                    rpm = float(parts[1])
                except ValueError:
                    pass
            elif len(parts) == 4 and parts[0].isdigit():
                try:
                    angle, dist, _, err = (int(p, 0) for p in parts)
                except ValueError:
                    continue
                if err == 0 and 0 < dist < 10000:
                    points[angle] = dist
        if points:
            with lock:
                latest["scan"] = points
                latest["rpm"] = rpm
                latest["ts"] = time.time()
                latest["error"] = None


PAGE = """<!doctype html>
<meta charset="utf-8">
<title>Neato Lidar</title>
<style>
  body { margin:0; background:#0b0e14; color:#d5dbe5; font:14px -apple-system,sans-serif;
         display:flex; flex-direction:column; align-items:center; height:100vh; }
  #hud { padding:10px; }
  canvas { flex:1; max-width:100vw; max-height:calc(100vh - 44px); }
  .val { color:#4cc38a; font-weight:600; }
</style>
<div id="hud">Neato XV-12 &middot; RPM <span class="val" id="rpm">-</span>
 &middot; points <span class="val" id="npts">-</span>
 &middot; range <span class="val" id="range">4 m</span></div>
<canvas id="c" width="900" height="900"></canvas>
<script>
const cv = document.getElementById('c'), ctx = cv.getContext('2d');
let maxR = 4000; // mm
function draw(scan) {
  const W = cv.width, H = cv.height, cx = W/2, cy = H/2, R = Math.min(W,H)/2 - 20;
  ctx.fillStyle = '#0b0e14'; ctx.fillRect(0,0,W,H);
  ctx.strokeStyle = '#232a36'; ctx.fillStyle = '#5b6676'; ctx.font = '12px sans-serif';
  for (let m = 1000; m <= maxR; m += 1000) {
    ctx.beginPath(); ctx.arc(cx, cy, R*m/maxR, 0, 7); ctx.stroke();
    ctx.fillText((m/1000)+' m', cx + R*m/maxR + 4, cy - 4);
  }
  ctx.beginPath(); ctx.moveTo(cx,0); ctx.lineTo(cx,H); ctx.moveTo(0,cy); ctx.lineTo(W,cy); ctx.stroke();
  // robot marker (front = up)
  ctx.fillStyle = '#e5c07b';
  ctx.beginPath(); ctx.moveTo(cx, cy-10); ctx.lineTo(cx-7, cy+8); ctx.lineTo(cx+7, cy+8); ctx.closePath(); ctx.fill();
  let n = 0;
  for (const [aStr, d] of Object.entries(scan)) {
    if (d <= 0 || d > maxR) continue;
    const a = (parseInt(aStr) - 90) * Math.PI / 180; // angle 0 = front, screen up
    const r = R * d / maxR;
    const x = cx + r * Math.cos(a), y = cy + r * Math.sin(a);
    ctx.fillStyle = '#4cc38a';
    ctx.beginPath(); ctx.arc(x, y, 3, 0, 7); ctx.fill();
    n++;
  }
  document.getElementById('npts').textContent = n;
}
async function tick() {
  try {
    const r = await fetch('/scan'); const j = await r.json();
    if (j.error) { document.getElementById('hud').textContent = 'Error: ' + j.error; return; }
    if (j.scan) { draw(j.scan); document.getElementById('rpm').textContent = (+j.rpm).toFixed(1); }
  } catch(e) {}
  setTimeout(tick, 200);
}
tick();
</script>
"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path == "/scan":
            with lock:
                body = json.dumps(latest).encode()
            ctype = "application/json"
        else:
            body = PAGE.encode()
            ctype = "text/html"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    threading.Thread(target=scan_loop, daemon=True).start()
    print(f"serving on http://localhost:{PORT}")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
