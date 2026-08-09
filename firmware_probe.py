#!/usr/bin/env python3
"""Read-only probe of the XV-12's Upload/flash facilities.

The normal transport splits replies at 0x1A, but flash dumps are binary and
can contain 0x1A anywhere — so this captures raw bytes until the line goes
quiet. Only ever sends read-oriented commands (dump/readflash); never sends
data, never burns.

    python3 firmware_probe.py "Upload dump"
    python3 firmware_probe.py "Upload readflash" -o dumps/region.bin
"""
import argparse
import glob
import sys
import time

import serial


def raw_cmd(ser, cmd, quiet=2.0, hard_limit=120.0):
    ser.reset_input_buffer()
    ser.write((cmd + "\n").encode())
    buf = b""
    last = time.time()
    t0 = last
    while True:
        chunk = ser.read(65536)
        now = time.time()
        if chunk:
            buf += chunk
            last = now
            sys.stderr.write(f"\r{len(buf)} bytes")
        if now - last > quiet or now - t0 > hard_limit:
            break
    sys.stderr.write("\n")
    return buf


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd")
    ap.add_argument("-o", "--out", help="write raw capture to file")
    ap.add_argument("--quiet", type=float, default=2.0)
    args = ap.parse_args()

    lowered = args.cmd.lower()
    if any(w in lowered for w in ("xmodem", "reboot")) or \
       ("size" in lowered and "readflash" not in lowered) or \
       ("upload" in lowered and not any(w in lowered for w in ("dump", "readflash", "help"))):
        sys.exit("refusing: this tool only sends read-only upload commands")

    port = glob.glob("/dev/cu.usbmodem*")[0]
    ser = serial.Serial(port, 115200, timeout=0.1)
    data = raw_cmd(ser, args.cmd, quiet=args.quiet)
    if args.out:
        with open(args.out, "wb") as f:
            f.write(data)
        print(f"{len(data)} bytes -> {args.out}")
    else:
        text = data.decode(errors="replace")
        print(text[:4000])
        if len(text) > 4000:
            print(f"... [{len(data)} bytes total]")
    ser.close()
