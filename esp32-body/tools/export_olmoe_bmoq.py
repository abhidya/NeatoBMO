#!/usr/bin/env python3
"""Convert a local OLMoE Hugging Face checkpoint into streamed BMOQ.

The converter never downloads model files and never loads the full checkpoint
into RAM. Safetensors shards are opened tensor-by-tensor and matrix tensors are
quantized row-by-row into grouped symmetric Q4 plus float32 scales.
"""

from __future__ import annotations

import argparse
import json
import math
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Protocol

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

ARCH_OLMOE = 1
MANIFEST_TENSOR_ID = 0x4F4C4D45
SCALE_ID_OFFSET = 0x00800000


@dataclass(frozen=True)
class OlmoeConfig:
    hidden_size: int = 2048
    intermediate_size: int = 1024
    num_hidden_layers: int = 16
    num_attention_heads: int = 16
    num_key_value_heads: int = 16
    num_experts: int = 64
    num_experts_per_tok: int = 8
    vocab_size: int = 50304
    max_position_embeddings: int = 4096
    rope_theta: int = 10000
    eos_token_id: int = 50279
    pad_token_id: int = 1
    tie_word_embeddings: bool = False

    @classmethod
    def from_json(cls, path: Path) -> "OlmoeConfig":
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            hidden_size=int(data["hidden_size"]),
            intermediate_size=int(data["intermediate_size"]),
            num_hidden_layers=int(data["num_hidden_layers"]),
            num_attention_heads=int(data["num_attention_heads"]),
            num_key_value_heads=int(data["num_key_value_heads"]),
            num_experts=int(data["num_experts"]),
            num_experts_per_tok=int(data["num_experts_per_tok"]),
            vocab_size=int(data["vocab_size"]),
            max_position_embeddings=int(data["max_position_embeddings"]),
            rope_theta=int(float(data["rope_theta"])),
            eos_token_id=int(data["eos_token_id"]),
            pad_token_id=int(data["pad_token_id"]),
            tie_word_embeddings=bool(data.get("tie_word_embeddings", False)),
        )

    def validate_official(self) -> None:
        expected = OlmoeConfig()
        if self != expected:
            raise ValueError(
                "config is not allenai/OLMoE-1B-7B-0924. Use "
                "--allow-nonstandard only for synthetic tests or deliberate forks."
            )


class TensorSource(Protocol):
    def shape(self, name: str) -> tuple[int, ...]: ...

    def row_floats(self, name: str, row: int) -> list[float]: ...

    def values_floats(self, name: str) -> list[float]: ...


@dataclass(frozen=True)
class LogicalTensor:
    tensor_id: int
    hf_name: str
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
    hf_name: str
    writer: Callable[[object], None]
    data_offset: int = 0


def align_up(value: int, alignment: int = ALIGNMENT) -> int:
    return (value + alignment - 1) // alignment * alignment


def layer_base(layer: int) -> int:
    return 1000 + layer * 10000


def scale_id(tensor_id: int) -> int:
    return tensor_id + SCALE_ID_OFFSET


def expected_logical_tensors(config: OlmoeConfig) -> list[LogicalTensor]:
    h = config.hidden_size
    i = config.intermediate_size
    vocab = config.vocab_size
    experts = config.num_experts
    tensors = [
        LogicalTensor(1, "model.embed_tokens.weight", "tok_emb", (vocab, h)),
        LogicalTensor(2, "model.norm.weight", "norm", (h,)),
        LogicalTensor(3, "lm_head.weight", "lm_head", (vocab, h)),
    ]
    for layer in range(config.num_hidden_layers):
        base = layer_base(layer)
        prefix = f"model.layers.{layer}"
        tag = f"l{layer:02d}"
        tensors.extend(
            [
                LogicalTensor(base + 1, f"{prefix}.input_layernorm.weight", f"{tag}.in_norm", (h,)),
                LogicalTensor(base + 2, f"{prefix}.post_attention_layernorm.weight", f"{tag}.postnorm", (h,)),
                LogicalTensor(base + 10, f"{prefix}.self_attn.q_proj.weight", f"{tag}.q", (h, h)),
                LogicalTensor(base + 11, f"{prefix}.self_attn.k_proj.weight", f"{tag}.k", (h, h)),
                LogicalTensor(base + 12, f"{prefix}.self_attn.v_proj.weight", f"{tag}.v", (h, h)),
                LogicalTensor(base + 13, f"{prefix}.self_attn.o_proj.weight", f"{tag}.o", (h, h)),
                LogicalTensor(base + 20, f"{prefix}.mlp.gate.weight", f"{tag}.router", (experts, h)),
            ]
        )
        for expert in range(experts):
            eprefix = f"{prefix}.mlp.experts.{expert}"
            etag = f"{tag}.e{expert:02d}"
            tensors.extend(
                [
                    LogicalTensor(base + 100 + expert * 10 + 1, f"{eprefix}.gate_proj.weight", f"{etag}.gate", (i, h)),
                    LogicalTensor(base + 100 + expert * 10 + 2, f"{eprefix}.up_proj.weight", f"{etag}.up", (i, h)),
                    LogicalTensor(base + 100 + expert * 10 + 3, f"{eprefix}.down_proj.weight", f"{etag}.down", (h, i)),
                ]
            )
    return tensors


def pack_entry(t: PhysicalTensor) -> bytes:
    name = t.short_name.encode("ascii")[:16].ljust(16, b"\0")
    return struct.pack(
        "<IHH4IQQII16s",
        t.tensor_id,
        t.dtype,
        t.quant_group,
        t.dimensions[0],
        t.dimensions[1],
        t.dimensions[2],
        t.dimensions[3],
        t.data_offset,
        t.byte_length,
        t.layout,
        0,
        name,
    )


def pack_header(config: OlmoeConfig, tensor_count: int, directory_offset: int, quant_group: int) -> bytes:
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
        ARCH_OLMOE,
        0,
        config.hidden_size,
        config.intermediate_size,
        config.num_hidden_layers,
        config.num_attention_heads,
        config.num_key_value_heads,
        config.num_experts,
        config.num_experts_per_tok,
        config.vocab_size,
        config.max_position_embeddings,
        config.rope_theta,
        config.eos_token_id,
        config.pad_token_id,
        quant_group,
        MANIFEST_TENSOR_ID,
    )
    return bytes(header)


def quantize_rows(block, group_size: int) -> tuple[bytes, bytes]:
    """Vectorized equivalent of quantize_row over a contiguous block of rows.

    Bit-for-bit identical to the scalar path: the scale is derived and applied
    in float64 exactly as the original Python did (only its stored copy is
    narrowed to float32), and numpy's rint uses the same half-to-even rule as
    Python's round(). Producing packed nibbles and scales in one pass also
    removes the duplicate quantization the row writers used to perform.
    """
    import numpy as np

    rows, columns = block.shape
    if columns % group_size:
        raise ValueError("Q4 group size must divide every matrix row")
    grouped = block.reshape(rows, columns // group_size, group_size)
    max_abs = np.abs(grouped).max(axis=2)
    nonzero = max_abs > 0.0
    scale = np.where(nonzero, max_abs / 7.0, 1.0)
    quantized = np.rint(grouped / scale[:, :, None])
    np.clip(quantized, -8.0, 7.0, out=quantized)
    quantized = np.where(nonzero[:, :, None], quantized, 0.0)
    nibbles = (quantized.reshape(rows, columns).astype(np.int8) & 0x0F).astype(np.uint8)
    packed = nibbles[:, 0::2] | (nibbles[:, 1::2] << 4)
    return packed.tobytes(), scale.astype(np.float32).tobytes()


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


def dense_writer(source: TensorSource, tensor: LogicalTensor) -> Callable[[object], None]:
    def write(output: object) -> None:
        for value in source.values_floats(tensor.hf_name):
            output.write(struct.pack("<f", value))

    return write


ROW_BLOCK = 512


def _quantized_blocks(source: TensorSource, tensor: LogicalTensor, group_size: int):
    """Yield (packed, scales) per row block, quantizing each row exactly once."""
    rows = tensor.shape[0]
    block_reader = getattr(source, "rows_block", None)
    for first in range(0, rows, ROW_BLOCK):
        count = min(ROW_BLOCK, rows - first)
        if block_reader is not None:
            yield quantize_rows(block_reader(tensor.hf_name, first, count), group_size)
            continue
        packed = bytearray()
        scales = bytearray()
        for row_index in range(first, first + count):
            row_packed, row_scales = quantize_row(
                source.row_floats(tensor.hf_name, row_index), group_size
            )
            packed.extend(row_packed)
            scales.extend(row_scales)
        yield bytes(packed), bytes(scales)


def q4_writers(source: TensorSource, tensor: LogicalTensor, group_size: int) -> tuple[Callable[[object], None], Callable[[object], None]]:
    def write_weights(output: object) -> None:
        for packed, _ in _quantized_blocks(source, tensor, group_size):
            output.write(packed)

    def write_scales(output: object) -> None:
        for _, scales in _quantized_blocks(source, tensor, group_size):
            output.write(scales)

    return write_weights, write_scales


def physical_tensors(source: TensorSource, config: OlmoeConfig, group_size: int) -> list[PhysicalTensor]:
    result: list[PhysicalTensor] = []
    for logical in expected_logical_tensors(config):
        actual_shape = source.shape(logical.hf_name)
        if actual_shape != logical.shape:
            raise ValueError(f"{logical.hf_name}: expected {logical.shape}, found {actual_shape}")
        if len(logical.shape) == 1:
            result.append(
                PhysicalTensor(
                    tensor_id=logical.tensor_id,
                    dtype=DTYPE_F32,
                    quant_group=0,
                    dimensions=(logical.shape[0], 1, 1, 1),
                    byte_length=logical.shape[0] * 4,
                    layout=LAYOUT_DENSE_F32,
                    short_name=logical.short_name,
                    hf_name=logical.hf_name,
                    writer=dense_writer(source, logical),
                )
            )
        elif len(logical.shape) == 2:
            rows, columns = logical.shape
            if columns % group_size:
                raise ValueError(
                    f"{logical.hf_name}: group size {group_size} does not divide {columns} columns"
                )
            groups = columns // group_size
            write_weights, write_scales = q4_writers(source, logical, group_size)
            result.append(
                PhysicalTensor(
                    tensor_id=logical.tensor_id,
                    dtype=DTYPE_Q4_SYM,
                    quant_group=group_size,
                    dimensions=(rows, columns, 1, 1),
                    byte_length=rows * columns // 2,
                    layout=LAYOUT_Q4_ROW_MAJOR,
                    short_name=logical.short_name,
                    hf_name=logical.hf_name,
                    writer=write_weights,
                )
            )
            result.append(
                PhysicalTensor(
                    tensor_id=scale_id(logical.tensor_id),
                    dtype=DTYPE_F32,
                    quant_group=group_size,
                    dimensions=(rows, groups, 1, 1),
                    byte_length=rows * groups * 4,
                    layout=LAYOUT_GROUP_SCALES_F32,
                    short_name=(logical.short_name + ".s"),
                    hf_name=logical.hf_name + "#scales",
                    writer=write_scales,
                )
            )
        else:
            raise ValueError(f"unsupported tensor rank for {logical.hf_name}")
    return result


def write_bmoq(source: TensorSource, output_path: Path, config: OlmoeConfig, group_size: int) -> dict:
    if group_size < 2 or group_size % 2:
        raise ValueError("group size must be even")
    tensors = physical_tensors(source, config, group_size)
    tensor_count = len(tensors) + 1
    directory_offset = HEADER_BYTES
    offset = align_up(directory_offset + tensor_count * ENTRY_BYTES)
    for tensor in tensors:
        tensor.data_offset = offset
        offset = align_up(offset + tensor.byte_length)
    manifest_body = {
        "format": "BMOQ",
        "version": VERSION,
        "architecture": "olmoe",
        "config": config.__dict__,
        "quantization": {"type": "grouped_symmetric_q4", "group_size": group_size},
        "tensor_count_without_manifest": len(tensors),
        "tensors": [
            {
                "id": t.tensor_id,
                "name": t.hf_name,
                "short_name": t.short_name,
                "dtype": t.dtype,
                "layout": t.layout,
                "shape": list(t.dimensions),
                "offset": t.data_offset,
                "bytes": t.byte_length,
            }
            for t in tensors
        ],
    }
    manifest_bytes = json.dumps(manifest_body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    manifest = PhysicalTensor(
        tensor_id=MANIFEST_TENSOR_ID,
        dtype=DTYPE_OPAQUE,
        quant_group=0,
        dimensions=(len(manifest_bytes), 1, 1, 1),
        byte_length=len(manifest_bytes),
        layout=LAYOUT_OPAQUE,
        short_name="manifest",
        hf_name="bmoq.manifest.json",
        writer=lambda output: output.write(manifest_bytes),
        data_offset=offset,
    )
    tensors.append(manifest)

    with output_path.open("wb") as output:
        output.write(pack_header(config, len(tensors), directory_offset, group_size))
        for tensor in tensors:
            output.write(pack_entry(tensor))
        for tensor in tensors:
            output.seek(tensor.data_offset)
            tensor.writer(output)
    return {"tensor_count": len(tensors), "bytes": output_path.stat().st_size, "manifest_bytes": len(manifest_bytes)}


class SafetensorSource:
    def __init__(self, model_dir: Path):
        self.model_dir = model_dir
        self.index: dict[str, Path] = {}
        index_path = model_dir / "model.safetensors.index.json"
        if index_path.exists():
            data = json.loads(index_path.read_text(encoding="utf-8"))
            self.index = {name: model_dir / shard for name, shard in data["weight_map"].items()}
        else:
            for path in sorted(model_dir.glob("*.safetensors")):
                for name in self._headers(path):
                    self.index[name] = path

    def _headers(self, path: Path) -> dict:
        with path.open("rb") as file:
            header_len = struct.unpack("<Q", file.read(8))[0]
            return json.loads(file.read(header_len).decode("utf-8"))

    def _shard_header(self, path: Path) -> tuple[dict, int]:
        """Parse each shard header once; re-parsing it per row made export of a
        7B checkpoint take days rather than minutes."""
        cached = getattr(self, "_header_cache", None)
        if cached is None:
            cached = {}
            self._header_cache = cached
        entry = cached.get(path)
        if entry is None:
            with path.open("rb") as file:
                header_len = struct.unpack("<Q", file.read(8))[0]
                header = json.loads(file.read(header_len).decode("utf-8"))
            entry = (header, 8 + header_len)
            cached[path] = entry
        return entry

    def _tensor_meta(self, name: str) -> tuple[Path, dict, int]:
        try:
            path = self.index[name]
        except KeyError as exc:
            raise KeyError(f"missing tensor {name}") from exc
        header, data_start = self._shard_header(path)
        return path, header[name], data_start

    def rows_block(self, name: str, first_row: int, row_count: int):
        """Read a contiguous row block as float64, matching row_floats' values."""
        import numpy as np

        shape = self.shape(name)
        if len(shape) != 2:
            raise ValueError(f"{name} is not a matrix")
        columns = shape[1]
        path, meta, data_start = self._tensor_meta(name)
        dtype = meta["dtype"]
        item_bytes = {"F32": 4, "F16": 2, "BF16": 2}[dtype]
        count = row_count * columns
        start = data_start + int(meta["data_offsets"][0]) + first_row * columns * item_bytes
        with path.open("rb") as file:
            file.seek(start)
            raw = file.read(count * item_bytes)
        if dtype == "F32":
            values = np.frombuffer(raw, dtype="<f4")
        elif dtype == "F16":
            values = np.frombuffer(raw, dtype="<f2")
        else:
            widened = np.frombuffer(raw, dtype="<u2").astype(np.uint32) << 16
            values = widened.view(np.float32)
        return values.astype(np.float64).reshape(row_count, columns)

    def shape(self, name: str) -> tuple[int, ...]:
        _, meta, _ = self._tensor_meta(name)
        return tuple(int(v) for v in meta["shape"])

    def row_floats(self, name: str, row: int) -> list[float]:
        shape = self.shape(name)
        if len(shape) != 2:
            raise ValueError(f"{name} is not a matrix")
        return self._read_floats(name, row * shape[1], shape[1])

    def values_floats(self, name: str) -> list[float]:
        shape = self.shape(name)
        return self._read_floats(name, 0, math.prod(shape))

    def _read_floats(self, name: str, first_value: int, count: int) -> list[float]:
        path, meta, data_start = self._tensor_meta(name)
        dtype = meta["dtype"]
        item_bytes = {"F32": 4, "F16": 2, "BF16": 2}[dtype]
        start = data_start + int(meta["data_offsets"][0]) + first_value * item_bytes
        with path.open("rb") as file:
            file.seek(start)
            data = file.read(count * item_bytes)
        if dtype == "F32":
            return list(struct.unpack("<" + "f" * count, data))
        if dtype == "F16":
            return [float(v) for v in struct.unpack("<" + "e" * count, data)]
        return [_bf16_to_float(data[i : i + 2]) for i in range(0, len(data), 2)]


def _bf16_to_float(two: bytes) -> float:
    bits = struct.unpack("<H", two)[0] << 16
    return struct.unpack("<f", struct.pack("<I", bits))[0]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("model_dir", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--group-size", type=int, default=32)
    parser.add_argument("--allow-nonstandard", action="store_true")
    args = parser.parse_args()

    config = OlmoeConfig.from_json(args.model_dir / "config.json")
    if not args.allow_nonstandard:
        config.validate_official()
    source = SafetensorSource(args.model_dir)
    summary = write_bmoq(source, args.output, config, args.group_size)
    print(
        f"{args.output}: tensors={summary['tensor_count']} "
        f"manifest={summary['manifest_bytes']} bytes total={summary['bytes']}"
    )


if __name__ == "__main__":
    main()
