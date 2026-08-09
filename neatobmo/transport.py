"""Transports to reach the Neato: direct USB serial, or the ESP32 WiFi bridge.

Both expose: send(cmd) -> str  (full reply text, 0x1A terminator stripped)
"""
import glob
import socket
import time

import serial

TERM = b"\x1a"


class SerialTransport:
    def __init__(self, port=None, baud=115200):
        if port is None:
            hits = glob.glob("/dev/cu.usbmodem*")
            if not hits:
                raise RuntimeError("no /dev/cu.usbmodem* device (is the Neato plugged in?)")
            port = hits[0]
        self.ser = serial.Serial(port, baud, timeout=0.1)

    def send(self, cmd, timeout=3.0):
        self.ser.reset_input_buffer()
        self.ser.write((cmd + "\n").encode())
        buf = b""
        t0 = time.time()
        while time.time() - t0 < timeout:
            chunk = self.ser.read(65536)
            if chunk:
                buf += chunk
                if TERM in buf:
                    break
        return buf.split(TERM)[0].decode(errors="replace")

    def close(self):
        self.ser.close()


class BridgeTransport:
    """ESP32 WiFi bridge (raw TCP, port 3333)."""

    def __init__(self, host, port=3333):
        self.sock = socket.create_connection((host, port), timeout=3)
        self.sock.settimeout(0.1)

    def send(self, cmd, timeout=3.0):
        # drain any stale bytes
        try:
            while self.sock.recv(65536):
                pass
        except socket.timeout:
            pass
        self.sock.sendall((cmd + "\n").encode())
        buf = b""
        t0 = time.time()
        while time.time() - t0 < timeout:
            try:
                chunk = self.sock.recv(65536)
                if not chunk:
                    break
                buf += chunk
                if TERM in buf:
                    break
            except socket.timeout:
                pass
        return buf.split(TERM)[0].decode(errors="replace")

    def close(self):
        self.sock.close()


def connect(target=None):
    """target: None/'usb' for serial, or an 'ip[:port]' string for the bridge."""
    if target in (None, "usb"):
        return SerialTransport()
    host, _, port = target.partition(":")
    return BridgeTransport(host, int(port) if port else 3333)
