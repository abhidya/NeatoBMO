#!/usr/bin/env python3
"""Scan-only P10 helper for externally driven Neato firmware transitions.

This module deliberately does not open the Neato USB serial port and cannot
send an ``Upload`` command.  It exists only to share the exact read-only OpenOCD
command shape used when the operator is collecting P10 observations before,
during, or after some separately authorized firmware action.
"""

from __future__ import annotations


DANGEROUS_OPENOCD_TOKENS = (
    "erase",
    "program",
    "write_image",
    "flash erase",
    "nand write",
    "load_image",
    "mww",
    "mwh",
    "mwb",
    "write_memory",
    "gpnvm",
    "reset init",
    "halt",
    "target create",
    "jtag newtap",
)


def openocd_command(speed: int) -> list[str]:
    if speed < 1 or speed > 100:
        raise ValueError("speed must be in the conservative 1..100 kHz range")
    commands = [
        "adapter driver cmsis-dap",
        "transport select jtag",
        f"adapter speed {speed}",
        "init; scan_chain; shutdown",
    ]
    lowered = "\n".join(commands).lower()
    for token in DANGEROUS_OPENOCD_TOKENS:
        if token in lowered:
            raise RuntimeError(f"unsafe OpenOCD token in scan command: {token}")
    argv = ["openocd"]
    for command in commands:
        argv.extend(["-c", command])
    return argv


if __name__ == "__main__":
    print(" ".join(openocd_command(10)))
