#!/usr/bin/env python3
"""Send one manifest-verified NeatoOS image through `Upload code noburn`.

This is a transport probe only.  It never selects burn/reboot, never retries,
and never describes a completed receive as firmware validity or bootability.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Callable

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from neatobmo.transport import BinaryTransferError, SerialTransport


COMMAND = "Upload code noburn"
TARGET_SERIAL = "WTD41611DD,0037829,P"
TARGET_SOFTWARE = "Software,2,5,15893"
REPRESENTATIONS = (
    "raw-arm",
    "raw-structural",
    "reference-header",
    "reference-header-full-length",
)


class ProbeSafetyError(RuntimeError):
    pass


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def expected_record(manifest: dict, representation: str) -> dict:
    if manifest.get("schema") != "neatoos-probe-set/v1":
        raise ProbeSafetyError("expected neatoos-probe-set/v1 manifest")
    if representation == "raw-arm":
        raw = manifest.get("raw_input")
        if not isinstance(raw, dict):
            raise ProbeSafetyError("manifest has no raw_input record")
        return {"label": representation, **raw}
    outputs = manifest.get("outputs")
    if not isinstance(outputs, list):
        raise ProbeSafetyError("manifest has no outputs list")
    matches = [record for record in outputs if record.get("label") == representation]
    if len(matches) != 1:
        raise ProbeSafetyError(f"expected one {representation!r} output record")
    record = matches[0]
    if record.get("encryption_status") != "NOT ENCRYPTED":
        raise ProbeSafetyError("probe must be labeled NOT ENCRYPTED")
    if record.get("authentication_status") != "NOT AUTHENTICATED":
        raise ProbeSafetyError("probe must be labeled NOT AUTHENTICATED")
    return record


def verify_image(image: Path, manifest_path: Path, representation: str) -> tuple[bytes, dict]:
    if representation not in REPRESENTATIONS:
        raise ProbeSafetyError(f"unsupported representation: {representation}")
    payload = image.read_bytes()
    manifest = json.loads(manifest_path.read_text())
    record = expected_record(manifest, representation)
    expected_size = record.get("size")
    expected_digest = record.get("sha256")
    if expected_size != len(payload) or expected_digest != sha256(payload):
        raise ProbeSafetyError(
            f"image does not match manifest: size={len(payload)} sha256={sha256(payload)}"
        )
    return payload, record


def require_target(version: str) -> None:
    if TARGET_SERIAL not in version or TARGET_SOFTWARE not in version:
        raise ProbeSafetyError("connected robot is not the approved XV-12 running 2.5.15893")


def run_probe(
    *,
    port: str,
    image: Path,
    manifest: Path,
    representation: str,
    transport_factory: Callable[[str], SerialTransport] = SerialTransport,
) -> dict:
    payload, record = verify_image(image, manifest, representation)
    transport = transport_factory(port)
    result = {
        "command": COMMAND,
        "representation": representation,
        "image": str(image),
        "image_bytes": len(payload),
        "image_sha256": sha256(payload),
        "manifest": str(manifest),
        "manifest_record": record,
        "writes_requested": False,
        "reboot_requested": False,
        "automatic_retry": False,
    }
    try:
        before = transport.send("GetVersion", timeout=5.0)
        require_target(before)
        result["before_identity_ok"] = True
        try:
            reply = transport.send_binary(
                COMMAND, payload, timeout=30.0, allow_terminator=True
            )
        except BinaryTransferError as error:
            result["usb_result"] = "receiver_rejected_or_transfer_incomplete"
            result["usb_error"] = str(error)
            result["classification"] = "requires P6 correlation"
            return result
        result["usb_reply_hex"] = reply.hex()
        result["usb_result"] = "receiver transaction completed"
        result["classification"] = "receiver accepted bytes; image validity and bootability unproven"
        after = transport.send("GetVersion", timeout=5.0)
        require_target(after)
        result["after_identity_ok"] = True
        return result
    finally:
        transport.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--representation", required=True, choices=REPRESENTATIONS)
    parser.add_argument("--port", required=True)
    parser.add_argument("--result", required=True, type=Path)
    args = parser.parse_args()
    if args.result.exists():
        parser.error(f"refusing to overwrite result: {args.result}")
    try:
        result = run_probe(
            port=args.port,
            image=args.image,
            manifest=args.manifest,
            representation=args.representation,
        )
    except (OSError, json.JSONDecodeError, ProbeSafetyError) as error:
        parser.error(str(error))
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    args.result.parent.mkdir(parents=True, exist_ok=True)
    args.result.write_text(rendered)
    print(rendered, end="")
    return 0 if result.get("after_identity_ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
