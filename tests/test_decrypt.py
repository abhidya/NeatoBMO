"""BMO's "decrypt my software" behaviour: honest gate, redaction, celebration.

The real Cruz .enc envelope is unsolved, so the success path is exercised
against a synthetic AES-128-CBC envelope built from a known key.  Every other
test asserts the honest degradation: no key -> no dance, no files, and a
redacted report that never leaks the key.
"""
import hashlib
import json
import os
import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from neatobmo import cues, decrypt, routines, turns

try:
    from Crypto.Cipher import AES
except Exception:  # pragma: no cover
    AES = None

KEY = bytes(range(16))  # 00 01 02 ... 0f
IV = b"\x00" * 16


def sample_plaintext():
    """ARM-vector head + many printable strings + every known marker."""
    words = ["vacuum", "lidar", "sensor", "wheel", "battery", "charger",
             "dock", "bumper", "motor", "map", "slam", "brush"]
    lines = [b"\x00\x00\xa0\xe1" * 8]  # 8 AL-condition ARM words
    for i in range(40):
        lines.append(f"BMO command {i:02d}: {words[i % len(words)]}".encode())
    for marker in (b"Neato Robotics", b"PlaySound", b"GetVersion",
                   b"SetMotor", b"Copyright", b"NEROS"):
        lines.append(marker + b" marker line")
    return b"\n".join(lines)


def build_enc(path, plaintext, key=KEY, iv=IV):
    """A minimal but structurally valid .enc envelope (not a real image)."""
    cipher = AES.new(key, AES.MODE_CBC, iv)
    padded = plaintext + b"\x00" * ((-len(plaintext)) % 512)
    payload = cipher.encrypt(padded)
    header = bytearray(512)
    struct.pack_into("<I", header, 0, len(plaintext))
    header[4] = 0x02
    header[5:16] = b"neato" + b"\x00" * 6
    header[16:32] = bytes(range(16, 32))
    path.write_bytes(bytes(header) + payload)
    return plaintext


class DecryptAttemptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if AES is None:
            raise unittest.SkipTest("pycryptodome not installed")
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        cls.enc = cls.root / "XV11App.synthetic.bin.enc"
        cls.plaintext = build_enc(cls.enc, sample_plaintext())

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_success_celebrates_saves_and_redacts(self):
        out = self.root / "out"
        result = decrypt.attempt(self.enc, key=KEY.hex(), output_dir=out)

        self.assertTrue(result.succeeded)
        self.assertIn("[dance]", result.reply)
        self.assertIn("[party]", result.reply)
        self.assertEqual(result.plaintext, self.plaintext)
        self.assertTrue(result.report["succeeded"])

        # key redaction: SHA-256 only, never the raw key
        serialized = json.dumps(result.report)
        self.assertNotIn(KEY.hex(), serialized)
        self.assertNotIn(KEY.hex().upper(), serialized)
        self.assertEqual(result.report["key_sha256"],
                         hashlib.sha256(KEY).hexdigest())

        # artifacts landed locally
        self.assertTrue(Path(result.written["local_plaintext"]).is_file())
        self.assertTrue(Path(result.written["local_report"]).is_file())

    def test_success_uploads_to_esp32_portal(self):
        uploaded = {}

        class FakeEsp32:
            def put_file(self, name, payload):
                uploaded[name] = payload

        result = decrypt.attempt(self.enc, key=KEY, esp32=FakeEsp32())
        self.assertTrue(result.succeeded)
        self.assertIn("bmo-decrypted.bin", uploaded)
        self.assertIn("bmo-decrypt-report.json", uploaded)
        self.assertEqual(uploaded["bmo-decrypted.bin"], self.plaintext)

    def test_wrong_key_stays_locked(self):
        result = decrypt.attempt(self.enc, key=b"\xff" * 16)
        self.assertFalse(result.succeeded)
        self.assertEqual(result.reply, decrypt.LOCKED_REPLY)
        self.assertIsNone(result.plaintext)
        self.assertIn("reason", result.report)

    def test_missing_image_stays_locked(self):
        result = decrypt.attempt(self.root / "nope.bin.enc")
        self.assertFalse(result.succeeded)
        self.assertEqual(result.reply, decrypt.LOCKED_REPLY)
        self.assertIn("error", result.report)

    def test_key_bytes_validation(self):
        with self.assertRaises(ValueError):
            decrypt.key_bytes("00")       # too short
        with self.assertRaises(ValueError):
            decrypt.key_bytes(b"x" * 17)  # wrong length
        self.assertEqual(decrypt.key_bytes("000102030405060708090a0b0c0d0e0f"),
                         KEY)


class DecryptRoutineTests(unittest.TestCase):
    def test_utterance_matches_decrypt_routine(self):
        hit = routines.match("decrypt your software", routines.ConvoState(), {})
        self.assertEqual(hit.routine, "decrypt")
        # empty ctx -> no image -> the honest "locked" reply, still a reply
        plan = cues.parse(hit.reply)
        self.assertTrue(plan.speech)
        self.assertIn(("move", "look"), plan.steps)

    def test_plan_turn_owns_decrypt_without_residual(self):
        plan = turns.plan_turn("decrypt your software")
        self.assertEqual([step.routine for step in plan.routines], ["decrypt"])
        self.assertFalse(plan.requires_brain)

    def test_decrypt_reply_is_dynamic_not_canned(self):
        self.assertNotIn("decrypt", {r for r in routines.canned_texts()})


class _FakeResponse:
    def __init__(self, body=b""):
        self.body = body

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class Esp32ClientFileTests(unittest.TestCase):
    def test_file_endpoint_paths(self):
        from unittest import mock
        from neatobmo.esp32 import Esp32Client

        client = Esp32Client("http://10.0.0.106")
        with mock.patch("urllib.request.urlopen") as urlopen:
            urlopen.return_value = _FakeResponse(b"[{\"name\":\"x\",\"size\":1}]")
            files = client.list_files()
            self.assertEqual(urlopen.call_args[0][0].full_url,
                             "http://10.0.0.106/files")
            self.assertEqual(files, [{"name": "x", "size": 1}])

            urlopen.return_value = _FakeResponse(b"OK")
            client.put_file("bmo-decrypted.bin", b"\x01\x02")
            req = urlopen.call_args[0][0]
            self.assertEqual(req.full_url,
                             "http://10.0.0.106/file?name=bmo-decrypted.bin")
            self.assertEqual(req.data, b"\x01\x02")

            urlopen.return_value = _FakeResponse(b"\x00\x01")
            self.assertEqual(client.get_file("a b.bin"), b"\x00\x01")
            self.assertEqual(urlopen.call_args[0][0].full_url,
                             "http://10.0.0.106/file?name=a%20b.bin")


if __name__ == "__main__":
    unittest.main()
