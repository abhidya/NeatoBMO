#!/usr/bin/env python3
"""Trigger a read-only P10 TAP scan on the first byte received from P6."""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

import serial
from serial.tools import list_ports


CHERRYDAP_VID = 0x0D28
CHERRYDAP_PID = 0x0204
OPENOCD_SCAN_COMMAND = "init; scan_chain; shutdown"


def detect_cherrydap_port() -> str:
    matches = [
        port.device
        for port in list_ports.comports()
        if port.vid == CHERRYDAP_VID and port.pid == CHERRYDAP_PID
    ]
    if len(matches) != 1:
        raise RuntimeError(
            "expected exactly one CherryDAP CDC port "
            f"({CHERRYDAP_VID:04x}:{CHERRYDAP_PID:04x}); found {matches}"
        )
    return matches[0]


def exclusive_path(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    return path.open("xb")


def build_openocd_command(speed: int) -> list[str]:
    """Return the only OpenOCD command sequence this helper is allowed to run."""
    if speed < 1 or speed > 100:
        raise ValueError("JTAG speed must be 1..100 kHz for this P10 experiment")
    return [
        "openocd",
        "-d2",
        "-c",
        "adapter driver cmsis-dap",
        "-c",
        "transport select jtag",
        "-c",
        f"adapter speed {speed}",
        "-c",
        OPENOCD_SCAN_COMMAND,
    ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", help="CherryDAP CDC port; auto-detected if omitted")
    parser.add_argument("--p6-log", type=Path, required=True)
    parser.add_argument("--openocd-log", type=Path, required=True)
    parser.add_argument("--trigger-timeout", type=float, default=60.0)
    parser.add_argument("--post-scan-capture", type=float, default=3.0)
    parser.add_argument("--speed", type=int, default=10)
    args = parser.parse_args()

    port = args.port or detect_cherrydap_port()
    command = build_openocd_command(args.speed)

    print(f"armed on {port}; waiting for first P6 byte", flush=True)
    start = time.monotonic()
    with exclusive_path(args.p6_log) as p6_log, serial.Serial(
        port=port,
        baudrate=115200,
        bytesize=serial.EIGHTBITS,
        parity=serial.PARITY_NONE,
        stopbits=serial.STOPBITS_ONE,
        timeout=0.02,
    ) as p6:
        first = b""
        while time.monotonic() - start < args.trigger_timeout:
            first = p6.read(1)
            if first:
                break
        if not first:
            raise TimeoutError(
                f"no P6 byte received within {args.trigger_timeout:.1f} seconds"
            )

        p6_log.write(first)
        p6_log.flush()
        elapsed = time.monotonic() - start
        print(
            f"triggered after {elapsed:.6f}s by byte 0x{first[0]:02x}; launching OpenOCD",
            flush=True,
        )

        with exclusive_path(args.openocd_log) as openocd_log:
            process = subprocess.Popen(
                command,
                stdout=openocd_log,
                stderr=subprocess.STDOUT,
            )
            while process.poll() is None:
                data = p6.read(4096)
                if data:
                    p6_log.write(data)
            deadline = time.monotonic() + args.post_scan_capture
            while time.monotonic() < deadline:
                data = p6.read(4096)
                if data:
                    p6_log.write(data)
            returncode = process.returncode

    print(f"OpenOCD exited with status {returncode}", flush=True)
    return returncode


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(2)
