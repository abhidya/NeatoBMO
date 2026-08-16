#!/usr/bin/env python3
"""Interpret a JTAG halt dump: SDRAM plaintext app + SRAM AES round keys.

Two oracles, both read-only:

1. SDRAM (0x20000000): a decrypted ARM926 application must begin with an
   exception-vector table (condition nibble 0xE) and contain known command
   strings.  Wrong/garbage SDRAM stays high-entropy.

2. SRAM (0x00200000 / 0x00300000): the AT91SAM9XE has no AES engine, so
   decryption is software AES.  During the boot-decrypt window the on-chip
   SRAM holds the expanded round keys -- a 176-byte AES-128 key schedule.
   A random 176-byte region matches a valid schedule with probability ~2^-1272,
   so a hit is definitive: that is the key.

Usage:
  verify_firmware_dump.py --sdram sdram.bin --sram0 sram0.bin --sram1 sram1.bin \
      [--enc XV11App.15893.P.bin.enc] [--output report.json]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import struct
from collections import Counter
from pathlib import Path

try:
    from Crypto.Cipher import AES
except Exception as exc:  # pragma: no cover
    raise SystemExit("pycryptodome required: pip install pycryptodome") from exc

KNOWN_MARKERS = (
    b"Neato Robotics", b"PlaySound", b"GetVersion", b"SetMotor",
    b"SetLCD", b"SetLDSRotation", b"Copyright", b"NEROS",
)


# ---- AES-128 key schedule (programmatic S-box, no table to hardcode) ----
def _gf_mul(a: int, b: int) -> int:
    p = 0
    for _ in range(8):
        if b & 1:
            p ^= a
        hi = a & 0x80
        a = (a << 1) & 0xFF
        if hi:
            a ^= 0x1B
        b >>= 1
    return p


def _gf_inv(a: int) -> int:
    if a == 0:
        return 0
    for b in range(1, 256):
        if _gf_mul(a, b) == 1:
            return b
    return 0


def _rotl8(x: int, n: int) -> int:
    return ((x << n) | (x >> (8 - n))) & 0xFF


def _sbox() -> list[int]:
    return [_gf_inv(a) ^ _rotl8(_gf_inv(a), 1) ^ _rotl8(_gf_inv(a), 2)
            ^ _rotl8(_gf_inv(a), 3) ^ _rotl8(_gf_inv(a), 4) ^ 0x63
            for a in range(256)]


_SBOX = _sbox()
_RCON = (0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1B, 0x36)


def expand_aes128(key16: bytes) -> bytes:
    sb = _SBOX
    w = [int.from_bytes(key16[i:i + 4], "big") for i in range(0, 16, 4)]
    for i in range(4, 44):
        t = w[i - 1]
        if i % 4 == 0:
            t = ((t << 8) & 0xFFFFFFFF) | (t >> 24)          # RotWord
            t = ((sb[(t >> 24) & 0xFF] << 24) | (sb[(t >> 16) & 0xFF] << 16)
                 | (sb[(t >> 8) & 0xFF] << 8) | sb[t & 0xFF])  # SubWord
            t ^= (_RCON[i // 4 - 1] << 24)                    # Rcon
        w.append(w[i - 4] ^ t)
    return b"".join(x.to_bytes(4, "big") for x in w)


def find_key_schedule(data: bytes) -> list[dict]:
    """Return every 176-byte window in data that is a valid AES-128 schedule."""
    hits = []
    for off in range(0, len(data) - 176 + 1, 4):
        if data[off:off + 176] == expand_aes128(data[off:off + 16]):
            hits.append({"offset": off, "key_hex": data[off:off + 16].hex()})
    return hits


def public_hits(hits: list[dict]) -> list[dict]:
    """Describe key-schedule evidence without publishing recovered keys."""
    return [
        {
            "offset": hit["offset"],
            "key_sha256": hashlib.sha256(
                bytes.fromhex(hit["key_hex"])
            ).hexdigest(),
        }
        for hit in hits
    ]


# ---- SDRAM plaintext oracle ----
def entropy(b: bytes) -> float:
    if not b:
        return 0.0
    c = Counter(b)
    n = len(b)
    return -sum((v / n) * math.log2(v / n) for v in c.values())


def arm_vector_score(data: bytes, words: int = 8) -> dict:
    hits = branch = total = 0
    for off in range(0, min(len(data), words * 4) - 3, 4):
        total += 1
        if (data[off + 3] & 0xF0) == 0xE0:
            hits += 1
        if data[off + 3] in (0xEA, 0xEB):
            branch += 1
    return {"condition_hits": hits, "total": total, "branch_opcodes": branch}


def score_sdram(data: bytes) -> dict:
    vec = arm_vector_score(data)
    markers = [m.decode("ascii") for m in KNOWN_MARKERS if m in data]
    strings = re.findall(rb"[\x20-\x7e]{6,}", data)
    return {
        "bytes": len(data),
        "entropy_first_1024": round(entropy(data[:1024]), 4),
        "arm_vector": vec,
        "markers_found": markers,
        "printable_string_count": len(strings),
        "looks_like_arm_firmware":
            vec["condition_hits"] >= 6 and vec["branch_opcodes"] >= 4,
    }


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--sdram", type=Path)
    p.add_argument("--sram0", type=Path)
    p.add_argument("--sram1", type=Path)
    p.add_argument("--enc", type=Path,
                   help="optional .enc; decrypts+validates with any found key")
    p.add_argument("--output", type=Path)
    args = p.parse_args(argv)

    report: dict = {}
    keys = []

    for label, path in (("sram0", args.sram0), ("sram1", args.sram1)):
        if not path:
            continue
        data = path.read_bytes()
        hits = find_key_schedule(data)
        report[label] = {
            "bytes": len(data), "entropy": round(entropy(data), 4),
            "aes128_key_schedule_hits": public_hits(hits),
        }
        keys += hits

    if args.sdram:
        report["sdram"] = score_sdram(args.sdram.read_bytes())

    if args.enc and keys:
        enc = args.enc.read_bytes()
        payload = enc[512:]
        for k in keys[:8]:
            for ivname, iv in (("zeros", b"\x00" * 16),
                               ("header_16_32", enc[16:32])):
                pt = AES.new(bytes.fromhex(k["key_hex"]), AES.MODE_CBC, iv).decrypt(payload)
                k[f"decrypt_{ivname}"] = {
                    "entropy": round(entropy(pt[:1024]), 4),
                    "markers": [m.decode("ascii") for m in KNOWN_MARKERS if m in pt],
                }

    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(rendered)
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
