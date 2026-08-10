#!/usr/bin/env python3
"""Inspect and make narrowly scoped version patches to raw Neato firmware.

This tool only reads and writes ordinary files. It has no serial, USB, debug,
or flash-programming support.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import struct
import tempfile
from typing import Iterable

from neato_firmware import (
    KNOWN_PLAINTEXT_MARKERS,
    arm32_screening_signals,
    entropy,
    printable_strings,
    sha256,
)


MANIFEST_SCHEMA = "neato-cfw-version-patch/v1"
TOOL_VERSION = 1


class CfwPatchError(ValueError):
    """Raised when an offline CFW operation fails a safety invariant."""


def find_offsets(data: bytes, needle: bytes) -> list[int]:
    if not needle:
        raise CfwPatchError("search value must not be empty")
    offsets = []
    start = 0
    while True:
        offset = data.find(needle, start)
        if offset < 0:
            return offsets
        offsets.append(offset)
        start = offset + 1


def inspect_raw(data: bytes, find_values: Iterable[bytes] = ()) -> dict[str, object]:
    markers = {
        marker.decode("ascii"): find_offsets(data, marker)
        for marker in KNOWN_PLAINTEXT_MARKERS
        if marker in data
    }
    searches = {
        value.decode("ascii", errors="backslashreplace"): find_offsets(data, value)
        for value in find_values
    }
    strings = printable_strings(data)
    return {
        "file_size": len(data),
        "sha256": sha256(data),
        "entropy": round(entropy(data), 6),
        "known_marker_offsets": markers,
        "printable_string_count": len(strings),
        "sample_strings": strings[:30],
        "arm32_screening_signals": arm32_screening_signals(data),
        "search_offsets": searches,
    }


def encode_value(value: str, encoding: str) -> bytes:
    if encoding == "ascii":
        try:
            return value.encode("ascii")
        except UnicodeEncodeError as error:
            raise CfwPatchError("ASCII patch values must contain only ASCII") from error

    try:
        number = int(value, 0)
    except ValueError as error:
        raise CfwPatchError(f"{encoding} patch values must be integers") from error

    if encoding == "u16le":
        if not 0 <= number <= 0xFFFF:
            raise CfwPatchError("u16le value is outside 0..65535")
        return struct.pack("<H", number)
    if encoding == "u32le":
        if not 0 <= number <= 0xFFFFFFFF:
            raise CfwPatchError("u32le value is outside 0..4294967295")
        return struct.pack("<I", number)
    raise CfwPatchError(f"unsupported encoding: {encoding}")


def normalize_sha256(value: str) -> str:
    normalized = value.lower()
    if not re.fullmatch(r"[0-9a-f]{64}", normalized):
        raise CfwPatchError("base SHA-256 must be exactly 64 hexadecimal characters")
    return normalized


def patch_version(
    base: bytes,
    *,
    expected_base_sha256: str,
    old_value: str,
    new_value: str,
    encoding: str = "ascii",
    offset: int | None = None,
) -> tuple[bytes, dict[str, object]]:
    expected_digest = normalize_sha256(expected_base_sha256)
    actual_digest = sha256(base)
    if actual_digest != expected_digest:
        raise CfwPatchError(
            f"base SHA-256 mismatch: expected {expected_digest}, got {actual_digest}"
        )

    old_bytes = encode_value(old_value, encoding)
    new_bytes = encode_value(new_value, encoding)
    if not old_bytes:
        raise CfwPatchError("patch value must not be empty")
    if len(old_bytes) != len(new_bytes):
        raise CfwPatchError("old and new encoded values must have identical length")
    if old_bytes == new_bytes:
        raise CfwPatchError("old and new encoded values must differ")

    matches = find_offsets(base, old_bytes)
    if offset is None:
        if len(matches) != 1:
            raise CfwPatchError(
                f"expected exactly one old-value match, found {len(matches)}; "
                "supply a reviewed --offset only when multiple matches are understood"
            )
        selected_offset = matches[0]
    else:
        if offset < 0 or offset + len(old_bytes) > len(base):
            raise CfwPatchError("patch offset is outside the image")
        if base[offset : offset + len(old_bytes)] != old_bytes:
            raise CfwPatchError("expected old bytes are not present at the patch offset")
        selected_offset = offset

    patched = (
        base[:selected_offset]
        + new_bytes
        + base[selected_offset + len(old_bytes) :]
    )
    if len(patched) != len(base):
        raise AssertionError("same-size patch invariant failed")

    manifest: dict[str, object] = {
        "schema": MANIFEST_SCHEMA,
        "tool_version": TOOL_VERSION,
        "operation": "version-only",
        "base": {"size": len(base), "sha256": actual_digest},
        "output": {"size": len(patched), "sha256": sha256(patched)},
        "patch": {
            "offset": selected_offset,
            "size": len(old_bytes),
            "encoding": encoding,
            "old_value": old_value,
            "new_value": new_value,
            "old_hex": old_bytes.hex(),
            "new_hex": new_bytes.hex(),
            "all_old_value_matches": matches,
        },
        "integrity_updates": [],
        "limitations": [
            "No boot checksum or integrity field is modified.",
            "This manifest proves file-level scope, not device bootability.",
        ],
    }
    return patched, manifest


def verify_patch(
    base: bytes, patched: bytes, manifest: dict[str, object]
) -> dict[str, object]:
    checks: dict[str, bool] = {}
    checks["schema"] = manifest.get("schema") == MANIFEST_SCHEMA
    checks["tool_version"] = manifest.get("tool_version") == TOOL_VERSION
    checks["operation"] = manifest.get("operation") == "version-only"

    try:
        base_record = manifest["base"]
        output_record = manifest["output"]
        patch_record = manifest["patch"]
        assert isinstance(base_record, dict)
        assert isinstance(output_record, dict)
        assert isinstance(patch_record, dict)
        offset = patch_record["offset"]
        size = patch_record["size"]
        if type(offset) is not int or type(size) is not int:
            raise TypeError("patch offset and size must be JSON integers")
        old_bytes = bytes.fromhex(str(patch_record["old_hex"]))
        new_bytes = bytes.fromhex(str(patch_record["new_hex"]))
        encoding = patch_record["encoding"]
        old_value = patch_record["old_value"]
        new_value = patch_record["new_value"]
        if not all(isinstance(value, str) for value in (encoding, old_value, new_value)):
            raise TypeError("patch encoding and values must be strings")
        encoded_old = encode_value(old_value, encoding)
        encoded_new = encode_value(new_value, encoding)
        recorded_matches = patch_record["all_old_value_matches"]
        if not isinstance(recorded_matches, list) or any(
            type(value) is not int for value in recorded_matches
        ):
            raise TypeError("old-value matches must be a list of JSON integers")
    except (AssertionError, KeyError, TypeError, ValueError) as error:
        raise CfwPatchError(f"invalid patch manifest: {error}") from error

    checks["base_size"] = base_record.get("size") == len(base)
    checks["base_sha256"] = base_record.get("sha256") == sha256(base)
    checks["output_size"] = output_record.get("size") == len(patched)
    checks["output_sha256"] = output_record.get("sha256") == sha256(patched)
    checks["same_file_size"] = len(base) == len(patched)
    checks["nonempty_patch"] = size > 0 and bool(old_bytes) and bool(new_bytes)
    checks["values_differ"] = old_bytes != new_bytes
    checks["encoded_size"] = size == len(old_bytes) == len(new_bytes)
    checks["encoded_values"] = old_bytes == encoded_old and new_bytes == encoded_new
    checks["old_value_matches"] = recorded_matches == find_offsets(base, old_bytes)
    checks["selected_known_match"] = offset in recorded_matches
    checks["hashes_differ"] = base_record.get("sha256") != output_record.get("sha256")
    checks["integrity_updates"] = manifest.get("integrity_updates") == []
    checks["patch_bounds"] = 0 <= offset <= len(base) - size

    if (
        checks["patch_bounds"]
        and checks["encoded_size"]
        and checks["nonempty_patch"]
    ):
        checks["old_bytes_at_offset"] = base[offset : offset + size] == old_bytes
        checks["new_bytes_at_offset"] = patched[offset : offset + size] == new_bytes
        expected = base[:offset] + new_bytes + base[offset + size :]
        checks["only_approved_replacement"] = patched == expected
    else:
        checks["old_bytes_at_offset"] = False
        checks["new_bytes_at_offset"] = False
        checks["only_approved_replacement"] = False

    return {"passed": all(checks.values()), "checks": checks}


def stage_file(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if temporary.read_bytes() != data:
            raise CfwPatchError(f"staged file verification failed: {path}")
        return temporary
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def publish_pair(
    first_path: Path,
    first_data: bytes,
    second_path: Path,
    second_data: bytes,
) -> None:
    if first_path == second_path:
        raise CfwPatchError("output and manifest paths must differ")
    for path in (first_path, second_path):
        if path.exists():
            raise CfwPatchError(f"refusing to overwrite existing file: {path}")

    staged_first = stage_file(first_path, first_data)
    staged_second: Path | None = None
    published_first = False
    published_second = False
    try:
        staged_second = stage_file(second_path, second_data)
        os.link(staged_first, first_path)
        published_first = True
        os.link(staged_second, second_path)
        published_second = True
    except FileExistsError as error:
        raise CfwPatchError(f"refusing to overwrite existing file: {error.filename}") from error
    finally:
        if not published_second and published_first:
            first_path.unlink(missing_ok=True)
        if not published_first and published_second:
            second_path.unlink(missing_ok=True)
        staged_first.unlink(missing_ok=True)
        if staged_second is not None:
            staged_second.unlink(missing_ok=True)


def write_new(path: Path, data: bytes) -> None:
    if path.exists():
        raise CfwPatchError(f"refusing to overwrite existing file: {path}")
    staged = stage_file(path, data)
    try:
        os.link(staged, path)
    except FileExistsError as error:
        raise CfwPatchError(f"refusing to overwrite existing file: {path}") from error
    finally:
        staged.unlink(missing_ok=True)


def emit(value: object, output: Path | None = None) -> None:
    rendered = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()
    if output is None:
        print(rendered.decode(), end="")
        return
    write_new(output, rendered)


def parse_offset(value: str) -> int:
    try:
        result = int(value, 0)
    except ValueError as error:
        raise argparse.ArgumentTypeError("offset must be an integer") from error
    if result < 0:
        raise argparse.ArgumentTypeError("offset must not be negative")
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="action", required=True)

    inspect_parser = subparsers.add_parser("inspect-raw")
    inspect_parser.add_argument("image", type=Path)
    inspect_parser.add_argument("--find", action="append", default=[])
    inspect_parser.add_argument("--output", type=Path)

    patch_parser = subparsers.add_parser("patch-version")
    patch_parser.add_argument("base", type=Path)
    patch_parser.add_argument("output", type=Path)
    patch_parser.add_argument("--base-sha256", required=True)
    patch_parser.add_argument("--old", required=True)
    patch_parser.add_argument("--new", required=True)
    patch_parser.add_argument(
        "--encoding", choices=("ascii", "u16le", "u32le"), default="ascii"
    )
    patch_parser.add_argument("--offset", type=parse_offset)
    patch_parser.add_argument("--manifest", type=Path)

    verify_parser = subparsers.add_parser("verify-patch")
    verify_parser.add_argument("base", type=Path)
    verify_parser.add_argument("patched", type=Path)
    verify_parser.add_argument("manifest", type=Path)
    verify_parser.add_argument("--output", type=Path)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.action == "inspect-raw":
            find_values = [value.encode("ascii") for value in args.find]
            emit(inspect_raw(args.image.read_bytes(), find_values), args.output)
            return 0

        if args.action == "patch-version":
            patched, manifest = patch_version(
                args.base.read_bytes(),
                expected_base_sha256=args.base_sha256,
                old_value=args.old,
                new_value=args.new,
                encoding=args.encoding,
                offset=args.offset,
            )
            manifest_path = args.manifest or args.output.with_suffix(
                args.output.suffix + ".manifest.json"
            )
            manifest_bytes = (
                json.dumps(manifest, indent=2, sort_keys=True) + "\n"
            ).encode()
            publish_pair(args.output, patched, manifest_path, manifest_bytes)
            emit(manifest)
            return 0

        manifest = json.loads(args.manifest.read_text())
        result = verify_patch(
            args.base.read_bytes(), args.patched.read_bytes(), manifest
        )
        emit(result, args.output)
        return 0 if result["passed"] else 2
    except (CfwPatchError, OSError, UnicodeEncodeError, json.JSONDecodeError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    raise SystemExit(main())
