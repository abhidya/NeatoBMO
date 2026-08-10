#!/usr/bin/env python3
"""Destructively write one exact archived Neato sound bank and verify recovery."""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import struct
import time

import serial


ALLOWED_SHA256 = {
    "d3969779a6a195812d72b6859454de004ea45beefdee6f1b5c50a2632564b64a": "vendor-default",
    "c17d42ec605efde8affd3d184ce41a2fd08aae80795633c4d6fe6b3e6750900f": "validated-bmo",
}
EXPECTED_BYTES = 770_048
COMMAND = "Upload sound"
ENQ = b"\x05"
TERM = b"\x1a"
ACK = b"\x06"
NAK = b"\x15"


def read_until(ser: serial.Serial, markers: tuple[bytes, ...], timeout: float) -> bytes:
    data = bytearray()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            chunk = ser.read(65536)
        except (serial.SerialException, OSError):
            break
        if not chunk:
            continue
        data.extend(chunk)
        if any(marker in chunk for marker in markers):
            break
    return bytes(data)


def command(ser: serial.Serial, text: str, timeout: float = 5.0) -> bytes:
    ser.reset_input_buffer()
    ser.write((text + "\n").encode())
    return read_until(ser, (TERM,), timeout)


def matching_version(data: bytes) -> bool:
    return b"WTD41611DD" in data and b"Software,2,4,15667" in data


def find_healthy_robot(timeout: float = 90.0):
    deadline = time.monotonic() + timeout
    attempts = []
    while time.monotonic() < deadline:
        for port in glob.glob("/dev/cu.usbmodem*"):
            try:
                ser = serial.Serial(port, 115200, timeout=0.1)
                version = command(ser, "GetVersion", 5.0)
                attempts.append({"port": port, "reply_hex_prefix": version[:80].hex()})
                if matching_version(version):
                    return ser, port, version, attempts
                ser.close()
            except (serial.SerialException, OSError):
                pass
        time.sleep(1.0)
    return None, None, b"", attempts


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("bank")
    parser.add_argument("--execute-destructive-write", action="store_true")
    args = parser.parse_args()
    if not args.execute_destructive_write:
        parser.error("refusing without --execute-destructive-write")

    with open(args.bank, "rb") as source:
        bank = source.read()
    digest = hashlib.sha256(bank).hexdigest()
    if len(bank) != EXPECTED_BYTES or digest not in ALLOWED_SHA256:
        raise SystemExit(f"refusing unexpected image: bytes={len(bank)} sha256={digest}")

    ser, port, version_before, attempts = find_healthy_robot(15.0)
    if ser is None:
        raise SystemExit("matching healthy XV-12 was not found before write")

    result = {
        "command": COMMAND,
        "image_bytes": len(bank),
        "image_sha256": digest,
        "image_profile": ALLOWED_SHA256[digest],
        "port_before": port,
        "version_before_ok": matching_version(version_before),
    }

    try:
        ser.reset_input_buffer()
        header = f"{COMMAND} Size {len(bank) + 4}\r".encode()
        ser.write(header)
        opening = read_until(ser, (ENQ, NAK, TERM), 5.0)
        result["opening_hex"] = opening.hex()
        if ENQ not in opening:
            raise SystemExit("robot did not emit ENQ; no payload was sent")

        checksum = sum(bank) & 0xFFFFFFFF
        result["checksum_hex"] = f"0x{checksum:08x}"
        started = time.monotonic()
        ser.write(bank + struct.pack("<I", checksum))
        ser.flush()
        closing = read_until(ser, (ACK, NAK, TERM), 30.0)
        result["transfer_seconds"] = round(time.monotonic() - started, 3)
        result["closing_hex"] = closing.hex()
    finally:
        try:
            ser.close()
        except Exception:
            pass

    recovered, new_port, version_after, attempts = find_healthy_robot(90.0)
    result["reenumeration_attempts"] = attempts
    result["port_after"] = new_port
    result["version_after_ok"] = matching_version(version_after)
    if recovered is None:
        print(json.dumps(result, indent=2))
        raise SystemExit("robot did not return to the normal application within 90 seconds")

    time.sleep(5.0)
    rows = []
    try:
        for sound_id in range(21):
            reply = command(recovered, f"PlaySound {sound_id}", 5.0)
            text = reply.split(TERM)[0].decode(errors="replace").strip()
            rows.append(
                {
                    "sound_id": sound_id,
                    "accepted": "out of range" not in text.lower(),
                    "reply": text,
                }
            )
    finally:
        recovered.close()
    result["sound_sweep"] = rows
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
