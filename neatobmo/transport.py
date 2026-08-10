"""Transports to reach the Neato: direct USB serial, or the ESP32 WiFi bridge.

Both expose: send(cmd) -> str  (full reply text, 0x1A terminator stripped)
and send_binary(cmd, payload) for the robot's ENQ/checksum/ACK framing —
direct over serial, or relayed through the ESP32's HTTP endpoints.
"""
import glob
import socket
import struct
import time

import serial

from .esp32 import Esp32Client, Esp32Error

TERM = b"\x1a"
ENQ = b"\x05"
ACK = b"\x06"
NAK = b"\x15"


class BinaryTransferError(RuntimeError):
    """The robot rejected or did not enter a binary transfer."""


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
            # read only what has arrived: read(65536) would block out the
            # full serial timeout even after the terminator is already in
            chunk = self.ser.read(self.ser.in_waiting or 1)
            if chunk:
                buf += chunk
                if TERM in buf:
                    break
        return buf.split(TERM)[0].decode(errors="replace")

    def send_binary(self, cmd, payload, timeout=15.0, allow_terminator=False):
        """Send a Neato-style binary transaction.

        Patched XV firmware uses the updater's existing framing:

          <cmd> Size <payload bytes + checksum bytes>\r
          robot: ENQ
          host:  payload + uint32_le(sum(payload))
          robot: ACK

        ``noburn``/firmware-upload commands use the same wire format, so the
        host side can be tested independently of the PlaySound patch.
        """
        payload = bytes(payload)
        self.ser.reset_input_buffer()
        header = f"{cmd} Size {len(payload) + 4}\r".encode()
        self.ser.write(header)

        reply = self._read_until({ENQ, TERM, NAK}, timeout)
        if ENQ not in reply:
            text = reply.replace(TERM, b"").decode(errors="replace").strip()
            raise BinaryTransferError(
                f"robot did not request binary data for {cmd!r}: {text or reply.hex()}"
            )

        self.ser.write(payload)
        self.ser.write(struct.pack("<I", sum(payload) & 0xFFFFFFFF))
        self.ser.flush()

        reply = self._read_until({ACK, NAK, TERM}, timeout)
        if allow_terminator and TERM in reply and NAK not in reply:
            return reply
        if ACK not in reply:
            reason = "NAK" if NAK in reply else "transfer ended without ACK"
            detail = reply.replace(TERM, b"").decode(errors="replace").strip()
            if detail:
                reason = f"{reason}: {detail}"
            raise BinaryTransferError(f"robot rejected {cmd!r}: {reason}")
        return reply

    def _read_until(self, markers, timeout):
        buf = bytearray()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            chunk = self.ser.read(65536)
            if not chunk:
                continue
            buf.extend(chunk)
            if any(marker in chunk for marker in markers):
                break
        return bytes(buf)

    def close(self):
        self.ser.close()


class BridgeTransport:
    """ESP32 WiFi bridge (raw TCP, port 3333)."""

    def __init__(self, host, port=3333, esp32=None):
        self.host = host
        self.esp32 = esp32 or Esp32Client(f"http://{host}")
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

    def send_binary(self, cmd, payload, timeout=90.0):
        """Relay a binary transaction through the ESP32's HTTP endpoints.

        The ESP32 firmware runs the actual ENQ/checksum/ACK exchange on its
        USB link (POST /soundbank -> ``Upload sound``).  The SHA-256 header
        lets the firmware verify the staged bytes before touching the robot.
        """
        payload = bytes(payload)
        if cmd != "Upload sound":
            raise BinaryTransferError(
                f"the ESP32 bridge only relays sound-bank uploads, not {cmd!r}")
        try:
            self.esp32.upload_soundbank(payload)
        except Esp32Error as exc:
            raise BinaryTransferError(str(exc))
        return ACK + TERM

    def close(self):
        self.sock.close()


def connect(target=None):
    """target: None/'usb' for serial, or an 'ip[:port]' string for the bridge."""
    if target in (None, "usb"):
        return SerialTransport()
    host, _, port = target.partition(":")
    return BridgeTransport(host, int(port) if port else 3333)
