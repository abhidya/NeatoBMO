#!/usr/bin/env python3
"""Probe the Cruz upload-save area with one volatile sentinel transaction."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import struct
import threading
import time

import serial
from serial.tools import list_ports


NEATO_VID = 0x2108
NEATO_PID = 0x780B
TARGET_SERIAL = b"Serial Number,WTD41611DD,0037829,P"
TARGET_MAINBOARD = b"MainBoard Version,7,1,"
ALLOWED_SOFTWARE = (
    b"Software,2,5,15893",
    b"Software,2,7,16621",
    b"Software,3,1,17844",
)
TERM = b"\x1a"
ENQ = b"\x05"
ACK = b"\x06"
NAK = b"\x15"
CAN = b"\x18"
SENTINEL = (b"NEATOBMO-UPLOAD-SAVE-AREA-PROBE-V1|" * 8)[:256]

RAW_COMMANDS = (
    "Upload dump",
    "Upload code dump",
    "Upload dump code",
    "Upload sound dump",
    "Upload dump sound",
    "Upload LDS dump",
    "Upload dump LDS",
    "Upload readflash",
    "Upload code readflash",
    "Upload readflash code",
    "Upload sound readflash",
    "Upload readflash sound",
    "Upload LDS readflash",
    "Upload readflash LDS",
)

XMODEM_COMMANDS = (
    "Upload readflash xmodem",
    "Upload xmodem readflash",
    "Upload code readflash xmodem",
    "Upload code xmodem readflash",
    "Upload sound readflash xmodem",
    "Upload LDS readflash xmodem",
)

SIZE_QUERY_COMMANDS = (
    "Upload dump Size 260",
    "Upload code dump Size 260",
    "Upload code Size 260 dump",
    "Upload sound dump Size 260",
    "Upload LDS dump Size 260",
    "Upload readflash Size 260",
    "Upload code readflash Size 260",
    "Upload code Size 260 readflash",
    "Upload sound readflash Size 260",
    "Upload LDS readflash Size 260",
)

FORBIDDEN_TOKENS = (" erase", " reboot", " size", " burn", "write")
P6_ABORT_MARKERS = (
    b"nandFlashWrite() OK",
    b"nandflashErase",
    b"ERASE",
    b"Loading factory application",
    b"Power On reset:",
)


class ProbeSafetyError(RuntimeError):
    pass


def neato_ports() -> list[str]:
    return [
        port.device
        for port in list_ports.comports()
        if port.vid == NEATO_VID and port.pid == NEATO_PID
    ]


def require_fixed_read_command(command: str) -> None:
    lowered = f" {command.lower()}"
    if command not in RAW_COMMANDS + XMODEM_COMMANDS:
        raise ProbeSafetyError(f"command is not in the fixed matrix: {command}")
    if any(token in lowered for token in FORBIDDEN_TOKENS):
        raise ProbeSafetyError(f"forbidden token in matrix command: {command}")


def require_fixed_size_query(command: str) -> None:
    if command not in SIZE_QUERY_COMMANDS:
        raise ProbeSafetyError(f"command is not in the fixed size-query matrix: {command}")
    lowered = command.lower()
    if "reboot" in lowered or "erase" in lowered or "noburn" in lowered:
        raise ProbeSafetyError(f"forbidden token in size-query command: {command}")


def read_until_quiet(
    connection: serial.Serial, *, quiet: float = 1.0, hard_limit: float = 8.0
) -> bytes:
    data = bytearray()
    started = last = time.monotonic()
    while time.monotonic() - started < hard_limit:
        chunk = connection.read(connection.in_waiting or 1)
        if chunk:
            data.extend(chunk)
            last = time.monotonic()
        elif time.monotonic() - last >= quiet:
            break
    return bytes(data)


def raw_command(
    connection: serial.Serial,
    command: str,
    *,
    quiet: float = 1.0,
    hard_limit: float = 8.0,
) -> bytes:
    connection.reset_input_buffer()
    connection.write((command + "\n").encode())
    return read_until_quiet(connection, quiet=quiet, hard_limit=hard_limit)


def read_large_log(connection: serial.Serial, command: str) -> tuple[bytes, bool]:
    """Capture a large log with one bounded extension after the main window."""
    data = bytearray(
        raw_command(connection, command, quiet=2.0, hard_limit=30.0)
    )
    extended = TERM not in data
    if extended:
        data.extend(read_until_quiet(connection, quiet=2.0, hard_limit=15.0))
    return bytes(data), extended


def require_identity(reply: bytes) -> None:
    if TARGET_SERIAL not in reply or TARGET_MAINBOARD not in reply:
        raise ProbeSafetyError("connected robot identity/mainboard did not match")
    if not any(version in reply for version in ALLOWED_SOFTWARE):
        raise ProbeSafetyError("connected robot software is outside the supported set")


def send_sentinel(connection: serial.Serial) -> tuple[bytes, bytes]:
    connection.reset_input_buffer()
    header = f"Upload code noburn Size {len(SENTINEL) + 4}\r".encode()
    connection.write(header)
    opening = read_until_quiet(connection, quiet=0.2, hard_limit=5.0)
    if ENQ not in opening:
        raise ProbeSafetyError("robot did not request the fixed sentinel payload")
    checksum = struct.pack("<I", sum(SENTINEL) & 0xFFFFFFFF)
    connection.write(SENTINEL + checksum)
    connection.flush()
    closing = read_until_quiet(connection, quiet=1.0, hard_limit=20.0)
    if NAK in closing:
        raise ProbeSafetyError("robot rejected the sentinel transaction")
    if ACK not in closing and TERM not in closing:
        raise ProbeSafetyError("sentinel transaction ended without ACK/terminator")
    return opening, closing


def probe_xmodem_start(connection: serial.Serial, command: str) -> tuple[bytes, bool]:
    require_fixed_read_command(command)
    connection.reset_input_buffer()
    connection.write((command + "\n").encode())
    data = bytearray()
    payload_start = False
    try:
        for _ in range(4):
            connection.write(b"C")
            connection.flush()
            chunk = read_until_quiet(connection, quiet=0.3, hard_limit=1.0)
            data.extend(chunk)
            if b"\x01" in chunk or b"\x02" in chunk:
                payload_start = True
                break
    finally:
        connection.write(CAN + CAN + b"\n")
        connection.flush()
        data.extend(read_until_quiet(connection, quiet=0.2, hard_limit=1.0))
    return bytes(data), payload_start


def probe_size_query(
    connection: serial.Serial, command: str
) -> tuple[bytes, bool, bool]:
    require_fixed_size_query(command)
    connection.reset_input_buffer()
    connection.write((command + "\r").encode())
    data = bytearray(read_until_quiet(connection, quiet=0.5, hard_limit=3.0))
    requested_upload = ENQ in data
    cancel_confirmed = False
    if requested_upload:
        connection.write(CAN + CAN + b"\n")
        connection.flush()
        post_cancel = read_until_quiet(connection, quiet=0.2, hard_limit=1.0)
        data.extend(post_cancel)
        cancel_confirmed = TERM in post_cancel
    return bytes(data), requested_upload, cancel_confirmed


def classify(data: bytes) -> str:
    if not data:
        return "no-response"
    allowed = set(range(0x20, 0x7F)) | {0x05, 0x06, 0x15, 0x18, 0x1A, 9, 10, 13}
    known_safe_text = all(byte in allowed for byte in data)
    if SENTINEL in data and known_safe_text:
        return "project-sentinel-returned"
    if known_safe_text:
        return "text-or-protocol-control"
    return "non-text-private-review-required"


def digest_record(command: str, data: bytes, payload_start: bool = False) -> dict[str, object]:
    classification = classify(data)
    record: dict[str, object] = {
        "command": command,
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "classification": classification,
        "xmodem_payload_start": payload_start,
        "terminator_seen": TERM in data,
    }
    if classification in {"text-or-protocol-control", "project-sentinel-returned"}:
        record["escaped_text"] = data.decode(errors="backslashreplace")
    return record


def capture_p6(
    port: str, stop: threading.Event, abort: threading.Event, sink: bytearray
) -> None:
    with serial.Serial(port, 115200, timeout=0.1) as connection:
        while not stop.is_set():
            chunk = connection.read(connection.in_waiting or 1)
            if chunk:
                sink.extend(chunk)
                if any(marker in sink for marker in P6_ABORT_MARKERS):
                    abort.set()


def preserve_or_publish(
    records: list[dict[str, object]],
    private_blobs: list[tuple[str, bytes]],
    command: str,
    data: bytes,
    *,
    payload_start: bool = False,
) -> bool:
    record = digest_record(command, data, payload_start)
    classification = record["classification"]
    should_stop = classification == "non-text-private-review-required" or payload_start
    if should_stop:
        name = f"breakthrough-{len(private_blobs) + 1:02d}.bin"
        private_blobs.append((name, data))
        record["private_artifact"] = name
    records.append(record)
    return should_stop


def run(
    neato_port: str, p6_port: str | None
) -> tuple[dict[str, object], bytes, list[tuple[str, bytes]]]:
    p6_data = bytearray()
    stop = threading.Event()
    abort = threading.Event()
    thread = None
    if p6_port:
        thread = threading.Thread(
            target=capture_p6, args=(p6_port, stop, abort, p6_data)
        )
        thread.start()
        time.sleep(0.2)

    records: list[dict[str, object]] = []
    private_blobs: list[tuple[str, bytes]] = []
    stopped_reason = None
    try:
        with serial.Serial(neato_port, 115200, timeout=0.1) as connection:
            before = raw_command(connection, "GetVersion")
            require_identity(before)
            records.append(digest_record("GetVersion before", before))
            records.append(digest_record("Help Upload", raw_command(connection, "Help Upload")))
            records.append(digest_record("GetErr before", raw_command(connection, "GetErr")))

            opening, closing = send_sentinel(connection)
            records.append(digest_record("sentinel opening", opening))
            records.append(digest_record("sentinel closing", closing))

            for command in RAW_COMMANDS:
                if abort.is_set():
                    stopped_reason = "P6 abort marker observed"
                    break
                require_fixed_read_command(command)
                if preserve_or_publish(
                    records, private_blobs, command, raw_command(connection, command)
                ):
                    stopped_reason = f"private review required after {command}"
                    break
                if abort.is_set():
                    stopped_reason = "P6 abort marker observed"
                    break

            if stopped_reason is None:
                for command in SIZE_QUERY_COMMANDS:
                    if abort.is_set():
                        stopped_reason = "P6 abort marker observed"
                        break
                    data, requested_upload, cancel_confirmed = probe_size_query(
                        connection, command
                    )
                    record = digest_record(command, data)
                    record["target_requested_upload_bytes"] = requested_upload
                    record["upload_cancel_confirmed"] = cancel_confirmed
                    if requested_upload and not cancel_confirmed:
                        name = f"breakthrough-{len(private_blobs) + 1:02d}.bin"
                        private_blobs.append((name, data))
                        record.pop("escaped_text", None)
                        record["classification"] = (
                            "upload-receiver-cancel-unconfirmed-private"
                        )
                        record["private_artifact"] = name
                        records.append(record)
                        stopped_reason = (
                            "size query selected upload receiver; cancel not confirmed"
                        )
                        break
                    classification = record["classification"]
                    if classification == "non-text-private-review-required":
                        name = f"breakthrough-{len(private_blobs) + 1:02d}.bin"
                        private_blobs.append((name, data))
                        record["private_artifact"] = name
                        records.append(record)
                        stopped_reason = f"private review required after {command}"
                        break
                    records.append(record)
                    if abort.is_set():
                        stopped_reason = "P6 abort marker observed"
                        break

            if stopped_reason is None:
                for command in XMODEM_COMMANDS:
                    if abort.is_set():
                        stopped_reason = "P6 abort marker observed"
                        break
                    data, payload_start = probe_xmodem_start(connection, command)
                    if preserve_or_publish(
                        records,
                        private_blobs,
                        command,
                        data,
                        payload_start=payload_start,
                    ):
                        stopped_reason = f"XMODEM/private review required after {command}"
                        break
                    if abort.is_set():
                        stopped_reason = "P6 abort marker observed"
                        break

            if stopped_reason is None and abort.is_set():
                stopped_reason = "P6 abort marker observed"
            if stopped_reason is None:
                records.append(digest_record("GetErr after", raw_command(connection, "GetErr")))
                after = raw_command(connection, "GetVersion")
                require_identity(after)
                records.append(digest_record("GetVersion after", after))

        # These commands can reset or remove USB on older stock builds. Run each
        # in its own connection only after the core matrix and final identity
        # have already been preserved.
        if stopped_reason is None and abort.is_set():
            stopped_reason = "P6 abort marker observed"
        for command in (() if stopped_reason else ("GetSysLog", "GetLifeStatLog")):
            try:
                with serial.Serial(neato_port, 115200, timeout=0.1) as connection:
                    data, extended = read_large_log(connection, command)
                    record = digest_record(command, data)
                    record["capture_extended_after_initial_limit"] = extended
                    records.append(record)
            except (serial.SerialException, OSError) as exc:
                records.append({
                    "command": command,
                    "classification": "usb-disconnected-during-log-query",
                    "error": str(exc),
                })
    finally:
        if thread:
            stop.set()
            thread.join(timeout=2.0)

    result = {
        "schema": "neatobmo-upload-save-area-probe/v1",
        "neato_port": neato_port,
        "p6_port": p6_port,
        "sentinel_bytes": len(SENTINEL),
        "sentinel_sha256": hashlib.sha256(SENTINEL).hexdigest(),
        "persistent_write_requested": False,
        "auto_retries": 0,
        "records": records,
        "p6_bytes": len(p6_data),
        "p6_sha256": hashlib.sha256(p6_data).hexdigest(),
        "p6_classification": classify(bytes(p6_data)),
        "stopped_reason": stopped_reason,
    }
    return result, bytes(p6_data), private_blobs


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--neato-port")
    parser.add_argument("--p6-port")
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--private-dir", type=Path, required=True)
    parser.add_argument("--execute-volatile-upload", action="store_true")
    args = parser.parse_args()
    if not args.execute_volatile_upload:
        raise ProbeSafetyError("refusing without --execute-volatile-upload")
    ports = neato_ports()
    neato_port = args.neato_port or (ports[0] if len(ports) == 1 else None)
    if not neato_port or neato_port not in ports:
        raise ProbeSafetyError(f"expected one matching Neato USB port; found {ports}")
    if args.result.exists() or args.private_dir.exists():
        raise ProbeSafetyError("refusing to overwrite an existing result")

    result, p6_data, private_blobs = run(neato_port, args.p6_port)
    args.private_dir.mkdir(parents=True, exist_ok=False)
    (args.private_dir / "p6.raw").write_bytes(p6_data)
    for name, data in private_blobs:
        (args.private_dir / name).write_bytes(data)
    args.result.parent.mkdir(parents=True, exist_ok=True)
    args.result.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
