#!/usr/bin/env python3
"""Probe whether Neato P6 commands run while a SOUND upload is receiving.

The probe is deliberately fail-closed:

* the only USB upload command is ``Upload sound noburn``;
* the only accepted payload is the exact recovery-tested vendor sound bank;
* the only P6 command sent is the read-only ``GetVersion`` command;
* P6 must identify the same allowlisted XV-12 before the upload starts;
* after ENQ, the complete byte-exact frame is always sent exactly once.

This tool does not retarget NAND, upload application code, extract a key, or
claim that the updater's RAM buffer is executable. Its sole hardware question
is whether the P6 command parser remains live concurrently with the USB binary
receiver. A positive result is a prerequisite for later TOCTOU research, not
proof of a writable destination race.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import socket
import struct
import threading
import time
from typing import Any, Callable

try:
    import serial  # type: ignore
except ModuleNotFoundError:  # Allows offline inspection/tests without pyserial.
    serial = None  # type: ignore


BANK_BYTES = 770_048
BANK_SHA256 = "d3969779a6a195812d72b6859454de004ea45beefdee6f1b5c50a2632564b64a"
FRAMED_BYTES = BANK_BYTES + 4
USB_COMMAND = "Upload sound noburn"
P6_COMMAND = b"GetVersion\r"
TARGET_SERIAL = b"WTD41611DD,0037829,P"
ALLOWED_SOFTWARE = (
    b"Software,2,5,15893",  # installed application
    b"Software,2,4,15667",  # BACK-selected factory application
)
CONFIRMATION = "RUN NO-WRITE P6 CONCURRENCY PROBE ON WTD41611DD-0037829-P"

ENQ = b"\x05"
ACK = b"\x06"
NAK = b"\x15"
TERM = b"\x1a"


class ProbeSafetyError(RuntimeError):
    """Raised when a fail-closed precondition is not satisfied."""


def validate_bank(bank: bytes) -> str:
    digest = hashlib.sha256(bank).hexdigest()
    if len(bank) != BANK_BYTES or digest != BANK_SHA256:
        raise ProbeSafetyError(
            "refusing unexpected bank: "
            f"expected bytes={BANK_BYTES} sha256={BANK_SHA256}; "
            f"got bytes={len(bank)} sha256={digest}"
        )
    return digest


def require_confirmation(value: str) -> None:
    if value != CONFIRMATION:
        raise ProbeSafetyError(f"confirmation must exactly equal: {CONFIRMATION}")


def require_target(data: bytes, *, channel: str) -> str:
    if TARGET_SERIAL not in data:
        raise ProbeSafetyError(f"{channel} did not identify the approved robot")
    for software in ALLOWED_SOFTWARE:
        if software in data:
            return software.decode("ascii")
    raise ProbeSafetyError(f"{channel} reported an unapproved software version")


def build_upload_header() -> bytes:
    return f"{USB_COMMAND} Size {FRAMED_BYTES}\r".encode("ascii")


def build_framed_payload(bank: bytes) -> tuple[bytes, int]:
    validate_bank(bank)
    checksum = sum(bank) & 0xFFFFFFFF
    return bank + struct.pack("<I", checksum), checksum


def validate_injection_offset(offset: int) -> None:
    # Keep the probe away from both the sound directory/header and the checksum
    # tail. This does not change bytes; it only chooses when P6 GetVersion is sent.
    minimum = 8 * 512
    maximum = BANK_BYTES - (8 * 512)
    if not minimum <= offset <= maximum:
        raise ProbeSafetyError(
            f"injection offset must be between {minimum} and {maximum} payload bytes"
        )


def classify_usb_markers(data: bytes) -> list[str]:
    labels: list[str] = []
    for marker, label in ((ENQ, "ENQ"), (ACK, "ACK"), (NAK, "NAK"), (TERM, "TERM")):
        if marker in data:
            labels.append(label)
    return labels


def classify_p6_capture(data: bytes) -> dict[str, bool]:
    lower = data.lower()
    return {
        "target_getversion": TARGET_SERIAL.lower() in lower
        and any(token.lower() in lower for token in ALLOWED_SOFTWARE),
        "sound_type": b"type" in lower and b"sound" in lower,
        "no_write_option": b"options" in lower and b"nowrite" in lower,
        "full_frame_received": b"upload complete" in lower
        and str(FRAMED_BYTES).encode("ascii") in lower,
        "nand_write_blocked": b"nandflashwrite() fail" in lower,
        "nand_write_ok": b"nandflashwrite() ok" in lower,
        "application_region_seen": b"region=0x10000" in lower,
        "sound_region_seen": b"region=0x400000" in lower,
    }


def read_until(
    connection: Any,
    markers: tuple[bytes, ...],
    timeout: float,
) -> bytes:
    data = bytearray()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        chunk = connection.read(getattr(connection, "in_waiting", 0) or 1)
        if not chunk:
            continue
        data.extend(chunk)
        if any(marker in data for marker in markers):
            break
    return bytes(data)


def usb_command(connection: Any, command: str, timeout: float = 5.0) -> bytes:
    connection.reset_input_buffer()
    connection.write((command + "\n").encode("ascii"))
    return read_until(connection, (TERM,), timeout)


def require_pyserial() -> None:
    if serial is None:
        raise ProbeSafetyError("pyserial is required for --execute-noburn-probe")


class P6Collector:
    """One full-duplex TCP client for the ESP32 P6 bridge on port 3334."""

    def __init__(self, host: str, port: int, timeout: float = 5.0) -> None:
        self._socket = socket.create_connection((host, port), timeout=timeout)
        self._socket.settimeout(0.2)
        self._data = bytearray()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._error: str | None = None
        self._thread = threading.Thread(target=self._read_loop, daemon=True)
        self._thread.start()

    def _read_loop(self) -> None:
        while not self._stop.is_set():
            try:
                chunk = self._socket.recv(4096)
            except socket.timeout:
                continue
            except OSError as exc:
                if not self._stop.is_set():
                    self._error = str(exc)
                return
            if not chunk:
                return
            with self._lock:
                self._data.extend(chunk)

    def mark(self) -> int:
        with self._lock:
            return len(self._data)

    def snapshot(self, start: int = 0) -> bytes:
        with self._lock:
            return bytes(self._data[start:])

    def send_getversion(self) -> None:
        # There is intentionally no caller-supplied P6 command surface.
        self._socket.sendall(P6_COMMAND)

    def wait_for(self, predicate: Callable[[bytes], bool], start: int, timeout: float) -> bytes:
        deadline = time.monotonic() + timeout
        latest = b""
        while time.monotonic() < deadline:
            latest = self.snapshot(start)
            if predicate(latest):
                return latest
            if self._error:
                raise ProbeSafetyError(f"P6 collector failed: {self._error}")
            time.sleep(0.05)
        return latest

    def close(self) -> None:
        self._stop.set()
        try:
            self._socket.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self._socket.close()
        self._thread.join(timeout=1.0)

    def __enter__(self) -> "P6Collector":
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close()


def preflight_usb(connection: Any) -> dict[str, Any]:
    version = usb_command(connection, "GetVersion")
    software = require_target(version, channel="USB GetVersion")
    upload_help = usb_command(connection, "Help Upload")
    help_lower = upload_help.lower()
    if b"sound" not in help_lower or b"noburn" not in help_lower:
        raise ProbeSafetyError("live Help Upload did not advertise sound + noburn")
    return {
        "software": software,
        "getversion_sha256": hashlib.sha256(version).hexdigest(),
        "upload_help_sha256": hashlib.sha256(upload_help).hexdigest(),
        "target_identity_ok": True,
        "upload_help_ok": True,
    }


def stream_with_injection(
    connection: Any,
    framed: bytes,
    collector: P6Collector,
    *,
    injection_offset: int,
    chunk_size: int,
    chunk_delay_ms: float,
) -> tuple[int, int, str | None]:
    """Send one exact frame and inject one fixed read-only P6 command.

    Once called, this function does not intentionally stop early: after ENQ the
    robot is waiting for a declared byte count, so completing the exact frame is
    safer than abandoning the receiver in a partial-transfer state.
    """
    sent = 0
    injection_mark = -1
    injection_error: str | None = None
    while sent < len(framed):
        if injection_mark < 0 and sent < injection_offset:
            stop = min(sent + chunk_size, injection_offset)
        else:
            stop = min(sent + chunk_size, len(framed))

        if stop > sent:
            connection.write(framed[sent:stop])
            sent = stop

        if injection_mark < 0 and sent == injection_offset:
            connection.flush()
            injection_mark = collector.mark()
            try:
                collector.send_getversion()
            except OSError as exc:
                # Never abandon the declared USB frame because the independent
                # P6 probe failed. Complete the exact frame, then report the P6
                # error in the result.
                injection_error = str(exc)

        if chunk_delay_ms:
            time.sleep(chunk_delay_ms / 1000.0)

    connection.flush()
    if injection_mark < 0:
        raise ProbeSafetyError("internal error: P6 injection point was not reached")
    return sent, injection_mark, injection_error


def run_probe(
    *,
    bank: bytes,
    usb_port: str,
    p6_host: str,
    p6_port: int,
    injection_offset: int,
    chunk_size: int,
    chunk_delay_ms: float,
) -> tuple[dict[str, Any], bytes]:
    require_pyserial()
    digest = validate_bank(bank)
    validate_injection_offset(injection_offset)
    if not 512 <= chunk_size <= 65_536:
        raise ProbeSafetyError("chunk size must be between 512 and 65536 bytes")
    if not 0.0 <= chunk_delay_ms <= 20.0:
        raise ProbeSafetyError("chunk delay must be between 0 and 20 milliseconds")

    framed, checksum = build_framed_payload(bank)
    result: dict[str, Any] = {
        "experiment": "p6_usb_upload_concurrency",
        "command": USB_COMMAND,
        "p6_command_hex": P6_COMMAND.hex(),
        "bank_bytes": len(bank),
        "bank_sha256": digest,
        "framed_bytes": len(framed),
        "checksum_hex": f"0x{checksum:08x}",
        "usb_port": usb_port,
        "p6_endpoint": f"{p6_host}:{p6_port}",
        "injection_offset": injection_offset,
        "chunk_size": chunk_size,
        "chunk_delay_ms": chunk_delay_ms,
        "upload_started": False,
        "writes_performed": False,
    }

    with P6Collector(p6_host, p6_port) as collector:
        # Gate 1: prove that P6 RX is an input console for this exact robot.
        time.sleep(0.25)
        baseline_mark = collector.mark()
        collector.send_getversion()
        baseline = collector.wait_for(
            lambda data: classify_p6_capture(data)["target_getversion"],
            baseline_mark,
            4.0,
        )
        baseline_flags = classify_p6_capture(baseline)
        result["p6_idle_probe"] = {
            "bytes": len(baseline),
            "sha256": hashlib.sha256(baseline).hexdigest(),
            **baseline_flags,
        }
        if not baseline_flags["target_getversion"]:
            raise ProbeSafetyError(
                "P6 did not return this robot's GetVersion response; "
                "USB upload was not started"
            )
        require_target(baseline, channel="P6 idle GetVersion")

        # Gate 2: independently pin the USB endpoint and updater feature.
        with serial.Serial(usb_port, 115200, timeout=0.1) as connection:
            result["usb_preflight"] = preflight_usb(connection)

            upload_mark = collector.mark()
            connection.reset_input_buffer()
            connection.write(build_upload_header())
            opening = read_until(connection, (ENQ, NAK, TERM), 8.0)
            result["usb_opening_hex"] = opening.hex()
            result["usb_opening_markers"] = classify_usb_markers(opening)
            if ENQ not in opening:
                raise ProbeSafetyError("robot did not emit ENQ; no bank bytes were sent")

            result["upload_started"] = True
            result["writes_performed"] = None
            started = time.monotonic()
            sent, injection_mark, injection_error = stream_with_injection(
                connection,
                framed,
                collector,
                injection_offset=injection_offset,
                chunk_size=chunk_size,
                chunk_delay_ms=chunk_delay_ms,
            )
            result["bytes_sent"] = sent
            result["p6_injection_error"] = injection_error
            closing = read_until(connection, (ACK, NAK, TERM), 30.0)
            result["transfer_seconds"] = round(time.monotonic() - started, 3)
            result["usb_closing_hex"] = closing.hex()
            result["usb_closing_markers"] = classify_usb_markers(closing)
            if NAK in closing:
                result["transfer_status"] = "robot_returned_nak"
            elif TERM in closing and ACK not in closing:
                result["transfer_status"] = "noburn_command_complete"
            elif ACK in closing:
                result["transfer_status"] = "unexpected_ack_on_noburn"
            else:
                result["transfer_status"] = "no_terminal_marker"

            # Capture the experimental P6 window before issuing the post-probe
            # USB GetVersion health check. This prevents a hypothetical USB-side
            # version trace from being misclassified as the injected P6 response.
            try:
                p6_after_injection = collector.wait_for(
                    lambda data: (
                        classify_p6_capture(data)["nand_write_blocked"]
                        or classify_p6_capture(data)["nand_write_ok"]
                    ),
                    injection_mark,
                    6.0,
                )
            except ProbeSafetyError as exc:
                result["p6_collection_error"] = str(exc)
                p6_after_injection = collector.snapshot(injection_mark)
            time.sleep(0.1)
            p6_after_injection = collector.snapshot(injection_mark)
            upload_p6 = collector.snapshot(upload_mark)

            # The command string is no-write, but only the isolated P6 window
            # above can confirm the hidden option and terminal NAND refusal.
            time.sleep(0.15)
            post_version = usb_command(connection, "GetVersion")
            try:
                post_software = require_target(
                    post_version, channel="post-probe USB GetVersion"
                )
                result["post_usb_health"] = {
                    "software": post_software,
                    "healthy": True,
                    "sha256": hashlib.sha256(post_version).hexdigest(),
                }
            except ProbeSafetyError as exc:
                result["post_usb_health"] = {
                    "healthy": False,
                    "error": str(exc),
                    "sha256": hashlib.sha256(post_version).hexdigest(),
                }

        all_p6 = collector.snapshot(0)
        concurrent_flags = classify_p6_capture(p6_after_injection)
        upload_flags = classify_p6_capture(upload_p6)
        result["p6_during_upload"] = {
            "bytes_after_injection": len(p6_after_injection),
            "sha256_after_injection": hashlib.sha256(p6_after_injection).hexdigest(),
            "concurrent_getversion_observed": concurrent_flags["target_getversion"],
            **upload_flags,
        }
        result["no_write_confirmed"] = (
            upload_flags["no_write_option"]
            and upload_flags["full_frame_received"]
            and upload_flags["nand_write_blocked"]
            and not upload_flags["nand_write_ok"]
        )
        result["application_region_observed"] = upload_flags["application_region_seen"]
        if upload_flags["nand_write_ok"]:
            result["writes_performed"] = True
        elif result["no_write_confirmed"]:
            result["writes_performed"] = False
        else:
            result["writes_performed"] = None
        result["conclusion"] = (
            "P6 command parser remained live during USB receive"
            if concurrent_flags["target_getversion"]
            else "No P6 GetVersion response was observed after the injection point"
        )
        return result, all_p6


def write_exclusive(path: Path | None, data: bytes) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as output:
        output.write(data)


def write_result(path: Path | None, result: dict[str, Any]) -> None:
    rendered = (json.dumps(result, indent=2, sort_keys=True) + "\n").encode("utf-8")
    print(rendered.decode("utf-8"), end="")
    write_exclusive(path, rendered)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bank", type=Path)
    parser.add_argument("--usb-port", help="explicit Neato USB CDC port")
    parser.add_argument("--p6-host", help="explicit ESP32 P6 bridge host")
    parser.add_argument("--p6-port", type=int, default=3334)
    parser.add_argument("--injection-offset", type=int, default=BANK_BYTES // 2)
    parser.add_argument("--chunk-size", type=int, default=4096)
    parser.add_argument("--chunk-delay-ms", type=float, default=5.0)
    parser.add_argument("--result", type=Path)
    parser.add_argument("--p6-output", type=Path)
    parser.add_argument("--execute-noburn-probe", action="store_true")
    parser.add_argument("--confirmation", default="")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    bank = args.bank.read_bytes()
    digest = validate_bank(bank)
    validate_injection_offset(args.injection_offset)

    if not args.execute_noburn_probe:
        write_result(
            args.result,
            {
                "mode": "offline_preflight",
                "bank_bytes": len(bank),
                "bank_sha256": digest,
                "command": USB_COMMAND,
                "p6_command_hex": P6_COMMAND.hex(),
                "injection_offset": args.injection_offset,
                "writes_performed": False,
                "next_gate": "supply explicit endpoints, execution flag, and exact confirmation",
            },
        )
        return 0

    require_confirmation(args.confirmation)
    if not args.usb_port or not args.p6_host:
        raise ProbeSafetyError("--usb-port and --p6-host are required for execution")

    result, p6_data = run_probe(
        bank=bank,
        usb_port=args.usb_port,
        p6_host=args.p6_host,
        p6_port=args.p6_port,
        injection_offset=args.injection_offset,
        chunk_size=args.chunk_size,
        chunk_delay_ms=args.chunk_delay_ms,
    )
    write_exclusive(args.p6_output, p6_data)
    write_result(args.result, result)
    return 0 if result["no_write_confirmed"] else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ProbeSafetyError as exc:
        raise SystemExit(f"refusing probe: {exc}") from exc
