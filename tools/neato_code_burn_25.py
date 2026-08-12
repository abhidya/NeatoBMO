#!/usr/bin/env python3
"""Install one exact Cruz-P 2.5 image on one exact XV-12 target.

This is intentionally not a general firmware uploader. It accepts one archived
image, one starting robot identity, and one typed destructive confirmation.
It never retries an application write automatically.
"""

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
IMAGE_BYTES = 805_888
IMAGE_SHA256 = "e1a31ef56e2b617a4056d70c308aab26b7e2cd95e679cb982d697ff4f089c697"
TARGET_SERIAL = "WTD41611DD,0037829,P"
TARGET_SOFTWARE = "Software,2,4,15667"
EXPECTED_SOFTWARE = ("Software,2,5,15893", "Software,2,5,15894")
CONFIRMATION = "BURN EXACT CRUZ-P 2.5 ON WTD41611DD-0037829-P"

TERM = b"\x1a"
ENQ = b"\x05"
ACK = b"\x06"
NAK = b"\x15"


class BurnSafetyError(RuntimeError):
    """Raised before a destructive transfer when a safety gate fails."""


def classify_image(size: int, digest: str) -> None:
    if size != IMAGE_BYTES or digest.lower() != IMAGE_SHA256:
        raise BurnSafetyError(
            f"refusing image: expected bytes={IMAGE_BYTES} sha256={IMAGE_SHA256}; "
            f"got bytes={size} sha256={digest.lower()}"
        )


def require_target(version: str) -> None:
    if TARGET_SERIAL not in version or TARGET_SOFTWARE not in version:
        raise BurnSafetyError("connected robot identity/version did not match the approved target")


def require_confirmation(value: str) -> None:
    if value != CONFIRMATION:
        raise BurnSafetyError(f"confirmation must exactly equal: {CONFIRMATION}")


def expected_post_version(version: str) -> bool:
    return TARGET_SERIAL in version and any(value in version for value in EXPECTED_SOFTWARE)


def neato_usb_ports() -> list[str]:
    return [
        port.device
        for port in list_ports.comports()
        if port.vid == 0x2108 and port.pid == 0x780B
    ]


def read_until(
    connection: serial.Serial, markers: tuple[bytes, ...], timeout: float
) -> bytes:
    data = bytearray()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            chunk = connection.read(connection.in_waiting or 1)
        except (serial.SerialException, OSError):
            break
        if not chunk:
            continue
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


def preflight(port: str, payload: bytes) -> dict[str, object]:
    digest = hashlib.sha256(payload).hexdigest()
    classify_image(len(payload), digest)
    with serial.Serial(port, 115200, timeout=0.1) as connection:
        version = decode_reply(send_command(connection, "GetVersion"))
        require_target(version)
        upload_help = decode_reply(send_command(connection, "Help Upload"))
    required_help = ("code", "reboot")
    if not all(token.lower() in upload_help.lower() for token in required_help):
        raise BurnSafetyError("live Help Upload did not advertise code + reboot")
    return {
        "command": COMMAND,
        "image_bytes": len(payload),
        "image_sha256": digest,
        "port": port,
        "target_identity_ok": True,
        "upload_help_ok": True,
        "writes_performed": False,
    }


def wait_for_post_version(timeout: float = 120.0) -> tuple[str | None, str, list[dict[str, str]]]:
    deadline = time.monotonic() + timeout
    attempts: list[dict[str, str]] = []
    last_version = ""
    while time.monotonic() < deadline:
        for port in neato_usb_ports():
            try:
                with serial.Serial(port, 115200, timeout=0.1) as connection:
                    version = decode_reply(send_command(connection, "GetVersion", 3.0))
            except (serial.SerialException, OSError):
                continue
            last_version = version
            attempts.append({"port": port, "reply": version})
            if expected_post_version(version):
                return port, version, attempts
        time.sleep(1.0)
    return None, last_version, attempts


def burn(port: str, payload: bytes) -> dict[str, object]:
    result = preflight(port, payload)
    result["writes_performed"] = "transfer_started"
    checksum = sum(payload) & 0xFFFFFFFF
    result["checksum_hex"] = f"0x{checksum:08x}"

    connection = serial.Serial(port, 115200, timeout=0.1)
    try:
        connection.reset_input_buffer()
        connection.write(f"{COMMAND} Size {len(payload) + 4}\r".encode())
        opening = read_until(connection, (ENQ, NAK, TERM), 8.0)
        result["opening_hex"] = opening.hex()
        if ENQ not in opening:
            result["writes_performed"] = False
            raise BurnSafetyError("robot did not emit ENQ; no application bytes were sent")

        started = time.monotonic()
        connection.write(payload + struct.pack("<I", checksum))
        connection.flush()
        closing = read_until(connection, (ACK, NAK, TERM), 120.0)
        result["transfer_seconds"] = round(time.monotonic() - started, 3)
        result["closing_hex"] = closing.hex()
        result["writes_performed"] = True
        if NAK in closing:
            raise BurnSafetyError("robot returned NAK after receiving the application image")
        if ACK not in closing and TERM not in closing:
            result["closing_note"] = "USB disconnected during code reboot before ACK/TERM was observed"
    finally:
        try:
            connection.close()
        except Exception:
            pass

    post_port, post_version, attempts = wait_for_post_version()
    result["post_port"] = post_port
    result["post_version"] = post_version
    result["post_version_expected"] = expected_post_version(post_version)
    result["reenumeration_attempts"] = attempts
    return result


def write_result(path: Path | None, result: dict[str, object]) -> None:
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    print(rendered, end="")
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x") as output:
        output.write(rendered)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path)
    parser.add_argument("--port", required=True)
    parser.add_argument("--result", type=Path)
    parser.add_argument("--execute-destructive-write", action="store_true")
    parser.add_argument("--confirmation", default="")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    payload = args.image.read_bytes()
    if not args.execute_destructive_write:
        write_result(args.result, preflight(args.port, payload))
        return 0

    require_confirmation(args.confirmation)
    result = burn(args.port, payload)
    write_result(args.result, result)
    return 0 if result["post_version_expected"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
