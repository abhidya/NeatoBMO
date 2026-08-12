import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "neatoos" / "tools" / "receiver_probe.py"
spec = importlib.util.spec_from_file_location("neatoos_receiver_probe", SCRIPT)
receiver = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules["neatoos_receiver_probe"] = receiver
spec.loader.exec_module(receiver)


class FakeTransport:
    def __init__(self, port):
        self.port = port
        self.binary_calls = []
        self.closed = False

    def send(self, command, timeout):
        assert command == "GetVersion"
        return (
            "Serial Number,WTD41611DD,0037829,P\r\n"
            "Software,2,5,15893\r\n"
        )

    def send_binary(self, command, payload, timeout, allow_terminator):
        self.binary_calls.append((command, payload, allow_terminator))
        return b"\x1a"

    def close(self):
        self.closed = True


def write_set(tmp_path, raw=b"arm"):
    image = tmp_path / "raw.bin"
    image.write_bytes(raw)
    digest = hashlib.sha256(raw).hexdigest()
    manifest = tmp_path / "set.json"
    manifest.write_text(json.dumps({
        "schema": "neatoos-probe-set/v1",
        "raw_input": {"size": len(raw), "sha256": digest, "path": str(image)},
        "outputs": [],
    }))
    return image, manifest


def test_only_noburn_command_is_used(tmp_path):
    image, manifest = write_set(tmp_path)
    created = []

    def factory(port):
        value = FakeTransport(port)
        created.append(value)
        return value

    result = receiver.run_probe(
        port="fake", image=image, manifest=manifest,
        representation="raw-arm", transport_factory=factory,
    )

    assert receiver.COMMAND == "Upload code noburn"
    assert created[0].binary_calls == [(receiver.COMMAND, b"arm", True)]
    assert created[0].closed
    assert result["writes_requested"] is False
    assert result["reboot_requested"] is False
    assert result["automatic_retry"] is False


def test_refuses_hash_mismatch_before_transport(tmp_path):
    image, manifest = write_set(tmp_path)
    image.write_bytes(b"changed")

    with pytest.raises(receiver.ProbeSafetyError, match="does not match"):
        receiver.run_probe(
            port="fake", image=image, manifest=manifest,
            representation="raw-arm",
            transport_factory=lambda port: pytest.fail("transport opened"),
        )


def test_refuses_unlabeled_envelope(tmp_path):
    image = tmp_path / "probe.enc"
    image.write_bytes(b"probe")
    digest = hashlib.sha256(b"probe").hexdigest()
    manifest = tmp_path / "set.json"
    manifest.write_text(json.dumps({
        "schema": "neatoos-probe-set/v1",
        "raw_input": {},
        "outputs": [{
            "label": "raw-structural", "size": 5, "sha256": digest,
            "encryption_status": "UNKNOWN",
            "authentication_status": "NOT AUTHENTICATED",
        }],
    }))

    with pytest.raises(receiver.ProbeSafetyError, match="NOT ENCRYPTED"):
        receiver.verify_image(image, manifest, "raw-structural")
