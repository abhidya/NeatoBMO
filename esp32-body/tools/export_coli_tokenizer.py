#!/usr/bin/env python3
"""Convert a GPT-NeoX/OLMo byte-level BPE tokenizer.json to CTOK.

The ESP32 firmware reads CTOK directly from a coli_store_t. It does not parse
Hugging Face tokenizer JSON and does not need the full vocabulary or merge table
in RAM.
"""

from __future__ import annotations

import argparse
import json
import struct
from pathlib import Path

HEADER_BYTES = 128
ENTRY_BYTES = 24
MERGE_BYTES = 16
VERSION = 1
PAD_ID = 1
EOS_ID = 50279
MAX_TOKEN_BYTES = 256

TOKEN_FLAG_SPECIAL = 0x0001
TOKEN_FLAG_BYTE = 0x0002


def bytes_to_unicode() -> dict[int, str]:
    bs = list(range(ord("!"), ord("~") + 1))
    bs += list(range(ord("¡"), ord("¬") + 1))
    bs += list(range(ord("®"), ord("ÿ") + 1))
    cs = bs[:]
    n = 0
    for byte in range(256):
        if byte not in bs:
            bs.append(byte)
            cs.append(256 + n)
            n += 1
    return dict(zip(bs, (chr(value) for value in cs)))


BYTE_DECODER = {value: key for key, value in bytes_to_unicode().items()}


def decode_vocab_token(token: str) -> bytes:
    output = bytearray()
    for char in token:
        byte_value = BYTE_DECODER.get(char)
        if byte_value is None:
            output.extend(char.encode("utf-8"))
        else:
            output.append(byte_value)
    return bytes(output)


def model_payload(tokenizer_json: dict) -> dict:
    model = tokenizer_json.get("model")
    if not isinstance(model, dict):
        raise ValueError("tokenizer.json missing model object")
    if model.get("type") not in (None, "BPE"):
        raise ValueError(f"unsupported tokenizer model type: {model.get('type')}")
    vocab = model.get("vocab")
    merges = model.get("merges")
    if not isinstance(vocab, dict) or not isinstance(merges, list):
        raise ValueError("tokenizer.json model must contain vocab and merges")
    return model


def special_ids(tokenizer_json: dict, vocab: dict[str, int]) -> set[int]:
    ids = {PAD_ID, EOS_ID}
    for item in tokenizer_json.get("added_tokens", []):
        if isinstance(item, dict):
            token_id = item.get("id")
            content = item.get("content")
            if isinstance(token_id, int):
                ids.add(token_id)
            elif isinstance(content, str) and content in vocab:
                ids.add(vocab[content])
    return ids


def parse_merge(merge: object) -> tuple[str, str]:
    if isinstance(merge, str):
        parts = merge.split()
    elif isinstance(merge, list):
        parts = merge
    else:
        raise ValueError(f"unsupported merge entry: {merge!r}")
    if len(parts) != 2 or not all(isinstance(part, str) for part in parts):
        raise ValueError(f"invalid merge entry: {merge!r}")
    return parts[0], parts[1]


def build_ctok(tokenizer_json: dict) -> bytes:
    model = model_payload(tokenizer_json)
    vocab: dict[str, int] = {str(token): int(token_id) for token, token_id in model["vocab"].items()}
    vocab_size = max(vocab.values()) + 1
    if len(set(vocab.values())) != len(vocab):
        raise ValueError("vocab contains duplicate token ids")
    if EOS_ID >= vocab_size or PAD_ID >= vocab_size:
        raise ValueError("vocab does not contain expected OLMoE pad/eos ids")

    tokens_by_id: list[bytes] = [b""] * vocab_size
    for token, token_id in vocab.items():
        token_bytes = decode_vocab_token(token)
        if len(token_bytes) > MAX_TOKEN_BYTES:
            raise ValueError(f"token {token_id} exceeds {MAX_TOKEN_BYTES} bytes")
        tokens_by_id[token_id] = token_bytes

    specials = special_ids(tokenizer_json, vocab)
    byte_token_for_value: dict[int, int] = {}
    for token_id, token_bytes in enumerate(tokens_by_id):
        if token_id in specials:
            continue
        if len(token_bytes) == 1 and token_bytes[0] not in byte_token_for_value:
            byte_token_for_value[token_bytes[0]] = token_id
    missing = sorted(set(range(256)) - set(byte_token_for_value))
    if missing:
        raise ValueError(f"missing byte fallback tokens: {missing[:8]}")

    merge_records: list[tuple[int, int, int, int]] = []
    for rank, merge in enumerate(model["merges"]):
        left, right = parse_merge(merge)
        result = left + right
        if left not in vocab or right not in vocab or result not in vocab:
            raise ValueError(f"merge references token absent from vocab: {merge!r}")
        merge_records.append((vocab[left], vocab[right], vocab[result], rank))
    merge_records.sort(key=lambda item: (item[0], item[1]))

    vocab_offset = HEADER_BYTES
    token_data_offset = vocab_offset + vocab_size * ENTRY_BYTES
    token_data = bytearray()
    entries = bytearray()
    for token_id, token_bytes in enumerate(tokens_by_id):
        flags = 0
        byte_value = 0
        if token_id in specials:
            flags |= TOKEN_FLAG_SPECIAL
        if len(token_bytes) == 1 and byte_token_for_value.get(token_bytes[0]) == token_id:
            flags |= TOKEN_FLAG_BYTE
            byte_value = token_bytes[0]
        data_offset = token_data_offset + len(token_data)
        token_data.extend(token_bytes)
        entries.extend(
            struct.pack("<QHHIII", data_offset, len(token_bytes), flags, byte_value, 0, 0)
        )

    merge_offset = token_data_offset + len(token_data)
    header = bytearray(HEADER_BYTES)
    header[0:4] = b"CTOK"
    struct.pack_into("<HHIIIIQQQIIIH", header, 4, VERSION, HEADER_BYTES,
                     vocab_size, len(merge_records), ENTRY_BYTES, MERGE_BYTES,
                     vocab_offset, token_data_offset, merge_offset, 256, PAD_ID,
                     EOS_ID, MAX_TOKEN_BYTES)

    output = bytearray(header)
    output.extend(entries)
    output.extend(token_data)
    for left, right, result, rank in merge_records:
        output.extend(struct.pack("<IIII", left, right, result, rank))
    return bytes(output)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("tokenizer_json", type=Path)
    parser.add_argument("output_ctok", type=Path)
    args = parser.parse_args()

    tokenizer_json = json.loads(args.tokenizer_json.read_text(encoding="utf-8"))
    ctok = build_ctok(tokenizer_json)
    args.output_ctok.write_bytes(ctok)
    print(f"wrote {args.output_ctok} ({len(ctok)} bytes)")


if __name__ == "__main__":
    main()
