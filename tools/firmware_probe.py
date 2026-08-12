#!/usr/bin/env python3
"""Read-only probe of the XV-12's Upload/flash facilities.

The normal transport splits replies at 0x1A, but flash dumps are binary and
can contain 0x1A anywhere — so this captures raw bytes until the line goes
quiet. Only ever sends read-oriented commands (dump/readflash); never sends
data, never burns.

    python3 firmware_probe.py "Upload dump"
    python3 tools/firmware_probe.py "Upload readflash" -o region.bin
"""
import argparse
import sys
import time

import serial
from serial.tools import list_ports


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


CAN = 0x18


def xmodem_recv(ser, initiate_cmd):
    """Receive an xmodem transfer the robot initiates after `initiate_cmd`,
    using the PyPI `xmodem` library. Read-only (robot sends, we ACK)."""
    import io
    from xmodem import XMODEM

    ser.reset_input_buffer()
    ser.write((initiate_cmd + "\n").encode())
    time.sleep(0.5)

    def getc(size, timeout=1):
        old = ser.timeout
        ser.timeout = timeout
        d = ser.read(size)
        ser.timeout = old
        return d or None

    def putc(d, timeout=1):
        return ser.write(d)

    sink = io.BytesIO()
    ok = XMODEM(getc, putc).recv(sink, crc_mode=1, retry=8)
    if not ok:
        sys.exit("no xmodem transfer started (robot never sent data)")
    return sink.getvalue()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd")
    ap.add_argument("--port", help="explicit Neato USB serial port")
    ap.add_argument("-o", "--out", help="write raw capture to file")
    ap.add_argument("--quiet", type=float, default=2.0)
    ap.add_argument("--xmodem-recv", action="store_true",
                    help="receive an xmodem transfer (readflash dumps)")
    args = ap.parse_args()

    lowered = args.cmd.lower()
    if "reboot" in lowered or \
       ("xmodem" in lowered and not (args.xmodem_recv and "readflash" in lowered)) or \
       ("size" in lowered and "readflash" not in lowered) or \
       ("upload" in lowered and not any(w in lowered for w in ("dump", "readflash", "help"))):
        sys.exit("refusing: this tool only sends read-only upload commands")

    if args.port:
        port = args.port
    else:
        ports = [
            candidate.device
            for candidate in list_ports.comports()
            if candidate.vid == 0x2108 and candidate.pid == 0x780B
        ]
        if len(ports) != 1:
            sys.exit(f"expected exactly one Neato USB port, found: {ports}")
        port = ports[0]
    ser = serial.Serial(port, 115200, timeout=0.1)
    if args.xmodem_recv:
        try:
            data = xmodem_recv(ser, args.cmd)
        finally:
            ser.write(bytes([CAN, CAN]))     # make sure the robot leaves xfer mode
    else:
        data = raw_cmd(ser, args.cmd, quiet=args.quiet)
    if args.out:
        with open(args.out, "xb") as f:
            f.write(data)
        print(f"{len(data)} bytes -> {args.out}")
    else:
        text = data.decode(errors="replace")
        print(text[:4000])
        if len(text) > 4000:
            print(f"... [{len(data)} bytes total]")
    ser.close()
