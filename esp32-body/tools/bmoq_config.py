"""Deterministic encoder for the bounded BMOQ-v2 binary config tensor."""

from __future__ import annotations

import struct
from collections.abc import Iterable, Mapping

CONFIG_MAGIC = b"BCFG"
CONFIG_VERSION = 1
CONFIG_HEADER_BYTES = 16
CONFIG_ENTRY_BYTES = 12
CONFIG_MAX_BYTES = 1024 * 1024
CONFIG_MAX_ENTRIES = 256

TYPE_U32 = 1
TYPE_I32 = 2
TYPE_F32 = 3
TYPE_BOOL = 4
TYPE_U32_ARRAY = 5


def _values(value: object) -> tuple[object, ...]:
    if isinstance(value, (str, bytes, bytearray)):
        raise TypeError("config values must be numeric")
    if isinstance(value, Iterable):
        return tuple(value)
    return (value,)


def _payload(value_type: int, value: object) -> tuple[int, bytes]:
    values = _values(value)
    if not values or len(values) > 0xFFFF:
        raise ValueError("config value count must be 1..65535")
    if value_type in (TYPE_U32, TYPE_U32_ARRAY):
        payload = b"".join(struct.pack("<I", int(item)) for item in values)
    elif value_type == TYPE_I32:
        payload = b"".join(struct.pack("<i", int(item)) for item in values)
    elif value_type == TYPE_F32:
        payload = b"".join(struct.pack("<f", float(item)) for item in values)
    elif value_type == TYPE_BOOL:
        payload = b"".join(struct.pack("<I", bool(item)) for item in values)
    else:
        raise ValueError(f"unsupported config type {value_type}")
    if value_type != TYPE_U32_ARRAY and len(values) != 1:
        raise ValueError("only TYPE_U32_ARRAY accepts multiple values")
    return len(values), payload


def encode_config(entries: Mapping[int, tuple[int, object]]) -> bytes:
    """Encode sorted key -> (type, value) entries for tensor 0x32474643."""
    if len(entries) > CONFIG_MAX_ENTRIES:
        raise ValueError("too many config entries")
    body = bytearray()
    for key in sorted(entries):
        if not 0 <= key <= 0xFFFFFFFF:
            raise ValueError(f"config key out of range: {key}")
        value_type, value = entries[key]
        count, payload = _payload(value_type, value)
        body.extend(struct.pack("<IHHI", key, value_type, count, len(payload)))
        body.extend(payload)
        body.extend(b"\0" * (-len(payload) % 4))
    total = CONFIG_HEADER_BYTES + len(body)
    if total > CONFIG_MAX_BYTES:
        raise ValueError("config tensor exceeds bounded runtime limit")
    return struct.pack(
        "<4sHHII", CONFIG_MAGIC, CONFIG_VERSION, CONFIG_HEADER_BYTES,
        len(entries), total,
    ) + body
