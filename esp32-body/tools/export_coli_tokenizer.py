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
MAX_SPECIAL_TOKENS = 512

TOKEN_FLAG_SPECIAL = 0x0001
TOKEN_FLAG_BYTE = 0x0002

PRETOKENIZER_BYTE_BPE = 0
PRETOKENIZER_O200K = 1


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


def token_id_from_declared(tokenizer_json: dict, vocab: dict[str, int], key: str) -> int | None:
    value = tokenizer_json.get(key)
    if isinstance(value, str):
        return vocab.get(value)
    if isinstance(value, dict):
        content = value.get("content")
        token_id = value.get("id")
        if isinstance(token_id, int):
            return token_id
        if isinstance(content, str):
            return vocab.get(content)
    return None


def infer_pad_eos_ids(tokenizer_json: dict, vocab: dict[str, int], vocab_size: int) -> tuple[int, int]:
    pad_id = token_id_from_declared(tokenizer_json, vocab, "pad_token")
    eos_id = token_id_from_declared(tokenizer_json, vocab, "eos_token")
    for item in tokenizer_json.get("added_tokens", []):
        if not isinstance(item, dict):
            continue
        content = item.get("content")
        token_id = item.get("id")
        if not isinstance(content, str):
            continue
        if not isinstance(token_id, int):
            token_id = vocab.get(content)
        if not isinstance(token_id, int):
            continue
        lowered = content.lower()
        if pad_id is None and "pad" in lowered:
            pad_id = token_id
        if eos_id is None and (
            "eos" in lowered or "endoftext" in lowered or content in {"</s>", "<|im_end|>"}
        ):
            eos_id = token_id
    if pad_id is None and PAD_ID < vocab_size:
        pad_id = PAD_ID
    if eos_id is None and EOS_ID < vocab_size:
        eos_id = EOS_ID
    if pad_id is None:
        pad_id = eos_id if eos_id is not None else 0
    if eos_id is None:
        eos_id = pad_id
    if not (0 <= pad_id < vocab_size and 0 <= eos_id < vocab_size):
        raise ValueError("could not infer in-range pad/eos token ids")
    return pad_id, eos_id


def special_ids(tokenizer_json: dict, vocab: dict[str, int], pad_id: int, eos_id: int) -> set[int]:
    ids = {pad_id, eos_id}
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


def pretokenizer_family(tokenizer_json: dict) -> int:
    pretokenizer = tokenizer_json.get("pre_tokenizer")
    candidates = []
    if isinstance(pretokenizer, dict):
        candidates.append(pretokenizer)
        pretoks = pretokenizer.get("pretokenizers")
        if isinstance(pretoks, list):
            candidates.extend(item for item in pretoks if isinstance(item, dict))
    for item in candidates:
        pattern = item.get("pattern")
        regex = pattern.get("Regex") if isinstance(pattern, dict) else None
        if isinstance(regex, str):
            if "\\p{Han}" in regex:
                raise ValueError("Kimi o200k+Han pretokenizer is not CTOK-supported yet")
            if "\\p{Lu}" in regex:
                return PRETOKENIZER_O200K
    return PRETOKENIZER_BYTE_BPE


def build_ctok(tokenizer_json: dict) -> bytes:
    model = model_payload(tokenizer_json)
    family = pretokenizer_family(tokenizer_json)
    vocab: dict[str, int] = {str(token): int(token_id) for token, token_id in model["vocab"].items()}
    vocab_size = max(vocab.values()) + 1
    if len(set(vocab.values())) != len(vocab):
        raise ValueError("vocab contains duplicate token ids")
    pad_id, eos_id = infer_pad_eos_ids(tokenizer_json, vocab, vocab_size)

    tokens_by_id: list[bytes] = [b""] * vocab_size
    for token, token_id in vocab.items():
        token_bytes = decode_vocab_token(token)
        if len(token_bytes) > MAX_TOKEN_BYTES:
            raise ValueError(f"token {token_id} exceeds {MAX_TOKEN_BYTES} bytes")
        tokens_by_id[token_id] = token_bytes

    specials = special_ids(tokenizer_json, vocab, pad_id, eos_id)
    if len(specials) > MAX_SPECIAL_TOKENS:
        raise ValueError(f"tokenizer has {len(specials)} special tokens; CTOK cap is {MAX_SPECIAL_TOKENS}")
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

    special_token_ids = sorted(specials)
    special_token_offset = token_data_offset + len(token_data)
    merge_offset = special_token_offset + len(special_token_ids) * 4
    header = bytearray(HEADER_BYTES)
    header[0:4] = b"CTOK"
    struct.pack_into("<HHIIIIQQQIIIH", header, 4, VERSION, HEADER_BYTES,
                     vocab_size, len(merge_records), ENTRY_BYTES, MERGE_BYTES,
                     vocab_offset, token_data_offset, merge_offset, 256, pad_id,
                     eos_id, MAX_TOKEN_BYTES)
    struct.pack_into("<H", header, 62, family)
    struct.pack_into("<IQ", header, 64, len(special_token_ids), special_token_offset)

    output = bytearray(header)
    output.extend(entries)
    output.extend(token_data)
    for token_id in special_token_ids:
        output.extend(struct.pack("<I", token_id))
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
