#!/usr/bin/env python3
"""Capture the ESP32-S3 USB console (ESP-IDF logs + mirrored Neato P6 bytes).

Reads /dev/cu.usbmodem... and tees everything to ~/neato-p6-boot.txt while
printing live. Does NOT pulse the ESP32 reset, so the P6 bridge stays up while
you power on the Neato. Ctrl-C to stop.
"""
import sys
import serial

PORT = sys.argv[1] if len(sys.argv) > 1 else "/dev/cu.usbmodem5C381965721"
OUT = sys.argv[2] if len(sys.argv) > 2 else "neato-p6-boot.txt"

p = serial.Serial(PORT, 115200, timeout=0.2)
print(f"[capture] {PORT} -> {OUT}   (Ctrl-C to stop)", file=sys.stderr)
with open(OUT, "wb") as f:
    try:
        while True:
            d = p.read(4096)
            if d:
                sys.stdout.buffer.write(d)
                sys.stdout.flush()
                f.write(d)
                f.flush()
    except KeyboardInterrupt:
        print("\n[capture] stopped", file=sys.stderr)
