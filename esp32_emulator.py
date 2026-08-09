#!/usr/bin/env python3
"""Emulate the ESP32 body's /speak endpoint on the Mac.

Accepts the same raw unsigned 8-bit 16 kHz mono PCM the real firmware takes
(POST /speak) and plays it through the Mac speakers with afplay. Lets you test
the website -> TTS -> body pipeline before the v3 firmware is flashed:

    python3 esp32_emulator.py                      # listens on :8486
    NEATOBMO_ESP32=http://127.0.0.1:8486 python3 bmo_web.py
"""
import io
import subprocess
import tempfile
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 8486
RATE = 16000  # matches AUDIO_RATE_HZ in esp32-body/src/audio.c


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        if self.path != "/speak":
            self.send_error(404)
            return
        n = int(self.headers.get("Content-Length", 0))
        pcm = self.rfile.read(n)
        print(f"/speak: {len(pcm)} bytes ({len(pcm)/RATE:.1f}s)")
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(1)
            w.setframerate(RATE)
            w.writeframes(pcm)
        with tempfile.NamedTemporaryFile(suffix=".wav") as f:
            f.write(buf.getvalue())
            f.flush()
            subprocess.run(["afplay", f.name])  # block like the firmware does
        self.send_response(200)
        self.send_header("Content-Length", "3")
        self.end_headers()
        self.wfile.write(b"OK\n")


if __name__ == "__main__":
    print(f"fake ESP32 body: POST /speak on http://127.0.0.1:{PORT}")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
