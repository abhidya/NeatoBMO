#!/usr/bin/env python3
"""Write one exact allowlisted Cruz-P application image, once, with recovery checks."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import struct
import time

import serial
from serial.tools import list_ports


COMMAND = "Upload code reboot"
CONFIRMATION = "BURN ONE EXACT CRUZ-P STOCK IMAGE ON WTD41611DD-0037829-P"
TARGET_SERIAL = "Serial Number,WTD41611DD,0037829,P"
TARGET_MAINBOARD = "MainBoard Version,7,1,"
ALLOWED_STARTING_SOFTWARE = (
    "Software,2,4,15667",
    "Software,2,5,15893",
    "Software,2,7,16621",
    "Software,3,1,17844",
)
SAFE_IMAGES = {
    "e1a31ef56e2b617a4056d70c308aab26b7e2cd95e679cb982d697ff4f089c697": {
        "release": "2.5", "build": 15893, "size": 805_888,
        "expected": "Software,2,5,15893",
    },
    "2e6033b1ef5440bed949de20e89563d7cb3dda41e0eb5e371c9d86dceeb1633f": {
        "release": "2.7", "build": 16621, "size": 805_888,
        "expected": "Software,2,7,16621",
    },
    "03396329a1a47a7358d09bd414d01eddaa5806a50a18f4d9ce2f96edc2d5fab7": {
        "release": "3.1", "build": 17844, "size": 847_872,
        "expected": "Software,3,1,17844",
    },
}
BLOCKED_IMAGES = {
    "373775d3d59f1569e9886c4aef4449ee847e74455c806e75ef916693841e27c3":
        "FORBIDDEN Cruz-P 3.2 build 18755: incompatible CPU target; brick risk",
}
TERM = b"\x1a"
ENQ = b"\x05"
ACK = b"\x06"
NAK = b"\x15"


class BurnSafetyError(RuntimeError):
    pass


def classify_image(path: Path) -> tuple[bytes, str, dict[str, object]]:
    payload = path.read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    if digest in BLOCKED_IMAGES:
        raise BurnSafetyError(BLOCKED_IMAGES[digest])
    metadata = SAFE_IMAGES.get(digest)
    if metadata is None:
        raise BurnSafetyError(
            f"refusing unknown image: bytes={len(payload)} sha256={digest}"
        )
    if len(payload) != metadata["size"]:
        raise BurnSafetyError(
            f"refusing size mismatch: expected={metadata['size']} actual={len(payload)}"
        )
    return payload, digest, metadata


def require_identity(version: str) -> None:
    if TARGET_SERIAL not in version or TARGET_MAINBOARD not in version:
        raise BurnSafetyError("connected target identity/mainboard did not match")
    if not any(token in version for token in ALLOWED_STARTING_SOFTWARE):
        raise BurnSafetyError("connected target software is outside the approved transition set")


def read_until(connection: serial.Serial, markers: tuple[bytes, ...], timeout: float) -> bytes:
    data = bytearray()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            chunk = connection.read(connection.in_waiting or 1)
        except (serial.SerialException, OSError):
            break
        if chunk:
            data.extend(chunk)
            if any(marker in data for marker in markers):
                break
    return bytes(data)


def send_command(connection: serial.Serial, command: str, timeout: float = 5.0) -> bytes:
    connection.reset_input_buffer()
    connection.write((command + "\n").encode())
    return read_until(connection, (TERM,), timeout)


def decode_reply(reply: bytes) -> str:
    return reply.split(TERM)[0].decode(errors="replace")


def neato_ports() -> list[str]:
    return [
        item.device for item in list_ports.comports()
        if item.vid == 0x2108 and item.pid == 0x780B
    ]


def wait_for_expected(expected: str, timeout: float = 120.0) -> tuple[str | None, str]:
    deadline = time.monotonic() + timeout
    last = ""
    while time.monotonic() < deadline:
        for port in neato_ports():
            try:
                with serial.Serial(port, 115200, timeout=0.1) as connection:
                    last = decode_reply(send_command(connection, "GetVersion", 3.0))
            except (serial.SerialException, OSError):
                continue
            if TARGET_SERIAL in last and TARGET_MAINBOARD in last and expected in last:
                return port, last
        time.sleep(1.0)
    return None, last


def burn(port: str, payload: bytes, digest: str, metadata: dict[str, object]) -> dict[str, object]:
    with serial.Serial(port, 115200, timeout=0.1) as connection:
        version_before = decode_reply(send_command(connection, "GetVersion"))
        require_identity(version_before)
        help_upload = decode_reply(send_command(connection, "Help Upload"))
        if "code" not in help_upload.lower() or "reboot" not in help_upload.lower():
            raise BurnSafetyError("live updater help did not advertise code + reboot")

        connection.reset_input_buffer()
        connection.write(f"{COMMAND} Size {len(payload) + 4}\r".encode())
        opening = read_until(connection, (ENQ, NAK, TERM), 8.0)
        if ENQ not in opening:
            raise BurnSafetyError("robot did not emit ENQ; no image bytes were sent")

        checksum = sum(payload) & 0xFFFFFFFF
        started = time.monotonic()
        connection.write(payload + struct.pack("<I", checksum))
        connection.flush()
        closing = read_until(connection, (ACK, NAK, TERM), 120.0)
        elapsed = round(time.monotonic() - started, 3)
        if NAK in closing:
            raise BurnSafetyError("robot returned NAK after receiving the image")
        if ACK not in closing and TERM not in closing:
            raise BurnSafetyError("transfer ended without ACK/terminator")

    expected = str(metadata["expected"])
    post_port, version_after = wait_for_expected(expected)
    return {
        "command": COMMAND,
        "image_bytes": len(payload),
        "image_sha256": digest,
        "image_release": metadata["release"],
        "image_build": metadata["build"],
        "checksum_hex": f"0x{checksum:08x}",
        "opening_hex": opening.hex(),
        "closing_hex": closing.hex(),
        "transfer_seconds": elapsed,
        "port_before": port,
        "version_before": version_before,
        "port_after": post_port,
        "version_after": version_after,
        "post_version_expected": post_port is not None,
        "writes_performed": True,
        "auto_retries": 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path)
    parser.add_argument("--port", required=True)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--execute-destructive-write", action="store_true")
    parser.add_argument("--confirmation", default="")
    args = parser.parse_args()

    payload, digest, metadata = classify_image(args.image)
    if not args.execute_destructive_write:
        result = {"writes_performed": False, "image_sha256": digest, **metadata}
    else:
        if args.confirmation != CONFIRMATION:
            raise BurnSafetyError(f"confirmation must exactly equal: {CONFIRMATION}")
        result = burn(args.port, payload, digest, metadata)

    args.result.parent.mkdir(parents=True, exist_ok=True)
    with args.result.open("x", encoding="utf-8") as output:
        json.dump(result, output, indent=2, sort_keys=True)
        output.write("\n")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if not args.execute_destructive_write or result["post_version_expected"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
