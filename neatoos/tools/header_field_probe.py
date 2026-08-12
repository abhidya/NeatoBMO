#!/usr/bin/env python3
"""One-bit Cruz-P header-field probe with no automatic retry.

The payload is derived only from the exact archived Cruz-P 2.5 image.  It
changes bit 0 at offset 0x18 inside the unresolved clear 0x10..0x1f header
field and preserves every encrypted body byte.  The CLI supports either one
`Upload code noburn` transport check or one manifest-like, confirmation-locked
`Upload code reboot` application write.
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

from neatobmo.transport import SerialTransport
from neatoos.tools import application_probe


BASE_BYTES = 805_888
BASE_SHA256 = "e1a31ef56e2b617a4056d70c308aab26b7e2cd95e679cb982d697ff4f089c697"
TARGET_SERIAL = "WTD41611DD,0037829,P"
TARGET_SOFTWARE = "Software,2,5,15893"
MUTATION_OFFSET = 0x18
MUTATION_XOR = 0x01
REPRESENTATION = "stock-header-field-0x18-bit0-flip"
NOBURN_COMMAND = "Upload code noburn"


class HeaderFieldProbeError(RuntimeError):
    pass


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def classify_base(data: bytes) -> None:
    digest = sha256(data)
    if len(data) != BASE_BYTES or digest != BASE_SHA256:
        raise HeaderFieldProbeError(
            f"refusing base: expected bytes={BASE_BYTES} sha256={BASE_SHA256}; "
            f"got bytes={len(data)} sha256={digest}"
        )


def mutate_header_field(data: bytes) -> bytes:
    if len(data) <= MUTATION_OFFSET:
        raise HeaderFieldProbeError("base is shorter than the fixed mutation offset")
    mutated = bytearray(data)
    mutated[MUTATION_OFFSET] ^= MUTATION_XOR
    return bytes(mutated)


def build_probe(base: bytes) -> bytes:
    classify_base(base)
    return mutate_header_field(base)


def require_installed_target(version: str) -> None:
    if TARGET_SERIAL not in version or TARGET_SOFTWARE not in version:
        raise HeaderFieldProbeError(
            "connected robot is not the approved XV-12 running stock 2.5.15893"
        )


def confirmation_for(payload: bytes) -> str:
    return f"FLASH NEATO HEADER FIELD PROBE {sha256(payload)}"


def metadata(payload: bytes) -> dict[str, object]:
    return {
        "representation": REPRESENTATION,
        "base_bytes": BASE_BYTES,
        "base_sha256": BASE_SHA256,
        "image_bytes": len(payload),
        "image_sha256": sha256(payload),
        "mutation": {
            "offset": MUTATION_OFFSET,
            "xor_mask": f"0x{MUTATION_XOR:02x}",
            "changed_bits": 1,
            "encrypted_body_preserved": True,
        },
        "automatic_retry": False,
    }


def run_noburn(
    *,
    port: str,
    payload: bytes,
    transport_factory: Callable[[str], SerialTransport] = SerialTransport,
) -> dict[str, object]:
    result = {
        **metadata(payload),
        "command": NOBURN_COMMAND,
        "writes_requested": False,
        "reboot_requested": False,
    }
    transport = transport_factory(port)
    try:
        before = transport.send("GetVersion", timeout=5.0)
        require_installed_target(before)
        result["before_identity_ok"] = True
        reply = transport.send_binary(
            NOBURN_COMMAND, payload, timeout=30.0, allow_terminator=True
        )
        result["usb_reply_hex"] = reply.hex()
        after = transport.send("GetVersion", timeout=5.0)
        require_installed_target(after)
        result["after_identity_ok"] = True
        result["classification"] = "transport complete; boot integrity untested"
        return result
    finally:
        transport.close()


def render_result(path: Path, result: dict[str, object]) -> None:
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x") as output:
        output.write(rendered)
    print(rendered, end="")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stock_image", type=Path)
    parser.add_argument("--port", required=True)
    parser.add_argument("--result", required=True, type=Path)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--noburn", action="store_true")
    mode.add_argument("--execute-application-write", action="store_true")
    parser.add_argument("--confirmation", default="")
    args = parser.parse_args()
    if args.result.exists():
        parser.error(f"refusing to overwrite result: {args.result}")

    base = args.stock_image.read_bytes()
    payload = build_probe(base)
    try:
        if args.noburn:
            result = run_noburn(port=args.port, payload=payload)
        else:
            required = confirmation_for(payload)
            if args.confirmation != required:
                raise HeaderFieldProbeError(
                    f"confirmation must exactly equal: {required}"
                )
            result = application_probe.execute(
                port=args.port,
                payload=payload,
                representation=REPRESENTATION,
            )
            result.update(metadata(payload))
    except Exception as error:
        result = {**metadata(payload), "error": str(error)}
    render_result(args.result, result)
    return 0 if not result.get("error") else 2


if __name__ == "__main__":
    raise SystemExit(main())
