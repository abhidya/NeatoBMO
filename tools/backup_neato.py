#!/usr/bin/env python3
"""Capture a read-only recovery snapshot from a Neato XV over USB.

This does not claim to dump the installed application image.  The stock XV
console does not export that image over USB.  It preserves the device-specific
state that can be queried safely before a firmware experiment.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
import re
import time

import serial

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from neatobmo.transport import SerialTransport


COMMANDS = (
    "GetVersion",
    "GetCalInfo",
    "GetWarranty",
    "GetSchedule",
    "GetTime",
    "GetCharger",
    "GetAnalogSensors",
    "GetDigitalSensors",
    "GetButtons",
    "GetMotors",
    "GetErr",
    "Help",
    "Help Upload",
    "Help PlaySound",
    "Help SetConfig",
    "Help SetSystemMode",
)

RESET_PRONE_LOG_COMMANDS = ("GetLifeStatLog", "GetSysLog")


def safe_component(value: str, fallback: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-.")
    return cleaned or fallback


def field(response: str, label: str) -> str | None:
    for line in response.splitlines():
        parts = [part.strip() for part in line.split(",")]
        if parts and parts[0] == label:
            values = [part for part in parts[1:] if part]
            return "-".join(values) if values else None
    return None


def write_snapshot(root: Path, port: str | None = None,
                   include_logs: bool = False) -> Path:
    transport = SerialTransport(port)
    timestamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    actual_port = transport.ser.port
    try:
        version = transport.send("GetVersion", timeout=5.0)
    finally:
        transport.close()
    serial_id = safe_component(field(version, "Serial Number") or "unknown", "unknown")
    software = safe_component(field(version, "Software") or "unknown", "unknown")
    destination = root / f"{serial_id}_sw-{software}_{timestamp}"
    destination.mkdir(parents=True, exist_ok=False)
    partial_path = destination / "snapshot.partial.json"
    responses: dict[str, str] = {"GetVersion": version}
    metadata = {
        "captured_at_utc": timestamp,
        "transport": "USB CDC serial",
        "port": actual_port,
        "serial": serial_id,
        "software": software,
        "scope": "read-only configuration/calibration snapshot; not an application image",
        "commands": responses,
    }

    def checkpoint() -> None:
        partial_path.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n")

    checkpoint()
    commands = COMMANDS + RESET_PRONE_LOG_COMMANDS if include_logs else COMMANDS
    for command in commands[1:]:
        transport = None
        try:
            transport = SerialTransport(actual_port)
            responses[command] = transport.send(command, timeout=5.0)
        except (serial.SerialException, OSError, RuntimeError) as exc:
            responses[command] = f"[USB capture failed: {exc}]"
            time.sleep(1.0)
        finally:
            if transport is not None:
                transport.close()
        checkpoint()
    json_path = destination / "snapshot.json"
    json_path.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n")
    partial_path.unlink()

    transcript = []
    for command, response in responses.items():
        transcript.extend((f"===== {command} =====", response.rstrip(), ""))
    transcript_path = destination / "transcript.txt"
    transcript_path.write_text("\n".join(transcript))

    checksums = []
    for path in (json_path, transcript_path):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        checksums.append(f"{digest}  {path.name}")
    (destination / "SHA256SUMS").write_text("\n".join(checksums) + "\n")
    return destination


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", help="USB serial device; auto-detected by default")
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("/Volumes/2TB/neato-firmware-archive/robot-backups"),
        help="backup directory",
    )
    parser.add_argument(
        "--include-logs",
        action="store_true",
        help="also query logs; firmware 2.4 may reset its USB connection",
    )
    args = parser.parse_args()
    destination = write_snapshot(args.out, args.port, args.include_logs)
    print(destination)


if __name__ == "__main__":
    main()
