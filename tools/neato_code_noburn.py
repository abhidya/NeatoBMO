#!/usr/bin/env python3
"""Run an allowlisted Cruz Rev113 application transfer with NoWrite enforced.

Only the verified Cruz-P 2.5, 2.7, and 3.1 encrypted images are accepted.
Version 3.2+ and every unknown image are refused before the serial port opens.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from neatobmo.transport import SerialTransport


COMMAND = "Upload code noburn"
TARGET_SERIAL = "WTD41611DD,0037829,P"
TARGET_SOFTWARE = "Software,2,4,15667"

SAFE_IMAGES = {
    "e1a31ef56e2b617a4056d70c308aab26b7e2cd95e679cb982d697ff4f089c697": {
        "release": "2.5",
        "build": 15893,
        "suffix": "P",
        "size": 805_888,
    },
    "2e6033b1ef5440bed949de20e89563d7cb3dda41e0eb5e371c9d86dceeb1633f": {
        "release": "2.7",
        "build": 16621,
        "suffix": "P",
        "size": 805_888,
    },
    "03396329a1a47a7358d09bd414d01eddaa5806a50a18f4d9ce2f96edc2d5fab7": {
        "release": "3.1",
        "build": 17844,
        "suffix": "P",
        "size": 847_872,
    },
}

BLOCKED_IMAGES = {
    "373775d3d59f1569e9886c4aef4449ee847e74455c806e75ef916693841e27c3":
        "FORBIDDEN Cruz-P 3.2 build 18755: incompatible CPU target; brick risk",
}


def classify_digest(size: int, digest: str) -> dict[str, object]:
    if digest in BLOCKED_IMAGES:
        raise ValueError(BLOCKED_IMAGES[digest])
    image = SAFE_IMAGES.get(digest)
    if image is None:
        raise ValueError(f"refusing unknown application image: bytes={size} sha256={digest}")
    if size != image["size"]:
        raise ValueError(
            f"refusing size mismatch for build {image['build']}: "
            f"expected={image['size']} actual={size}"
        )
    return image


def require_target(version: str) -> None:
    if TARGET_SERIAL not in version or TARGET_SOFTWARE not in version:
        raise RuntimeError("refusing: connected robot identity/version did not match")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("image", type=Path)
    parser.add_argument("--port", required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if not args.execute:
        parser.error("refusing to contact robot without --execute")

    payload = args.image.read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    try:
        image = classify_digest(len(payload), digest)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    assert COMMAND == "Upload code noburn"
    transport = SerialTransport(args.port)
    try:
        before = transport.send("GetVersion", timeout=5.0)
        require_target(before)
        reply = transport.send_binary(
            COMMAND, payload, timeout=60.0, allow_terminator=True
        )
        after = transport.send("GetVersion", timeout=5.0)
        require_target(after)
    finally:
        transport.close()

    print(json.dumps({
        "command": COMMAND,
        "image": image,
        "payload_bytes": len(payload),
        "payload_sha256": digest,
        "transfer_reply_hex": reply.hex(),
        "post_transfer_software_unchanged": TARGET_SOFTWARE in after,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
