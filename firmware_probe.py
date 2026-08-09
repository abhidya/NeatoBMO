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


SOH, STX, EOT, ACK, NAK, CAN, CRC = 0x01, 0x02, 0x04, 0x06, 0x15, 0x18, ord("C")


def xmodem_recv(ser, initiate_cmd, max_bytes=1 << 20):
    """Receive an xmodem transfer the robot initiates after `initiate_cmd`.
    Read-only from the robot's perspective (it sends, we ACK)."""
    ser.reset_input_buffer()
    ser.write((initiate_cmd + "\n").encode())
    time.sleep(0.5)
    data = b""
    crc_mode = True
    for _ in range(20):                       # solicit: 'C' for CRC, fall back to NAK
        ser.write(bytes([CRC if crc_mode else NAK]))
        hdr = ser.read(1)
        t0 = time.time()
        while not hdr and time.time() - t0 < 1.0:
            hdr = ser.read(1)
        if hdr and hdr[0] in (SOH, STX):
            break
        if hdr and hdr[0] == EOT:
            ser.write(bytes([ACK]))
            return data
        crc_mode = not crc_mode
    else:
        sys.exit("no xmodem start (robot never sent SOH); it may not use xmodem for reads")

    while len(data) < max_bytes:
        if not hdr:
            hdr = ser.read(1)
            if not hdr:
                break
        b = hdr[0]
        hdr = b""
        if b == EOT:
            ser.write(bytes([ACK]))
            break
        if b not in (SOH, STX):
            continue
        n = 128 if b == SOH else 1024
        need = 2 + n + (2 if crc_mode else 1)
        pkt = b""
        t0 = time.time()
        while len(pkt) < need and time.time() - t0 < 3.0:
            pkt += ser.read(need - len(pkt))
        if len(pkt) < need:
            ser.write(bytes([NAK]))
            continue
        data += pkt[2:2 + n]
        ser.write(bytes([ACK]))
        sys.stderr.write(f"\r{len(data)} bytes")
    sys.stderr.write("\n")
    return data


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd")
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

    port = glob.glob("/dev/cu.usbmodem*")[0]
    ser = serial.Serial(port, 115200, timeout=0.1)
    if args.xmodem_recv:
        try:
            data = xmodem_recv(ser, args.cmd)
        finally:
            ser.write(bytes([CAN, CAN]))     # make sure the robot leaves xfer mode
    else:
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
