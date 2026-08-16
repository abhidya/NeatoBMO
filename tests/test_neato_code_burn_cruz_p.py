from pathlib import Path
import struct
import sys

import pytest

import tools.neato_code_burn_cruz_p as burn


def test_allowlist_contains_only_post_write_verified_images():
    assert {item["build"] for item in burn.SAFE_IMAGES.values()} == {15893, 16621, 17844}


def test_identity_accepts_approved_transition_versions():
    base = (
        "Serial Number,WTD41611DD,0037829,P\r\n"
        "MainBoard Version,7,1,\r\n"
    )
    for software in burn.ALLOWED_STARTING_SOFTWARE:
        burn.require_identity(base + software)


def test_identity_rejects_wrong_board():
    with pytest.raises(burn.BurnSafetyError):
        burn.require_identity("Serial Number,OTHER\r\nSoftware,2,5,15893")


def test_unknown_image_is_rejected(tmp_path: Path):
    image = tmp_path / "unknown.bin"
    image.write_bytes(b"unknown")
    with pytest.raises(burn.BurnSafetyError, match="unknown image"):
        burn.classify_image(image)


def test_blocked_digest_is_rejected(tmp_path: Path, monkeypatch):
    image = tmp_path / "blocked.bin"
    image.write_bytes(b"blocked")
    digest = next(iter(burn.BLOCKED_IMAGES))
    monkeypatch.setattr(
        burn.hashlib,
        "sha256",
        lambda _payload: type("H", (), {"hexdigest": lambda self: digest})(),
    )
    with pytest.raises(burn.BurnSafetyError, match="FORBIDDEN Cruz-P 3.2"):
        burn.classify_image(image)


def test_neato_port_discovery_uses_only_the_observed_usb_identity(monkeypatch):
    class Port:
        def __init__(self, device: str, vid: int, pid: int):
            self.device = device
            self.vid = vid
            self.pid = pid

    monkeypatch.setattr(
        burn.list_ports,
        "comports",
        lambda: [
            Port("/dev/cu.neato", 0x2108, 0x780B),
            Port("/dev/cu.cherrydap", 0x0D28, 0x0204),
        ],
    )
    assert burn.neato_ports() == ["/dev/cu.neato"]


def test_destructive_mode_requires_the_exact_confirmation(tmp_path: Path, monkeypatch):
    result = tmp_path / "result.json"
    monkeypatch.setattr(
        burn,
        "classify_image",
        lambda _path: (b"payload", "digest", {"release": "2.5", "build": 15893}),
    )
    monkeypatch.setattr(
        burn,
        "burn",
        lambda *_args: pytest.fail("burn must not run without exact confirmation"),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "neato_code_burn_cruz_p.py",
            "image.enc",
            "--port",
            "/dev/cu.neato",
            "--result",
            str(result),
            "--execute-destructive-write",
            "--confirmation",
            "wrong",
        ],
    )
    with pytest.raises(burn.BurnSafetyError, match="confirmation must exactly equal"):
        burn.main()
    assert not result.exists()


@pytest.mark.parametrize("post_verified", [True, False])
def test_burn_sends_one_payload_and_never_retries(monkeypatch, post_verified: bool):
    payload = b"clean-room-test-payload"

    class FakeSerial:
        def __init__(self):
            self.writes: list[bytes] = []

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def reset_input_buffer(self):
            pass

        def write(self, data: bytes):
            self.writes.append(data)

        def flush(self):
            pass

    connection = FakeSerial()
    monkeypatch.setattr(burn.serial, "Serial", lambda *_args, **_kwargs: connection)
    monkeypatch.setattr(
        burn,
        "send_command",
        lambda _connection, command, *_args: (
            b"Serial Number,WTD41611DD,0037829,P\r\n"
            b"MainBoard Version,7,1,\r\nSoftware,2,5,15893\x1a"
            if command == "GetVersion"
            else b"Upload code reboot\x1a"
        ),
    )
    replies = iter((burn.ENQ, burn.ACK))
    monkeypatch.setattr(burn, "read_until", lambda *_args, **_kwargs: next(replies))
    monkeypatch.setattr(
        burn,
        "wait_for_expected",
        lambda _expected: (
            ("/dev/cu.neato", "Software,2,7,16621")
            if post_verified
            else (None, "")
        ),
    )

    result = burn.burn(
        "/dev/cu.neato",
        payload,
        "digest",
        {"release": "2.7", "build": 16621, "expected": "Software,2,7,16621"},
    )

    transfer = payload + struct.pack("<I", sum(payload) & 0xFFFFFFFF)
    assert connection.writes.count(transfer) == 1
    assert result["auto_retries"] == 0
    assert result["post_version_expected"] is post_verified
