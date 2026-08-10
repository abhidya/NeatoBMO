#!/usr/bin/env python3
"""Characterize XV sound-upload validation without permitting a flash burn.

This harness intentionally supports exactly one command: ``Upload sound noburn``.
It keeps every payload at the known public-bank length and stops if the robot
does not answer GetVersion after any test.
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import struct
import time

import serial


BANK_SHA256 = "d3969779a6a195812d72b6859454de004ea45beefdee6f1b5c50a2632564b64a"
COMMAND = "Upload sound noburn"
ENQ = 0x05
ACK = 0x06
NAK = 0x15
TERM = 0x1A


def read_until_marker(ser: serial.Serial, markers: set[int], timeout: float) -> bytes:
    data = bytearray()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        chunk = ser.read(65536)
        if not chunk:
            continue
        data.extend(chunk)
        if any(marker in chunk for marker in markers):
            break
    return bytes(data)


def get_version(ser: serial.Serial) -> bytes:
    ser.reset_input_buffer()
    ser.write(b"GetVersion\n")
    return read_until_marker(ser, {TERM}, 5.0)


def mutate(bank: bytes, changes: list[tuple[int, bytes]]) -> bytes:
    result = bytearray(bank)
    for offset, replacement in changes:
        result[offset : offset + len(replacement)] = replacement
    return bytes(result)


def cases(bank: bytes):
    record0 = 8 * 512
    yield "wrong_outer_checksum", bank, ((sum(bank) + 1) & 0xFFFFFFFF)
    yield "bad_directory_magic", mutate(bank, [(0, b"XX")]), None
    yield "slot0_page_out_of_bounds", mutate(bank, [(8, struct.pack("<H", 0xFFFF))]), None
    yield "record0_pcm_size_out_of_bounds", mutate(
        bank, [(record0 + 8, struct.pack("<I", 0xFFFFFFFF))]
    ), None
    yield "record0_zero_sample_rate", mutate(
        bank, [(record0 + 2, struct.pack("<H", 0))]
    ), None
    yield "record0_unknown_flags", mutate(bank, [(record0, b"\x7f\x7f")]), None


def classify(data: bytes) -> list[str]:
    labels = []
    for value, label in ((ENQ, "ENQ"), (ACK, "ACK"), (NAK, "NAK"), (TERM, "TERM")):
        if value in data:
            labels.append(label)
    return labels


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("bank")
    parser.add_argument("--port")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if not args.execute:
        parser.error("refusing to contact robot without --execute")

    bank = open(args.bank, "rb").read()
    digest = hashlib.sha256(bank).hexdigest()
    if digest != BANK_SHA256 or len(bank) != 770_048:
        raise SystemExit(f"refusing unexpected bank: bytes={len(bank)} sha256={digest}")

    ports = [args.port] if args.port else glob.glob("/dev/cu.usbmodem*")
    if len(ports) != 1:
        raise SystemExit(f"expected exactly one Neato port, found {ports}")

    results = []
    with serial.Serial(ports[0], 115200, timeout=0.1) as ser:
        initial = get_version(ser)
        if b"Software,2,4,15667" not in initial or b"WTD41611DD" not in initial:
            raise SystemExit("refusing: connected robot identity/version did not match")

        for name, payload, checksum_override in cases(bank):
            assert COMMAND == "Upload sound noburn"
            ser.reset_input_buffer()
            header = f"{COMMAND} Size {len(payload) + 4}\r".encode()
            ser.write(header)
            opening = read_until_marker(ser, {ENQ, NAK, TERM}, 5.0)
            if ENQ not in opening:
                results.append(
                    {"case": name, "opening_hex": opening.hex(), "status": "no_enq"}
                )
                break

            checksum = (
                checksum_override
                if checksum_override is not None
                else sum(payload) & 0xFFFFFFFF
            )
            ser.write(payload + struct.pack("<I", checksum))
            ser.flush()
            closing = read_until_marker(ser, {ACK, NAK, TERM}, 15.0)
            time.sleep(0.25)
            health = get_version(ser)
            healthy = (
                b"Software,2,4,15667" in health
                and b"WTD41611DD" in health
                and bytes([TERM]) in health
            )
            results.append(
                {
                    "case": name,
                    "payload_sha256": hashlib.sha256(payload).hexdigest(),
                    "checksum_hex": f"0x{checksum:08x}",
                    "opening_markers": classify(opening),
                    "opening_hex": opening.hex(),
                    "closing_markers": classify(closing),
                    "closing_hex": closing.hex(),
                    "getversion_healthy": healthy,
                }
            )
            print(json.dumps(results[-1], sort_keys=True), flush=True)
            if not healthy:
                print(json.dumps({"stopped": "GetVersion health check failed"}))
                break

    print(json.dumps({"bank_sha256": digest, "command": COMMAND, "results": results}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
