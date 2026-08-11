#!/usr/bin/env python3
"""Capture the ESP32-S3 USB console (ESP-IDF logs + mirrored Neato P6 bytes).

NON-DESTRUCTIVE: opens the output in APPEND mode and, when no path is given,
writes a fresh timestamped file under captures/. We lost an 8 KB P6 sample once
by truncating a reused filename -- never again. Reads the serial port read-only
(never writes to it) and does NOT pulse the ESP32 reset, so the bridge stays up
while you power-cycle the Neato. Ctrl-C to stop.

Usage:
  p6_capture.py [PORT] [OUTFILE]
  PORT     default: first /dev/cu.usbmodem* found
  OUTFILE  default: captures/p6_<epoch>.log  (append mode; never truncates)
"""
import glob
import os
import sys
import time

import serial

def pick_port(arg):
    if arg:
        return arg
    hits = sorted(glob.glob("/dev/cu.usbmodem*"))
    if not hits:
        sys.exit("no /dev/cu.usbmodem* found -- is the ESP32 plugged in?")
    return hits[0]

PORT = pick_port(sys.argv[1] if len(sys.argv) > 1 else None)
if len(sys.argv) > 2:
    OUT = sys.argv[2]
else:
    root = os.path.join(os.path.dirname(__file__), os.pardir, "captures")
    os.makedirs(root, exist_ok=True)
    OUT = os.path.normpath(os.path.join(root, f"p6_{int(time.time())}.log"))

# Configure BEFORE opening so the open() doesn't assert DTR/RTS and reset the
# ESP32 (that would drop the Neato's one-time boot burst). exclusive=True takes
# TIOCEXCL so a second capturer can't silently attach and split the byte stream.
p = serial.Serial()
p.port = PORT
p.baudrate = 115200
p.timeout = 0.2
p.dtr = False
p.rts = False
p.exclusive = True
p.open()
stamp = time.strftime("%Y-%m-%dT%H:%M:%S")
print(f"[capture] {PORT} -> {OUT} (append)  started {stamp}   (Ctrl-C to stop)",
      file=sys.stderr)
# 'ab' = append-binary: existing data is preserved, this run adds to the end.
with open(OUT, "ab") as f:
    f.write(f"\n===== capture session {stamp} on {PORT} =====\n".encode())
    f.flush()
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
