#!/usr/bin/env python3
"""Passively wait for P10 TDO power-up, then launch a read-only TAP scan."""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

import usb.core
import usb.util


VID = 0x0D28
PID = 0x0204
INTERFACE = 0
OUT_ENDPOINT = 0x02
IN_ENDPOINT = 0x81
DAP_SWJ_PINS = 0x10
TDO_MASK = 1 << 3
OPENOCD_SCAN_COMMAND = "init; scan_chain; shutdown"


def exchange(out_endpoint, in_endpoint, payload: bytes) -> bytes:
    if len(payload) > 64:
        raise ValueError("CMSIS-DAP packet exceeds 64 bytes")
    out_endpoint.write(payload + bytes(64 - len(payload)))
    response = bytes(in_endpoint.read(64, timeout=1000))
    if not response or response[0] != payload[0]:
        raise RuntimeError(f"unexpected CMSIS-DAP response: {response[:8].hex()}")
    return response


def read_pins(out_endpoint, in_endpoint) -> int:
    response = exchange(
        out_endpoint,
        in_endpoint,
        bytes((DAP_SWJ_PINS, 0, 0, 0, 0, 0, 0)),
    )
    if len(response) < 2:
        raise RuntimeError("short DAP_SWJ_Pins response")
    return response[1]


def wait_for_tdo_rise(timeout: float) -> tuple[float, int, int]:
    device = usb.core.find(idVendor=VID, idProduct=PID)
    if device is None:
        raise RuntimeError("CherryUSB CMSIS-DAP device not found")

    configuration = device.get_active_configuration()
    interface = configuration[(INTERFACE, 0)]
    out_endpoint = usb.util.find_descriptor(
        interface, custom_match=lambda endpoint: endpoint.bEndpointAddress == OUT_ENDPOINT
    )
    in_endpoint = usb.util.find_descriptor(
        interface, custom_match=lambda endpoint: endpoint.bEndpointAddress == IN_ENDPOINT
    )
    if out_endpoint is None or in_endpoint is None:
        raise RuntimeError("CMSIS-DAP bulk endpoints not found")

    usb.util.claim_interface(device, INTERFACE)
    start = time.monotonic()
    initial = read_pins(out_endpoint, in_endpoint)
    previous = initial
    try:
        while time.monotonic() - start < timeout:
            current = read_pins(out_endpoint, in_endpoint)
            if not previous & TDO_MASK and current & TDO_MASK:
                return time.monotonic() - start, initial, current
            previous = current
            time.sleep(0.001)
    finally:
        usb.util.release_interface(device, INTERFACE)
        usb.util.dispose_resources(device)

    raise TimeoutError(f"TDO did not rise within {timeout:.1f} seconds")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--speed", type=int, default=10)
    args = parser.parse_args()

    elapsed, initial, triggered = wait_for_tdo_rise(args.timeout)
    print(
        f"TDO rise after {elapsed:.6f}s: pins 0x{initial:02x} -> 0x{triggered:02x}",
        flush=True,
    )

    if args.speed < 1 or args.speed > 100:
        raise ValueError("JTAG speed must be 1..100 kHz for this P10 experiment")

    command = [
        "openocd",
        "-d2",
        "-c",
        "adapter driver cmsis-dap",
        "-c",
        "transport select jtag",
        "-c",
        f"adapter speed {args.speed}",
        "-c",
        OPENOCD_SCAN_COMMAND,
    ]
    completed = subprocess.run(command, check=False, capture_output=True, text=True)
    args.log.parent.mkdir(parents=True, exist_ok=True)
    args.log.write_text(completed.stdout + completed.stderr, encoding="utf-8")
    sys.stdout.write(completed.stdout)
    sys.stderr.write(completed.stderr)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
