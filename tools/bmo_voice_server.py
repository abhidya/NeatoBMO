#!/usr/bin/env python3
"""BMO neural voice server: Piper prosody -> BMO RVC timbre, over HTTP.

Runs inside the dedicated Python 3.12 venv (~/.neatobmo/voice-venv) because
torch has no wheel for the system Python.  Loads both models once and serves:

    GET  /health          -> {"ok": true, "engine": "piper+rvc-bmo"}
    POST /synth           -> body {"text": "...", "transpose": 6}
                             reply: WAV (mono 16-bit, Piper's native rate)

bmo_web.synthesize_tts prefers this server and falls back to Colibri/espeak,
so speech keeps working while models load or if this server is down.

    ~/.neatobmo/voice-venv/bin/python tools/bmo_voice_server.py

Models (downloaded once, never committed):
  ~/.neatobmo/voices/piper/en_US-amy-medium.onnx     (base prosody voice)
  ~/.neatobmo/voices/bmo-rvc/BmoAdventureTime_115e_3220s.pth (+ .index)
"""

from __future__ import annotations

import argparse
import io
import json
import os

# rvc-python's rmvpe path auto-picks the Metal (MPS) device, which lacks the
# fft op it needs on this torch build — fall back to CPU for those ops.
os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
# torch and faiss each bundle libomp on mac x86; without this the duplicate
# runtime aborts the process.
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
import subprocess
import sys
import tempfile
import time
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

VOICES_DIR = Path(os.path.expanduser("~/.neatobmo/voices"))
# piper lives in its own venv (its deps conflict with rvc-python's pins)
PIPER_BIN = Path(os.environ.get(
    "BMO_PIPER_BIN", os.path.expanduser("~/.neatobmo/piper-venv/bin/piper")))
PIPER_VOICE = VOICES_DIR / "piper/en_US-amy-medium.onnx"
RVC_MODEL = VOICES_DIR / "bmo-rvc/BmoAdventureTime_115e_3220s.pth"
RVC_INDEX = VOICES_DIR / "bmo-rvc/BmoAdventureTime.index"
DEFAULT_TRANSPOSE = int(os.environ.get("BMO_VOICE_TRANSPOSE", "6"))
PORT = int(os.environ.get("BMO_VOICE_PORT", "8486"))

rvc = None


def load_rvc():
    """Load the RVC engine once; downloads hubert/rmvpe base models on first run."""
    global rvc
    # Intel-Mac MPS segfaults inside rmvpe even with the fallback env; make
    # torch report no MPS so every internal device pick lands on CPU.
    import torch
    torch.backends.mps.is_available = lambda: False
    if hasattr(torch.backends.mps, "is_built"):
        torch.backends.mps.is_built = lambda: False
    from rvc_python.infer import RVCInference
    rvc = RVCInference(device="cpu")
    rvc.load_model(str(RVC_MODEL), index_path=str(RVC_INDEX))
    # index_rate=0 skips the faiss feature search, which segfaults inside the
    # RVC pipeline on this mac-x86 torch/faiss combo; the .pth model alone
    # carries the BMO timbre.  "pm" pitch tracking is the CPU-fast method.
    rvc.set_params(f0method=os.environ.get("BMO_VOICE_F0", "pm"),
                   f0up_key=DEFAULT_TRANSPOSE,
                   index_rate=float(os.environ.get("BMO_VOICE_INDEX_RATE", "0")))
    print("rvc: BMO model loaded", flush=True)


def piper_synth(text: str) -> bytes:
    """Base speech from Piper (natural prosody), as WAV bytes."""
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as handle:
        out_path = handle.name
    try:
        proc = subprocess.run(
            [str(PIPER_BIN), "-m", str(PIPER_VOICE), "-f", out_path],
            input=text.encode(), capture_output=True, timeout=120)
        if proc.returncode:
            raise RuntimeError(proc.stderr.decode(errors="replace").strip()
                               or "piper failed")
        return Path(out_path).read_bytes()
    finally:
        Path(out_path).unlink(missing_ok=True)


def rvc_convert(input_path: str, output_path: str) -> None:
    """infer_file minus its bug: surface vc_single's error tuple as an error."""
    import numpy as np
    from scipy.io import wavfile
    model_info = rvc.models[rvc.current_model]
    wav_opt = rvc.vc.vc_single(
        sid=0, input_audio_path=input_path, f0_up_key=rvc.f0up_key,
        f0_method=rvc.f0method, file_index=model_info.get("index", ""),
        index_rate=rvc.index_rate, filter_radius=rvc.filter_radius,
        resample_sr=rvc.resample_sr, rms_mix_rate=rvc.rms_mix_rate,
        protect=rvc.protect, f0_file="", file_index2="")
    if not isinstance(wav_opt, np.ndarray):
        raise RuntimeError(f"RVC conversion failed: {wav_opt[0]}")
    wavfile.write(output_path, rvc.vc.tgt_sr, wav_opt)


def synth_bmo(text: str, transpose: int | None = None) -> bytes:
    base = piper_synth(text)
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as src, \
         tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as dst:
        src.write(base)
        src.flush()
        try:
            if transpose is not None and transpose != rvc.f0up_key:
                rvc.set_params(f0up_key=transpose)
            rvc_convert(src.name, dst.name)
            return Path(dst.name).read_bytes()
        finally:
            Path(src.name).unlink(missing_ok=True)
            Path(dst.name).unlink(missing_ok=True)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, code, data, ctype="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/health":
            return self._send(200, json.dumps(
                {"ok": rvc is not None, "engine": "piper+rvc-bmo",
                 "transpose": DEFAULT_TRANSPOSE}).encode())
        self._send(404, b'{"error": "unknown path"}')

    def do_POST(self):
        if self.path != "/synth":
            return self._send(404, b'{"error": "unknown path"}')
        try:
            n = int(self.headers.get("Content-Length", 0))
            request = json.loads(self.rfile.read(n))
            text = request.get("text", "").strip()
            if not text:
                return self._send(400, b'{"error": "empty text"}')
            started = time.time()
            wav = synth_bmo(text, request.get("transpose"))
            print(f"synth: {len(text)} chars in {time.time() - started:.1f}s",
                  flush=True)
            return self._send(200, wav, "audio/wav")
        except Exception as exc:
            return self._send(500, json.dumps({"error": str(exc)}).encode())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=PORT)
    parser.add_argument("--once", metavar="TEXT",
                        help="synthesize one line to stdout WAV and exit")
    parser.add_argument("--transpose", type=int, default=None)
    args = parser.parse_args()
    for path in (PIPER_BIN, PIPER_VOICE, RVC_MODEL, RVC_INDEX):
        if not path.exists():
            raise SystemExit(f"missing model file: {path}")
    load_rvc()
    if args.once is not None:
        sys.stdout.buffer.write(synth_bmo(args.once, args.transpose))
        return
    print(f"BMO voice server on http://127.0.0.1:{args.port}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
