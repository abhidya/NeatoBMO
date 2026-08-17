"""One adapter for the ESP32 body board's HTTP endpoints.

The device exposes /speak, /emote, /ota, /soundbank, and the SPIFFS-backed
web-portal files (/file, /files) — see esp32-body/src/web.c, neato_audio.c
and files.c.  Every caller goes through this client, so the endpoint names,
timeouts, the "OK" reply convention, and the X-Bank-SHA256 header live in
exactly one place — and tests can hand any module a fake with the same
methods.
"""
import hashlib
import json
import urllib.error
import urllib.parse
import urllib.request


class Esp32Error(RuntimeError):
    """The ESP32 rejected a request or answered unexpectedly."""


class Esp32Client:
    def __init__(self, base_url):
        self.base_url = base_url.rstrip("/")

    @property
    def hostname(self):
        return urllib.parse.urlparse(self.base_url).hostname

    def _post(self, path, data, content_type, timeout, headers=None):
        req = urllib.request.Request(
            self.base_url + path, data=data,
            headers={"Content-Type": content_type, **(headers or {})})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode(errors="replace").strip()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace").strip()
            raise Esp32Error(detail or f"ESP32 {path} returned HTTP {exc.code}") from exc
        except OSError as exc:
            raise Esp32Error(f"ESP32 {path} unreachable: {exc}") from exc

    def _get(self, path, timeout):
        req = urllib.request.Request(self.base_url + path)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace").strip()
            raise Esp32Error(detail or f"ESP32 {path} returned HTTP {exc.code}") from exc
        except OSError as exc:
            raise Esp32Error(f"ESP32 {path} unreachable: {exc}") from exc

    def _post_ok(self, path, data, content_type, timeout, headers=None):
        reply = self._post(path, data, content_type, timeout, headers)
        if reply != "OK":
            raise Esp32Error(f"unexpected ESP32 {path} reply: {reply or 'empty'}")

    def speak(self, wav):
        """Relay a WAV to the robot's own speaker (patched-firmware path)."""
        self._post_ok("/speak", bytes(wav), "audio/wav", timeout=45)

    def emote(self, text):
        """Emoji/cue text -> LCD face cascade (faces.c)."""
        self._post("/emote", text.encode(), "text/plain", timeout=5)

    def ota(self, firmware):
        """Push a firmware .bin; returns the device's reply text."""
        return self._post("/ota", bytes(firmware),
                          "application/octet-stream", timeout=180)

    def upload_soundbank(self, payload):
        """SHA-gated sound-bank install; the device runs the USB burn."""
        payload = bytes(payload)
        self._post_ok(
            "/soundbank", payload, "application/octet-stream", timeout=90,
            headers={"X-Bank-SHA256": hashlib.sha256(payload).hexdigest()})

    def put_file(self, name, payload):
        """Store a file in the device's SPIFFS-backed web portal."""
        self._post_ok("/file?name=" + urllib.parse.quote(name),
                      bytes(payload), "application/octet-stream", timeout=60)

    def get_file(self, name):
        """Fetch a file the device is serving on its web portal."""
        return self._get("/file?name=" + urllib.parse.quote(name), timeout=30)

    def list_files(self):
        """JSON list of the files the device is serving, name+size."""
        return json.loads(self._get("/files", timeout=10).decode())
