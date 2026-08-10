#!/usr/bin/env python3
"""Convert a local Gemma 3 GGUF into streamed BMOQ.

The converter is offline-only and streams GGUF tensors row-by-row. Q8_0 tensors
are dequantized a row at a time and requantized to the firmware's grouped
symmetric Q4 format plus float32 scales.
"""

from __future__ import annotations

import argparse
import json
import math
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

HEADER_BYTES = 4096
ALIGNMENT = 4096
ENTRY_BYTES = 64
VERSION = 1
ENDIAN_MARKER = 0x01020304

DTYPE_OPAQUE = 0
DTYPE_F32 = 1
DTYPE_Q4_SYM = 2

LAYOUT_OPAQUE = 0
LAYOUT_Q4_ROW_MAJOR = 1
LAYOUT_GROUP_SCALES_F32 = 2
LAYOUT_DENSE_F32 = 3

ARCH_GEMMA3 = 2
MANIFEST_TENSOR_ID = 0x47454D33
SCALE_ID_OFFSET = 0x00800000

GGUF_TYPE_F32 = 0
GGUF_TYPE_Q8_0 = 8
GGUF_VALUE_STRING = 8
GGUF_VALUE_ARRAY = 9

VALUE_FORMATS = {
    0: "B",
    1: "b",
    2: "H",
    3: "h",
    4: "I",
    5: "i",
    6: "f",
    7: "?",
    10: "Q",
    11: "q",
    12: "d",
}


@dataclass(frozen=True)
class GemmaConfig:
    hidden_size: int
    intermediate_size: int
    num_hidden_layers: int
    num_attention_heads: int
    num_key_value_heads: int
    vocab_size: int
    max_position_embeddings: int
    rope_theta: int
    eos_token_id: int
    pad_token_id: int
    quant_group: int
    head_dim: int
    sliding_window: int
    tie_word_embeddings: bool

    @classmethod
    def from_metadata(cls, meta: dict, quant_group: int) -> "GemmaConfig":
        arch = meta.get("general.architecture")
        if arch != "gemma3":
            raise ValueError(f"unsupported architecture: {arch!r}")
        key_length = int(meta["gemma3.attention.key_length"])
        value_length = int(meta["gemma3.attention.value_length"])
        if key_length != value_length:
            raise ValueError("Gemma key_length and value_length must match")
        return cls(
            hidden_size=int(meta["gemma3.embedding_length"]),
            intermediate_size=int(meta["gemma3.feed_forward_length"]),
            num_hidden_layers=int(meta["gemma3.block_count"]),
            num_attention_heads=int(meta["gemma3.attention.head_count"]),
            num_key_value_heads=int(meta["gemma3.attention.head_count_kv"]),
            vocab_size=len(meta["tokenizer.ggml.tokens"]),
            max_position_embeddings=int(meta["gemma3.context_length"]),
            rope_theta=int(float(meta["gemma3.rope.freq_base"])),
            eos_token_id=int(meta["tokenizer.ggml.eos_token_id"]),
            pad_token_id=int(meta.get("tokenizer.ggml.padding_token_id", 0)),
            quant_group=quant_group,
            head_dim=key_length,
            sliding_window=int(meta.get("gemma3.attention.sliding_window", 0)),
            tie_word_embeddings=True,
        )


@dataclass(frozen=True)
class GgufTensor:
    name: str
    shape: tuple[int, ...]
    ggml_type: int
    offset: int


@dataclass(frozen=True)
class LogicalTensor:
    tensor_id: int
    gguf_name: str
    short_name: str
    shape: tuple[int, ...]


@dataclass
class PhysicalTensor:
    tensor_id: int
    dtype: int
    quant_group: int
    dimensions: tuple[int, int, int, int]
    byte_length: int
    layout: int
    short_name: str
    gguf_name: str
    writer: Callable[[object], None]
    data_offset: int = 0


def scale_id(tensor_id: int) -> int:
    return tensor_id + SCALE_ID_OFFSET


def layer_base(layer: int) -> int:
    return 2000 + layer * 100


def expected_logical_tensors(config: GemmaConfig) -> list[LogicalTensor]:
    h = config.hidden_size
    hd = config.head_dim
    q = config.num_attention_heads * hd
    kv = config.num_key_value_heads * hd
    ff = config.intermediate_size
    tensors = [
        LogicalTensor(1, "token_embd.weight", "tok_emb", (config.vocab_size, h)),
        LogicalTensor(2, "output_norm.weight", "norm", (h,)),
    ]
    for layer in range(config.num_hidden_layers):
        base = layer_base(layer)
        prefix = f"blk.{layer}"
        tag = f"l{layer:02d}"
        tensors.extend(
            [
                LogicalTensor(base + 1, f"{prefix}.attn_norm.weight", f"{tag}.attnn", (h,)),
                LogicalTensor(base + 2, f"{prefix}.post_attention_norm.weight", f"{tag}.panorm", (h,)),
                LogicalTensor(base + 3, f"{prefix}.ffn_norm.weight", f"{tag}.ffnn", (h,)),
                LogicalTensor(base + 4, f"{prefix}.post_ffw_norm.weight", f"{tag}.pfnorm", (h,)),
                LogicalTensor(base + 5, f"{prefix}.attn_q_norm.weight", f"{tag}.qnorm", (hd,)),
                LogicalTensor(base + 6, f"{prefix}.attn_k_norm.weight", f"{tag}.knorm", (hd,)),
                LogicalTensor(base + 10, f"{prefix}.attn_q.weight", f"{tag}.q", (q, h)),
                LogicalTensor(base + 11, f"{prefix}.attn_k.weight", f"{tag}.k", (kv, h)),
                LogicalTensor(base + 12, f"{prefix}.attn_v.weight", f"{tag}.v", (kv, h)),
                LogicalTensor(base + 13, f"{prefix}.attn_output.weight", f"{tag}.o", (h, q)),
                LogicalTensor(base + 20, f"{prefix}.ffn_gate.weight", f"{tag}.gate", (ff, h)),
                LogicalTensor(base + 21, f"{prefix}.ffn_up.weight", f"{tag}.up", (ff, h)),
                LogicalTensor(base + 22, f"{prefix}.ffn_down.weight", f"{tag}.down", (h, ff)),
            ]
        )
    return tensors


def align_up(value: int, alignment: int = ALIGNMENT) -> int:
    return (value + alignment - 1) // alignment * alignment


def _read_string(file: object) -> str:
    size = struct.unpack("<Q", file.read(8))[0]
    return file.read(size).decode("utf-8", "replace")


def _read_value(file: object, value_type: int):
    if value_type == GGUF_VALUE_STRING:
        return _read_string(file)
    if value_type == GGUF_VALUE_ARRAY:
        element_type = struct.unpack("<I", file.read(4))[0]
        count = struct.unpack("<Q", file.read(8))[0]
        return [_read_value(file, element_type) for _ in range(count)]
    try:
        fmt = VALUE_FORMATS[value_type]
    except KeyError as exc:
        raise ValueError(f"unsupported GGUF metadata value type {value_type}") from exc
    return struct.unpack("<" + fmt, file.read(struct.calcsize("<" + fmt)))[0]


class GgufSource:
    def __init__(self, path: Path):
        self.path = path
        self.metadata: dict = {}
        self.tensors: dict[str, GgufTensor] = {}
        with path.open("rb") as file:
            if file.read(4) != b"GGUF":
                raise ValueError("not a GGUF file")
            version = struct.unpack("<I", file.read(4))[0]
            if version != 3:
                raise ValueError(f"unsupported GGUF version {version}")
            tensor_count, metadata_count = struct.unpack("<QQ", file.read(16))
            for _ in range(metadata_count):
                key = _read_string(file)
                value_type = struct.unpack("<I", file.read(4))[0]
                self.metadata[key] = _read_value(file, value_type)
            infos: list[GgufTensor] = []
            for _ in range(tensor_count):
                name = _read_string(file)
                rank = struct.unpack("<I", file.read(4))[0]
                dims = struct.unpack("<" + "Q" * rank, file.read(8 * rank))
                ggml_type = struct.unpack("<I", file.read(4))[0]
                offset = struct.unpack("<Q", file.read(8))[0]
                shape = tuple(reversed(tuple(int(v) for v in dims)))
                infos.append(GgufTensor(name, shape, ggml_type, offset))
            alignment = int(self.metadata.get("general.alignment", 32))
            self.data_start = align_up(file.tell(), alignment)
            self.tensors = {tensor.name: tensor for tensor in infos}

    def shape(self, name: str) -> tuple[int, ...]:
        return self.tensors[name].shape

    def row_floats(self, name: str, row: int) -> list[float]:
        tensor = self.tensors[name]
        if len(tensor.shape) != 2:
            raise ValueError(f"{name} is not a matrix")
        rows, columns = tensor.shape
        if row < 0 or row >= rows:
            raise IndexError(row)
        if tensor.ggml_type != GGUF_TYPE_Q8_0:
            raise ValueError(f"{name}: expected Q8_0, found type {tensor.ggml_type}")
        if columns % 32:
            raise ValueError(f"{name}: Q8_0 columns must be divisible by 32")
        block_bytes = 34
        row_bytes = columns // 32 * block_bytes
        start = self.data_start + tensor.offset + row * row_bytes
        out: list[float] = []
        with self.path.open("rb") as file:
            file.seek(start)
            data = file.read(row_bytes)
        cursor = 0
        for _ in range(columns // 32):
            scale = _f16_to_float(data[cursor : cursor + 2])
            cursor += 2
            qs = data[cursor : cursor + 32]
            cursor += 32
            out.extend(struct.unpack("<" + "b" * 32, qs)[i] * scale for i in range(32))
        return out

    def values_floats(self, name: str) -> list[float]:
        tensor = self.tensors[name]
        if len(tensor.shape) != 1:
            raise ValueError(f"{name} is not a vector")
        if tensor.ggml_type != GGUF_TYPE_F32:
            raise ValueError(f"{name}: expected F32, found type {tensor.ggml_type}")
        count = tensor.shape[0]
        with self.path.open("rb") as file:
            file.seek(self.data_start + tensor.offset)
            data = file.read(count * 4)
        return list(struct.unpack("<" + "f" * count, data))


def _f16_to_float(two: bytes) -> float:
    return struct.unpack("<e", two)[0]


def quantize_row(row: list[float], group_size: int) -> tuple[bytes, bytes]:
    if len(row) % group_size:
        raise ValueError("Q4 group size must divide every matrix row")
    packed = bytearray(len(row) // 2)
    scales = bytearray()
    for group_start in range(0, len(row), group_size):
        group = row[group_start : group_start + group_size]
        max_abs = max(abs(v) for v in group)
        scale = max_abs / 7.0 if max_abs > 0.0 else 1.0
        scales.extend(struct.pack("<f", scale))
        for offset in range(0, group_size, 2):
            low = int(round(group[offset] / scale)) if max_abs > 0.0 else 0
            high = int(round(group[offset + 1] / scale)) if max_abs > 0.0 else 0
            low = max(-8, min(7, low)) & 0xF
            high = max(-8, min(7, high)) & 0xF
            packed[(group_start + offset) // 2] = low | (high << 4)
    return bytes(packed), bytes(scales)


def dense_writer(source: GgufSource, tensor: LogicalTensor) -> Callable[[object], None]:
    def write(output: object) -> None:
        for value in source.values_floats(tensor.gguf_name):
            output.write(struct.pack("<f", value))

    return write


def q4_writers(source: GgufSource, tensor: LogicalTensor, group_size: int) -> tuple[Callable[[object], None], Callable[[object], None]]:
    rows = tensor.shape[0]

    def write_weights(output: object) -> None:
        for row_index in range(rows):
            packed, _ = quantize_row(source.row_floats(tensor.gguf_name, row_index), group_size)
            output.write(packed)

    def write_scales(output: object) -> None:
        for row_index in range(rows):
            _, scales = quantize_row(source.row_floats(tensor.gguf_name, row_index), group_size)
            output.write(scales)

    return write_weights, write_scales


def physical_tensors(source: GgufSource, config: GemmaConfig) -> list[PhysicalTensor]:
    result: list[PhysicalTensor] = []
    for logical in expected_logical_tensors(config):
        actual_shape = source.shape(logical.gguf_name)
        if actual_shape != logical.shape:
            raise ValueError(f"{logical.gguf_name}: expected {logical.shape}, found {actual_shape}")
        if len(logical.shape) == 1:
            result.append(
                PhysicalTensor(
                    logical.tensor_id,
                    DTYPE_F32,
                    0,
                    (logical.shape[0], 1, 1, 1),
                    logical.shape[0] * 4,
                    LAYOUT_DENSE_F32,
                    logical.short_name,
                    logical.gguf_name,
                    dense_writer(source, logical),
                )
            )
        elif len(logical.shape) == 2:
            rows, columns = logical.shape
            if columns % config.quant_group:
                raise ValueError(f"{logical.gguf_name}: group size {config.quant_group} does not divide {columns}")
            groups = columns // config.quant_group
            write_weights, write_scales = q4_writers(source, logical, config.quant_group)
            result.append(
                PhysicalTensor(
                    logical.tensor_id,
                    DTYPE_Q4_SYM,
                    config.quant_group,
                    (rows, columns, 1, 1),
                    rows * columns // 2,
                    LAYOUT_Q4_ROW_MAJOR,
                    logical.short_name,
                    logical.gguf_name,
                    write_weights,
                )
            )
            result.append(
                PhysicalTensor(
                    scale_id(logical.tensor_id),
                    DTYPE_F32,
                    config.quant_group,
                    (rows, groups, 1, 1),
                    rows * groups * 4,
                    LAYOUT_GROUP_SCALES_F32,
                    logical.short_name + ".s",
                    logical.gguf_name + "#scales",
                    write_scales,
                )
            )
    return result


def pack_entry(tensor: PhysicalTensor) -> bytes:
    name = tensor.short_name.encode("ascii")[:16].ljust(16, b"\0")
    return struct.pack(
        "<IHH4IQQII16s",
        tensor.tensor_id,
        tensor.dtype,
        tensor.quant_group,
        *tensor.dimensions,
        tensor.data_offset,
        tensor.byte_length,
        tensor.layout,
        0,
        name,
    )


def pack_header(config: GemmaConfig, tensor_count: int, directory_offset: int) -> bytes:
    header = bytearray(HEADER_BYTES)
    struct.pack_into(
        "<4sH2x4IQ",
        header,
        0,
        b"BMOQ",
        VERSION,
        ENDIAN_MARKER,
        HEADER_BYTES,
        tensor_count,
        ENTRY_BYTES,
        directory_offset,
    )
    struct.pack_into(
        "<16I",
        header,
        32,
        ARCH_GEMMA3,
        0,
        config.hidden_size,
        config.intermediate_size,
        config.num_hidden_layers,
        config.num_attention_heads,
        config.num_key_value_heads,
        config.sliding_window,
        0,
        config.vocab_size,
        config.max_position_embeddings,
        config.rope_theta,
        config.eos_token_id,
        config.pad_token_id,
        config.quant_group,
        MANIFEST_TENSOR_ID,
    )
    return bytes(header)


def plan_bmoq(source: GgufSource, group_size: int) -> tuple[GemmaConfig, list[PhysicalTensor], bytes]:
    config = GemmaConfig.from_metadata(source.metadata, group_size)
    if group_size < 2 or group_size % 2:
        raise ValueError("group size must be even")
    tensors = physical_tensors(source, config)
    manifest_body = {
        "format": "BMOQ",
        "version": VERSION,
        "architecture": "gemma3",
        "config": config.__dict__,
        "quantization": {"type": "q8_0_to_grouped_symmetric_q4", "group_size": group_size},
        "source": {"path": str(source.path), "gguf_tensor_count": len(source.tensors)},
        "unsupported": [],
        "tensor_count_without_manifest": len(tensors),
        "tensors": [
            {
                "id": tensor.tensor_id,
                "name": tensor.gguf_name,
                "short_name": tensor.short_name,
                "dtype": tensor.dtype,
                "layout": tensor.layout,
                "shape": list(tensor.dimensions),
                "bytes": tensor.byte_length,
            }
            for tensor in tensors
        ],
    }
    return config, tensors, json.dumps(manifest_body, sort_keys=True, separators=(",", ":")).encode("utf-8")


def write_bmoq(source: GgufSource, output_path: Path, group_size: int) -> dict:
    config, tensors, manifest_bytes = plan_bmoq(source, group_size)
    directory_offset = HEADER_BYTES
    tensor_count = len(tensors) + 1
    offset = align_up(directory_offset + tensor_count * ENTRY_BYTES)
    for tensor in tensors:
        tensor.data_offset = offset
        offset = align_up(offset + tensor.byte_length)
    manifest = PhysicalTensor(
        MANIFEST_TENSOR_ID,
        DTYPE_OPAQUE,
        0,
        (len(manifest_bytes), 1, 1, 1),
        len(manifest_bytes),
        LAYOUT_OPAQUE,
        "manifest",
        "bmoq.manifest.json",
        lambda output: output.write(manifest_bytes),
        offset,
    )
    tensors.append(manifest)

    with output_path.open("wb") as output:
        output.write(pack_header(config, len(tensors), directory_offset))
        for tensor in tensors:
            output.write(pack_entry(tensor))
        for tensor in tensors:
            output.seek(tensor.data_offset)
            tensor.writer(output)
    return {"tensor_count": len(tensors), "bytes": output_path.stat().st_size, "manifest_bytes": len(manifest_bytes)}


def inspect_summary(source: GgufSource, group_size: int) -> dict:
    config, tensors, manifest_bytes = plan_bmoq(source, group_size)
    types = sorted({tensor.ggml_type for tensor in source.tensors.values()})
    return {
        "architecture": "gemma3",
        "config": config.__dict__,
        "gguf_tensor_count": len(source.tensors),
        "gguf_tensor_types": types,
        "bmoq_tensor_count": len(tensors) + 1,
        "manifest_bytes": len(manifest_bytes),
        "estimated_bmoq_bytes": align_up(HEADER_BYTES + (len(tensors) + 1) * ENTRY_BYTES)
        + sum(align_up(t.byte_length) for t in tensors)
        + align_up(len(manifest_bytes)),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("gguf", type=Path)
    parser.add_argument("output", type=Path, nargs="?")
    parser.add_argument("--group-size", type=int, default=32)
    parser.add_argument("--inspect-only", action="store_true")
    args = parser.parse_args()

    source = GgufSource(args.gguf)
    if args.inspect_only:
        print(json.dumps(inspect_summary(source, args.group_size), sort_keys=True, indent=2))
        return
    if args.output is None:
        raise SystemExit("output path is required unless --inspect-only is used")
    summary = write_bmoq(source, args.output, args.group_size)
    print(
        f"{args.output}: tensors={summary['tensor_count']} "
        f"manifest={summary['manifest_bytes']} bytes total={summary['bytes']}"
    )


if __name__ == "__main__":
    main()
