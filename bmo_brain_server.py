#!/usr/bin/env python3
"""OpenAI-compatible HTTP wrapper around colibri's OLMoE chat engine.

The colibri release gateway can't serve OLMoE (its engine only speaks a
stdin/stdout chat REPL), so this wraps `SNAP=<snap> CHAT=1 ./olmoe` with
/v1/chat/completions + /v1/models. One generation at a time; requests queue.

    python3 bmo_brain_server.py \
        --engine /Volumes/2TB/colibri-v1.5.0-macos-arm64/olmoe \
        --snap /Volumes/2TB/models/olmoe-snap --port 8000
"""
import argparse
import json
import os
import subprocess
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ARGS = None
LOCK = threading.Lock()
PROC = None
API_KEY = None


def start_engine():
    global PROC
    env = os.environ.copy()
    env.update({"SNAP": ARGS.snap, "CHAT": "1", "MAX_NEW": str(ARGS.max_new),
                "TEMP": str(ARGS.temp), "CTX": "4096"})
    PROC = subprocess.Popen(
        [ARGS.engine, str(ARGS.cache), str(ARGS.bits)],
        cwd=os.path.dirname(ARGS.engine),
        env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, text=True, bufsize=0)
    read_until_prompt()  # banner + weight load
    print("engine ready")


def read_until_prompt(timeout=600):
    """Read engine stdout until it shows the '> ' prompt; return text before it."""
    buf = ""
    t0 = time.time()
    while time.time() - t0 < timeout:
        ch = PROC.stdout.read(1)
        if ch == "":
            raise RuntimeError("engine died")
        buf += ch
        if buf.endswith("\n> ") or buf == "> ":
            return buf[:-2].strip()
    raise TimeoutError("engine generation timeout")


def ask(prompt):
    """One-shot: reset context, send flattened prompt, return reply."""
    with LOCK:
        PROC.stdin.write("/reset\n")
        PROC.stdin.flush()
        read_until_prompt(30)
        PROC.stdin.write(prompt.replace("\n", " ").strip() + "\n")
        PROC.stdin.flush()
        return read_until_prompt()


def flatten(messages):
    parts = []
    for m in messages:
        role = m.get("role", "user")
        content = m.get("content") or ""
        if role == "system":
            parts.append(content)
        elif role == "user":
            parts.append("User: " + content)
        elif role == "assistant":
            parts.append("Assistant: " + content)
    parts.append("Assistant:")
    return " ".join(parts)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authed(self):
        if not API_KEY:
            return True
        return self.headers.get("Authorization", "") == f"Bearer {API_KEY}"

    def do_GET(self):
        if not self._authed():
            return self._json({"error": "unauthorized"}, 401)
        if self.path.startswith("/v1/models"):
            return self._json({"object": "list",
                               "data": [{"id": "olmoe", "object": "model", "owned_by": "colibri"}]})
        self._json({"error": "not found"}, 404)

    def do_POST(self):
        if not self._authed():
            return self._json({"error": "unauthorized"}, 401)
        if self.path != "/v1/chat/completions":
            return self._json({"error": "not found"}, 404)
        n = int(self.headers.get("Content-Length", 0))
        req = json.loads(self.rfile.read(n))
        try:
            reply = ask(flatten(req.get("messages", [])))
        except Exception as e:
            return self._json({"error": str(e)}, 500)
        self._json({
            "id": "chatcmpl-" + uuid.uuid4().hex[:12],
            "object": "chat.completion",
            "model": "olmoe",
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": reply}}],
        })


def main():
    global ARGS, API_KEY
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", required=True)
    ap.add_argument("--snap", required=True)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--cache", type=int, default=64)
    ap.add_argument("--bits", type=int, default=8)
    ap.add_argument("--max-new", type=int, default=300)
    ap.add_argument("--temp", type=float, default=0.7)
    ap.add_argument("--api-key-file", default="~/.neatobmo/coli_api_key")
    ARGS = ap.parse_args()
    path = os.path.expanduser(ARGS.api_key_file)
    if os.path.exists(path):
        API_KEY = open(path).read().strip()
    start_engine()
    print(f"BMO brain (OLMoE) on http://{ARGS.host}:{ARGS.port}/v1")
    ThreadingHTTPServer((ARGS.host, ARGS.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
