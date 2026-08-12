#!/usr/bin/env python3
"""One-shot, manifest-locked NeatoOS application-region experiment.

This tool can issue exactly `Upload code reboot`.  It has no retry loop and
requires the connected target, image manifest, image hash, representation, and
typed confirmation to match before it sends any application bytes.
"""

from __future__ import annotations

import argparse
import glob
import json
from pathlib import Path
import struct
import sys
import time

import serial

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from neatoos.tools.receiver_probe import sha256, verify_image


COMMAND = "Upload code reboot"
ENQ = b"\x05"
ACK = b"\x06"
NAK = b"\x15"
TERM = b"\x1a"
TARGET_SERIAL = "WTD41611DD,0037829,P"
APPROVED_SOFTWARE = ("Software,2,4,15667", "Software,2,5,15893")


class ApplicationProbeError(RuntimeError):
    pass


def require_recovery_target(version: str) -> None:
    if TARGET_SERIAL not in version or not any(
        software in version for software in APPROVED_SOFTWARE
    ):
        raise ApplicationProbeError(
            "connected robot is not the approved XV-12 factory/installed updater"
        )


def read_until(connection, markers: tuple[bytes, ...], timeout: float) -> bytes:
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


def command(connection, text: str, timeout: float = 5.0) -> str:
    connection.reset_input_buffer()
    connection.write((text + "\n").encode())
    return read_until(connection, (TERM,), timeout).split(TERM)[0].decode(errors="replace")


def wait_for_application(timeout: float = 45.0) -> dict:
    deadline = time.monotonic() + timeout
    attempts = []
    while time.monotonic() < deadline:
        for port in sorted(glob.glob("/dev/cu.usbmodem*")):
            try:
                with serial.Serial(port, 115200, timeout=0.1) as connection:
                    reply = command(connection, "GetVersion", 3.0)
            except (serial.SerialException, OSError):
                continue
            if "Serial Number," in reply and "Software," in reply:
                attempts.append({"port": port, "reply": reply})
                return {"port": port, "reply": reply, "attempts": attempts}
        time.sleep(1.0)
    return {"port": None, "reply": "", "attempts": attempts}


def execute(*, port: str, payload: bytes, representation: str) -> dict:
    result = {
        "command": COMMAND,
        "representation": representation,
        "image_bytes": len(payload),
        "image_sha256": sha256(payload),
        "automatic_retry": False,
        "application_region_write_requested": True,
    }
    connection = serial.Serial(port, 115200, timeout=0.1)
    try:
        before = command(connection, "GetVersion")
        require_recovery_target(before)
        result["before_identity_ok"] = True
        connection.reset_input_buffer()
        connection.write(f"{COMMAND} Size {len(payload) + 4}\r".encode())
        opening = read_until(connection, (ENQ, NAK, TERM), 8.0)
        result["opening_hex"] = opening.hex()
        if ENQ not in opening:
            result["application_bytes_sent"] = False
            raise ApplicationProbeError("robot did not emit ENQ; no image bytes were sent")
        result["application_bytes_sent"] = True
        checksum = sum(payload) & 0xFFFFFFFF
        result["transport_checksum"] = f"0x{checksum:08x}"
        connection.write(payload + struct.pack("<I", checksum))
        connection.flush()
        closing = read_until(connection, (ACK, NAK, TERM), 120.0)
        result["closing_hex"] = closing.hex()
        result["usb_ack"] = ACK in closing
        result["usb_nak"] = NAK in closing
    finally:
        connection.close()
    result["post_reboot"] = wait_for_application()
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument(
        "--representation",
        required=True,
        choices=(
            "raw-arm",
            "raw-structural",
            "reference-header",
            "reference-header-full-length",
        ),
    )
    parser.add_argument("--port", required=True)
    parser.add_argument("--result", required=True, type=Path)
    parser.add_argument("--execute-application-write", action="store_true")
    parser.add_argument("--confirmation", default="")
    args = parser.parse_args()
    if not args.execute_application_write:
        parser.error("refusing without --execute-application-write")
    if args.result.exists():
        parser.error(f"refusing to overwrite result: {args.result}")
    payload, record = verify_image(args.image, args.manifest, args.representation)
    required = f"FLASH NEATOOS {sha256(payload)}"
    if args.confirmation != required:
        parser.error(f"confirmation must exactly equal: {required}")
    try:
        result = execute(port=args.port, payload=payload, representation=args.representation)
    except Exception as error:
        result = {
            "command": COMMAND,
            "representation": args.representation,
            "image_bytes": len(payload),
            "image_sha256": sha256(payload),
            "automatic_retry": False,
            "error": str(error),
        }
    result["manifest"] = str(args.manifest)
    result["manifest_record"] = record
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    args.result.parent.mkdir(parents=True, exist_ok=True)
    args.result.write_text(rendered)
    print(rendered, end="")
    return 0 if not result.get("error") else 2


if __name__ == "__main__":
    raise SystemExit(main())
