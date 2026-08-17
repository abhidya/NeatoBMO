#!/usr/bin/env python3
"""Export Gemma GGUF SentencePiece metadata to the MCU CSPM tokenizer format."""

from __future__ import annotations

import argparse
import json
import struct
from dataclasses import dataclass, field
from pathlib import Path

from export_gemma_gguf_bmoq import GgufSource

HEADER_BYTES = 128
ENTRY_BYTES = 24
NODE_BYTES = 16
EDGE_BYTES = 8
VERSION = 1
MAX_PIECE_BYTES = 128

TYPE_NORMAL = 1
TYPE_CONTROL = 3
TYPE_USER_DEFINED = 4
TYPE_BYTE = 6

FLAG_SPECIAL = 0x0001
FLAG_BYTE = 0x0002

NO_TOKEN = 0xFFFFFFFF


@dataclass
class TrieNode:
    children: dict[int, int] = field(default_factory=dict)
    token_id: int = NO_TOKEN
    score: float = 0.0


@dataclass(frozen=True)
class GemmaTokenizerMetadata:
    tokens: list[str]
    scores: list[float]
    token_types: list[int]
    bos_token_id: int
    eos_token_id: int
    pad_token_id: int
    unk_token_id: int
    add_bos_token: bool
    add_eos_token: bool
    add_space_prefix: bool


def token_piece_bytes(token: str, token_type: int) -> bytes:
    if token_type == TYPE_BYTE and len(token) == 6 and token.startswith("<0x") and token.endswith(">"):
        return bytes([int(token[3:5], 16)])
    return token.encode("utf-8")


def metadata_from_gguf(path: Path) -> GemmaTokenizerMetadata:
    source = GgufSource(path)
    meta = source.metadata
    tokens = [str(token) for token in meta["tokenizer.ggml.tokens"]]
    scores = [float(score) for score in meta["tokenizer.ggml.scores"]]
    token_types = [int(token_type) for token_type in meta["tokenizer.ggml.token_type"]]
    if not (len(tokens) == len(scores) == len(token_types)):
        raise ValueError("Gemma tokenizer tokens/scores/token_type lengths differ")
    if meta.get("tokenizer.ggml.model") != "llama":
        raise ValueError(f"unsupported Gemma tokenizer model: {meta.get('tokenizer.ggml.model')!r}")
    return GemmaTokenizerMetadata(
        tokens=tokens,
        scores=scores,
        token_types=token_types,
        bos_token_id=int(meta["tokenizer.ggml.bos_token_id"]),
        eos_token_id=int(meta["tokenizer.ggml.eos_token_id"]),
        pad_token_id=int(meta["tokenizer.ggml.padding_token_id"]),
        unk_token_id=int(meta["tokenizer.ggml.unknown_token_id"]),
        add_bos_token=bool(meta.get("tokenizer.ggml.add_bos_token", False)),
        add_eos_token=bool(meta.get("tokenizer.ggml.add_eos_token", False)),
        add_space_prefix=bool(meta.get("tokenizer.ggml.add_space_prefix", False)),
    )


def build_trie(metadata: GemmaTokenizerMetadata, piece_bytes: list[bytes]) -> list[TrieNode]:
    nodes = [TrieNode()]
    for token_id, (token_type, raw) in enumerate(zip(metadata.token_types, piece_bytes)):
        if token_type != TYPE_NORMAL or not raw:
            continue
        node_id = 0
        for byte_value in raw:
            node = nodes[node_id]
            child = node.children.get(byte_value)
            if child is None:
                child = len(nodes)
                node.children[byte_value] = child
                nodes.append(TrieNode())
            node_id = child
        nodes[node_id].token_id = token_id
        nodes[node_id].score = metadata.scores[token_id]
    return nodes


def build_cspm(metadata: GemmaTokenizerMetadata) -> bytes:
    vocab_size = len(metadata.tokens)
    piece_bytes = [
        token_piece_bytes(token, token_type)
        for token, token_type in zip(metadata.tokens, metadata.token_types)
    ]
    max_piece = max(len(piece) for piece in piece_bytes)
    if max_piece > MAX_PIECE_BYTES:
        raise ValueError(f"max piece length {max_piece} exceeds {MAX_PIECE_BYTES}")

    byte_token_ids: dict[int, int] = {}
    for token_id, (token_type, raw) in enumerate(zip(metadata.token_types, piece_bytes)):
        if token_type == TYPE_BYTE:
            if len(raw) != 1:
                raise ValueError(f"byte token {token_id} is not one byte")
            byte_token_ids[raw[0]] = token_id
    missing = sorted(set(range(256)) - set(byte_token_ids))
    if missing:
        raise ValueError(f"missing byte fallback tokens: {missing[:8]}")

    special_ids = {
        metadata.bos_token_id,
        metadata.eos_token_id,
        metadata.pad_token_id,
        metadata.unk_token_id,
    }
    for token_id, token_type in enumerate(metadata.token_types):
        if token_type in (TYPE_CONTROL, TYPE_USER_DEFINED):
            special_ids.add(token_id)

    nodes = build_trie(metadata, piece_bytes)
    flat_edges: list[tuple[int, int]] = []
    node_records: list[tuple[int, int, int, float]] = []
    for node in nodes:
        first_edge = len(flat_edges)
        items = sorted(node.children.items())
        flat_edges.extend(items)
        node_records.append((first_edge, len(items), node.token_id, node.score))

    entry_offset = HEADER_BYTES
    node_offset = entry_offset + vocab_size * ENTRY_BYTES
    edge_offset = node_offset + len(nodes) * NODE_BYTES
    piece_data_offset = edge_offset + len(flat_edges) * EDGE_BYTES

    piece_data = bytearray()
    entries = bytearray()
    for token_id, raw in enumerate(piece_bytes):
        flags = 0
        byte_value = 0
        if token_id in special_ids:
            flags |= FLAG_SPECIAL
        if metadata.token_types[token_id] == TYPE_BYTE:
            flags |= FLAG_BYTE
            byte_value = raw[0]
        data_offset = piece_data_offset + len(piece_data)
        piece_data.extend(raw)
        entries.extend(
            struct.pack(
                "<QHHfHHI",
                data_offset,
                len(raw),
                metadata.token_types[token_id],
                metadata.scores[token_id],
                flags,
                byte_value,
                0,
            )
        )

    header = bytearray(HEADER_BYTES)
    header[0:4] = b"CSPM"
    struct.pack_into(
        "<HHIII3I4Q5IH3?36x",
        header,
        4,
        VERSION,
        HEADER_BYTES,
        vocab_size,
        len(nodes),
        len(flat_edges),
        ENTRY_BYTES,
        NODE_BYTES,
        EDGE_BYTES,
        entry_offset,
        node_offset,
        edge_offset,
        piece_data_offset,
        metadata.bos_token_id,
        metadata.eos_token_id,
        metadata.pad_token_id,
        metadata.unk_token_id,
        256,
        MAX_PIECE_BYTES,
        metadata.add_bos_token,
        metadata.add_eos_token,
        metadata.add_space_prefix,
    )

    output = bytearray(header)
    output.extend(entries)
    for first_edge, edge_count, token_id, score in node_records:
        output.extend(struct.pack("<IHHIf", first_edge, edge_count, 0, token_id, score))
    for byte_value, child_node in flat_edges:
        output.extend(struct.pack("<B3xI", byte_value, child_node))
    output.extend(piece_data)
    return bytes(output)


def inspect_summary(metadata: GemmaTokenizerMetadata, cspm: bytes) -> dict:
    type_counts: dict[int, int] = {}
    for token_type in metadata.token_types:
        type_counts[token_type] = type_counts.get(token_type, 0) + 1
    return {
        "format": "CSPM",
        "version": VERSION,
        "vocab_size": len(metadata.tokens),
        "bytes": len(cspm),
        "bos_token_id": metadata.bos_token_id,
        "eos_token_id": metadata.eos_token_id,
        "pad_token_id": metadata.pad_token_id,
        "unk_token_id": metadata.unk_token_id,
        "add_bos_token": metadata.add_bos_token,
        "add_eos_token": metadata.add_eos_token,
        "add_space_prefix": metadata.add_space_prefix,
        "token_type_counts": type_counts,
        "max_piece_bytes": max(len(token_piece_bytes(t, ty)) for t, ty in zip(metadata.tokens, metadata.token_types)),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("gguf", type=Path)
    parser.add_argument("output", type=Path, nargs="?")
    parser.add_argument("--inspect-only", action="store_true")
    args = parser.parse_args()

    metadata = metadata_from_gguf(args.gguf)
    cspm = build_cspm(metadata)
    if args.inspect_only:
        print(json.dumps(inspect_summary(metadata, cspm), sort_keys=True, indent=2))
        return
    if args.output is None:
        raise SystemExit("output path is required unless --inspect-only is used")
    args.output.write_bytes(cspm)
    print(f"{args.output}: vocab={len(metadata.tokens)} bytes={len(cspm)}")


if __name__ == "__main__":
    main()
