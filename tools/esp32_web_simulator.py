#!/usr/bin/env python3
"""Serve the ESP32 dashboard and emulate its remote soundboard API.

This is deliberately no-burn: it downloads and validates the same precompiled
module the ESP32 would use, then simulates install/play timing.  The dashboard
HTML is read directly from ``esp32-body/src/index.html`` so development and
device UI cannot drift.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "esp32-body" / "src" / "index.html"
PUBLISH = ROOT / "docs" / "bmo-soundboard"
MODULE_BYTES = 770_048


class State:
    def __init__(self, speed: float, allow_remote_modules: bool):
        self.lock = threading.Lock()
        self.speed = speed
        self.allow_remote_modules = allow_remote_modules
        self.job = None
        self.installed_sha = ""
        self.status = {
            "state": "idle", "key": "", "module_sha256": "", "error": "",
            "slot_index": 0, "slot_count": 0, "reused_module": False,
        }

    def snapshot(self):
        with self.lock:
            return dict(self.status)

    def update(self, **values):
        with self.lock:
            self.status.update(values)


STATE: State


def render_index(host: str) -> bytes:
    page = INDEX.read_text()
    local_catalog = f"http://{host}/bmo-soundboard/catalog.json"
    config = "<script>window.BMO_SIMULATOR=true;window.BMO_CATALOG_URL=" \
        + json.dumps(local_catalog) + ";</script>"
    if "<script>" not in page:
        raise RuntimeError("embedded dashboard is missing its script")
    return page.replace("<script>", config + "<script>", 1).encode()


def validate_request(request: dict, allow_remote_modules: bool = False) -> None:
    required = ("url", "sha256", "key", "slots", "slot_seconds")
    if any(name not in request for name in required):
        raise ValueError("missing module request fields")
    parsed_url = urllib.parse.urlparse(request["url"])
    if parsed_url.scheme not in ("http", "https"):
        raise ValueError("module URL must use HTTP or HTTPS")
    if not allow_remote_modules and (
            parsed_url.hostname not in ("127.0.0.1", "localhost", "::1") or
            not parsed_url.path.startswith("/bmo-soundboard/modules/")):
        raise ValueError(
            "remote module URLs are disabled; use --allow-remote-modules")
    if len(request["sha256"]) != 64 or any(
            char not in "0123456789abcdef" for char in request["sha256"]):
        raise ValueError("invalid SHA-256")
    if not isinstance(request["slots"], list) or not 1 <= len(request["slots"]) <= 10:
        raise ValueError("invalid slot sequence")
    if len(request["slots"]) != len(request["slot_seconds"]):
        raise ValueError("slot timing mismatch")
    if any(not isinstance(slot, int) or not 0 <= slot <= 20
           for slot in request["slots"]):
        raise ValueError("invalid sound slot")
    if any(not isinstance(wait, (int, float)) or not 0 < wait <= 20
           for wait in request["slot_seconds"]):
        raise ValueError("invalid slot duration")


def run_job(request: dict) -> None:
    try:
        STATE.update(state="downloading")
        with urllib.request.urlopen(request["url"], timeout=30) as response:
            module = response.read(MODULE_BYTES + 1)
        if len(module) != MODULE_BYTES:
            raise ValueError(f"module is {len(module)} bytes, expected {MODULE_BYTES}")
        if module[:2] != b"KT":
            raise ValueError("module lacks KT marker")
        actual = hashlib.sha256(module).hexdigest()
        if actual != request["sha256"]:
            raise ValueError(f"module SHA-256 mismatch: {actual}")
        reused = STATE.installed_sha == actual
        if not reused:
            STATE.update(state="installing")
            time.sleep(0.75 * STATE.speed)
            STATE.installed_sha = actual
        STATE.update(state="playing", module_sha256=actual,
                     reused_module=reused)
        for index, wait in enumerate(request["slot_seconds"]):
            STATE.update(slot_index=index)
            time.sleep(wait * STATE.speed)
        STATE.update(state="ready")
    except Exception as exc:
        STATE.update(state="error", error=str(exc))
    finally:
        with STATE.lock:
            STATE.job = None


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print(fmt % args)

    def reply(self, body: bytes, content_type="application/json", code=200,
              cors=False):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        if cors:
            self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def json(self, value, code=200):
        self.reply(json.dumps(value).encode(), code=code)

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/":
            page = render_index(self.headers.get("Host", "127.0.0.1:8080"))
            return self.reply(page, "text/html; charset=utf-8")
        if path == "/soundboard/status":
            return self.json(STATE.snapshot())
        prefix = "/bmo-soundboard/"
        if path.startswith(prefix):
            relative = Path(urllib.parse.unquote(path[len(prefix):]))
            target = (PUBLISH / relative).resolve()
            if PUBLISH.resolve() not in target.parents or not target.is_file():
                return self.json({"error": "not found"}, 404)
            content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            return self.reply(target.read_bytes(), content_type, cors=True)
        if path == "/wifi":
            return self.reply(b"ESP32 simulator: WiFi is already available.\n", "text/plain")
        self.json({"error": "not found"}, 404)

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        if path != "/soundboard/play":
            return self.json({"error": "not found"}, 404)
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 2048:
                raise ValueError("invalid JSON body size")
            request = json.loads(self.rfile.read(length))
            validate_request(request, STATE.allow_remote_modules)
            with STATE.lock:
                if STATE.job is not None:
                    return self.json({"error": "another sound is active"}, 409)
                STATE.status = {
                    "state": "queued", "key": request["key"],
                    "module_sha256": "", "error": "", "slot_index": 0,
                    "slot_count": len(request["slots"]), "reused_module": False,
                }
                STATE.job = threading.Thread(target=run_job, args=(request,), daemon=True)
                STATE.job.start()
            self.json({"ok": True, "state": "queued"}, 202)
        except Exception as exc:
            self.json({"error": str(exc)}, 400)


def main() -> None:
    global STATE
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--speed", type=float, default=0.05,
                        help="fraction of real install/play timing")
    parser.add_argument(
        "--allow-remote-modules", action="store_true",
        help="permit arbitrary HTTP(S) module URLs (unsafe on untrusted LANs)")
    args = parser.parse_args()
    STATE = State(max(0, args.speed), args.allow_remote_modules)
    print(f"ESP32 web simulator: http://{args.host}:{args.port}")
    print("Sound-module writes are simulated; no robot is contacted.")
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
