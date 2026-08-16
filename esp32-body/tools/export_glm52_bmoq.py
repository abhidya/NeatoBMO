#!/usr/bin/env python3
"""Convert a local GLM-5.2 Hugging Face checkpoint into streamed BMOQ v2.

The converter never downloads model files and never loads a full checkpoint into
RAM. Safetensors shards are opened tensor-by-tensor and matrices are quantized
row-by-row into grouped symmetric Q4 plus float32 scales. GLM's architecture
parameters live in the bounded BMOQ-v2 binary config tensor so firmware does
not need to parse a large JSON manifest before it can size MLA state.
"""

from __future__ import annotations

import argparse
import json
import math
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

from bmoq_config import (
    TYPE_BOOL,
    TYPE_F32,
    TYPE_U32,
    TYPE_U32_ARRAY,
    encode_config,
)

HEADER_BYTES = 4096
ALIGNMENT = 4096
ENTRY_BYTES = 64
VERSION = 2
ENDIAN_MARKER = 0x01020304

DTYPE_OPAQUE = 0
DTYPE_F32 = 1
DTYPE_Q4_SYM = 2

LAYOUT_OPAQUE = 0
LAYOUT_Q4_ROW_MAJOR = 1
LAYOUT_GROUP_SCALES_F32 = 2
LAYOUT_DENSE_F32 = 3

ARCH_GLM52 = 3
CONFIG_TENSOR_ID = 0x32474643
MANIFEST_TENSOR_ID = 0x474C4D35
SCALE_ID_OFFSET = 0x00800000

CFG_STOP_TOKEN_IDS = 1
CFG_RMS_NORM_EPS = 2
CFG_ATTENTION_SCALE = 3
CFG_ROUTED_SCALE = 4
CFG_MOE_INTERMEDIATE_SIZE = 1000
CFG_FIRST_DENSE_LAYERS = 1001
CFG_Q_LORA_RANK = 1002
CFG_KV_LORA_RANK = 1003
CFG_QK_NOPE_HEAD_DIM = 1004
CFG_QK_ROPE_HEAD_DIM = 1005
CFG_V_HEAD_DIM = 1006
CFG_SHARED_EXPERTS = 1007
CFG_EXPERT_GROUPS = 1008
CFG_TOPK_GROUPS = 1009
CFG_NORMALIZE_TOPK = 1010


@dataclass(frozen=True)
class Glm52Config:
    hidden_size: int
    dense_intermediate_size: int
    num_hidden_layers: int
    num_attention_heads: int
    num_experts: int
    num_experts_per_tok: int
    moe_intermediate_size: int
    first_dense_layers: int
    q_lora_rank: int
    kv_lora_rank: int
    qk_nope_head_dim: int
    qk_rope_head_dim: int
    v_head_dim: int
    shared_experts: int
    vocab_size: int
    max_position_embeddings: int
    rope_theta: int
    eos_token_id: int
    pad_token_id: int
    expert_groups: int
    topk_groups: int
    normalize_topk: bool
    rms_norm_epsilon: float
    routed_scale: float
    stop_token_ids: tuple[int, ...]
    quant_group: int

    @property
    def qk_head_dim(self) -> int:
        return self.qk_nope_head_dim + self.qk_rope_head_dim

    @property
    def attention_scale(self) -> float:
        return 1.0 / math.sqrt(float(self.qk_head_dim))

    @classmethod
    def from_json(cls, model_dir: Path, quant_group: int, max_context: int | None = None) -> "Glm52Config":
        data = json.loads((model_dir / "config.json").read_text(encoding="utf-8"))
        generation = _read_optional_json(model_dir / "generation_config.json")
        rope = data.get("rope_parameters") or {}
        stop_ids = _merged_stop_ids(data.get("eos_token_id"), generation.get("eos_token_id"))
        config = cls(
            hidden_size=int(data["hidden_size"]),
            dense_intermediate_size=int(data["intermediate_size"]),
            num_hidden_layers=int(data["num_hidden_layers"]),
            num_attention_heads=int(data["num_attention_heads"]),
            num_experts=int(data["n_routed_experts"]),
            num_experts_per_tok=int(data["num_experts_per_tok"]),
            moe_intermediate_size=int(data["moe_intermediate_size"]),
            first_dense_layers=int(data["first_k_dense_replace"]),
            q_lora_rank=int(data["q_lora_rank"]),
            kv_lora_rank=int(data["kv_lora_rank"]),
            qk_nope_head_dim=int(data["qk_nope_head_dim"]),
            qk_rope_head_dim=int(data["qk_rope_head_dim"]),
            v_head_dim=int(data["v_head_dim"]),
            shared_experts=int(data.get("n_shared_experts", 0)),
            vocab_size=int(data["vocab_size"]),
            max_position_embeddings=int(max_context or data.get("max_position_embeddings", 4096)),
            rope_theta=int(float(rope.get("rope_theta", data.get("rope_theta", 10000)))),
            eos_token_id=stop_ids[0] if stop_ids else int(data.get("eos_token_id", 0)),
            pad_token_id=int(data.get("pad_token_id", 0)),
            expert_groups=int(data.get("n_group", 1)),
            topk_groups=int(data.get("topk_group", 1)),
            normalize_topk=bool(data.get("norm_topk_prob", False)),
            rms_norm_epsilon=float(data.get("rms_norm_eps", 1.0e-5)),
            routed_scale=float(data.get("routed_scaling_factor", 1.0)),
            stop_token_ids=tuple(stop_ids[:8]),
            quant_group=quant_group,
        )
        config.validate()
        return config

    def validate(self) -> None:
        checks = {
            "hidden_size": (self.hidden_size, 1, 1 << 20),
            "num_hidden_layers": (self.num_hidden_layers, 1, 128),
            "num_attention_heads": (self.num_attention_heads, 1, 1024),
            "n_routed_experts": (self.num_experts, 1, 4096),
            "num_experts_per_tok": (self.num_experts_per_tok, 1, 64),
            "moe_intermediate_size": (self.moe_intermediate_size, 1, 1 << 20),
            "intermediate_size": (self.dense_intermediate_size, 1, 1 << 24),
            "first_k_dense_replace": (self.first_dense_layers, 0, self.num_hidden_layers),
            "q_lora_rank": (self.q_lora_rank, 0, 1 << 20),
            "kv_lora_rank": (self.kv_lora_rank, 1, 1 << 20),
            "qk_nope_head_dim": (self.qk_nope_head_dim, 1, 1 << 16),
            "qk_rope_head_dim": (self.qk_rope_head_dim, 1, 1 << 16),
            "v_head_dim": (self.v_head_dim, 1, 1 << 16),
            "n_shared_experts": (self.shared_experts, 0, 64),
            "vocab_size": (self.vocab_size, 1, 1 << 24),
        }
        for name, (value, low, high) in checks.items():
            if value < low or value > high:
                raise ValueError(f"{name}={value} is outside [{low},{high}]")
        if self.expert_groups != 1:
            raise ValueError("GLM-5.2 exporter currently supports n_group=1")
        if self.num_experts_per_tok > self.num_experts:
            raise ValueError("num_experts_per_tok exceeds n_routed_experts")
        if self.quant_group < 2 or self.quant_group % 2:
            raise ValueError("group size must be even")

    def config_entries(self) -> dict[int, tuple[int, object]]:
        return {
            CFG_STOP_TOKEN_IDS: (TYPE_U32_ARRAY, self.stop_token_ids or (self.eos_token_id,)),
            CFG_RMS_NORM_EPS: (TYPE_F32, self.rms_norm_epsilon),
            CFG_ATTENTION_SCALE: (TYPE_F32, self.attention_scale),
            CFG_ROUTED_SCALE: (TYPE_F32, self.routed_scale),
            CFG_MOE_INTERMEDIATE_SIZE: (TYPE_U32, self.moe_intermediate_size),
            CFG_FIRST_DENSE_LAYERS: (TYPE_U32, self.first_dense_layers),
            CFG_Q_LORA_RANK: (TYPE_U32, self.q_lora_rank),
            CFG_KV_LORA_RANK: (TYPE_U32, self.kv_lora_rank),
            CFG_QK_NOPE_HEAD_DIM: (TYPE_U32, self.qk_nope_head_dim),
            CFG_QK_ROPE_HEAD_DIM: (TYPE_U32, self.qk_rope_head_dim),
            CFG_V_HEAD_DIM: (TYPE_U32, self.v_head_dim),
            CFG_SHARED_EXPERTS: (TYPE_U32, self.shared_experts),
            CFG_EXPERT_GROUPS: (TYPE_U32, self.expert_groups),
            CFG_TOPK_GROUPS: (TYPE_U32, self.topk_groups),
            CFG_NORMALIZE_TOPK: (TYPE_BOOL, self.normalize_topk),
        }


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


def _read_optional_json(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _as_ids(value: object) -> list[int]:
    if value is None:
        return []
    if isinstance(value, list):
        return [int(item) for item in value]
    return [int(value)]


def _merged_stop_ids(*values: object) -> list[int]:
    result: list[int] = []
    for value in values:
        for item in _as_ids(value):
            if item not in result and len(result) < 8:
                result.append(item)
    return result


def align_up(value: int, alignment: int = ALIGNMENT) -> int:
    return (value + alignment - 1) // alignment * alignment


def layer_base(layer: int) -> int:
    return 1000 + layer * 10000


def scale_id(tensor_id: int) -> int:
    return tensor_id + SCALE_ID_OFFSET


def dense_gate_id(layer: int) -> int:
    return layer_base(layer) + 20


def dense_up_id(layer: int) -> int:
    return layer_base(layer) + 21


def dense_down_id(layer: int) -> int:
    return layer_base(layer) + 22


def sparse_router_id(layer: int) -> int:
    return layer_base(layer) + 30


def sparse_router_bias_id(layer: int) -> int:
    return layer_base(layer) + 31


def shared_gate_id(layer: int) -> int:
    return layer_base(layer) + 40


def shared_up_id(layer: int) -> int:
    return layer_base(layer) + 41


def shared_down_id(layer: int) -> int:
    return layer_base(layer) + 42


def expert_base(layer: int, expert: int) -> int:
    return layer_base(layer) + 1000 + expert * 10


def expert_gate_id(layer: int, expert: int) -> int:
    return expert_base(layer, expert) + 1


def expert_up_id(layer: int, expert: int) -> int:
    return expert_base(layer, expert) + 2


def expert_down_id(layer: int, expert: int) -> int:
    return expert_base(layer, expert) + 3


def expected_logical_tensors(config: Glm52Config) -> list[LogicalTensor]:
    h = config.hidden_size
    layers = [
        LogicalTensor(1, "model.embed_tokens.weight", "tok_emb", (config.vocab_size, h)),
        LogicalTensor(2, "model.norm.weight", "norm", (h,)),
        LogicalTensor(3, "lm_head.weight", "lm_head", (config.vocab_size, h)),
    ]
    for layer in range(config.num_hidden_layers):
        base = layer_base(layer)
        prefix = f"model.layers.{layer}"
        tag = f"l{layer:02d}"
        layers.extend(
            [
                LogicalTensor(base + 1, f"{prefix}.input_layernorm.weight", f"{tag}.in_norm", (h,)),
                LogicalTensor(base + 2, f"{prefix}.post_attention_layernorm.weight", f"{tag}.postnorm", (h,)),
                LogicalTensor(base + 10, f"{prefix}.self_attn.q_a_proj.weight", f"{tag}.qa", (config.q_lora_rank, h)),
                LogicalTensor(base + 11, f"{prefix}.self_attn.q_a_layernorm.weight", f"{tag}.qanorm", (config.q_lora_rank,)),
                LogicalTensor(
                    base + 12,
                    f"{prefix}.self_attn.q_b_proj.weight",
                    f"{tag}.qb",
                    (config.num_attention_heads * config.qk_head_dim, config.q_lora_rank),
                ),
                LogicalTensor(
                    base + 13,
                    f"{prefix}.self_attn.kv_a_proj_with_mqa.weight",
                    f"{tag}.kva",
                    (config.kv_lora_rank + config.qk_rope_head_dim, h),
                ),
                LogicalTensor(base + 14, f"{prefix}.self_attn.kv_a_layernorm.weight", f"{tag}.kvanorm", (config.kv_lora_rank,)),
                LogicalTensor(
                    base + 15,
                    f"{prefix}.self_attn.kv_b_proj.weight",
                    f"{tag}.kvb",
                    (config.num_attention_heads * (config.qk_nope_head_dim + config.v_head_dim), config.kv_lora_rank),
                ),
                LogicalTensor(
                    base + 16,
                    f"{prefix}.self_attn.o_proj.weight",
                    f"{tag}.o",
                    (h, config.num_attention_heads * config.v_head_dim),
                ),
            ]
        )
        if layer < config.first_dense_layers:
            layers.extend(
                [
                    LogicalTensor(dense_gate_id(layer), f"{prefix}.mlp.gate_proj.weight", f"{tag}.dgate", (config.dense_intermediate_size, h)),
                    LogicalTensor(dense_up_id(layer), f"{prefix}.mlp.up_proj.weight", f"{tag}.dup", (config.dense_intermediate_size, h)),
                    LogicalTensor(dense_down_id(layer), f"{prefix}.mlp.down_proj.weight", f"{tag}.ddown", (h, config.dense_intermediate_size)),
                ]
            )
        else:
            shared_i = config.moe_intermediate_size * config.shared_experts
            layers.extend(
                [
                    LogicalTensor(sparse_router_id(layer), f"{prefix}.mlp.gate.weight", f"{tag}.router", (config.num_experts, h)),
                    LogicalTensor(sparse_router_bias_id(layer), f"{prefix}.mlp.gate.e_score_correction_bias", f"{tag}.rbias", (config.num_experts,)),
                    LogicalTensor(shared_gate_id(layer), f"{prefix}.mlp.shared_experts.gate_proj.weight", f"{tag}.shgate", (shared_i, h)),
                    LogicalTensor(shared_up_id(layer), f"{prefix}.mlp.shared_experts.up_proj.weight", f"{tag}.shup", (shared_i, h)),
                    LogicalTensor(shared_down_id(layer), f"{prefix}.mlp.shared_experts.down_proj.weight", f"{tag}.shdown", (h, shared_i)),
                ]
            )
            for expert in range(config.num_experts):
                eprefix = f"{prefix}.mlp.experts.{expert}"
                etag = f"{tag}.e{expert:03d}"
                layers.extend(
                    [
                        LogicalTensor(expert_gate_id(layer, expert), f"{eprefix}.gate_proj.weight", f"{etag}.gate", (config.moe_intermediate_size, h)),
                        LogicalTensor(expert_up_id(layer, expert), f"{eprefix}.up_proj.weight", f"{etag}.up", (config.moe_intermediate_size, h)),
                        LogicalTensor(expert_down_id(layer, expert), f"{eprefix}.down_proj.weight", f"{etag}.down", (h, config.moe_intermediate_size)),
                    ]
                )
    return layers


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


def q4_writers(source: TensorSource, tensor: LogicalTensor, group_size: int) -> tuple[Callable[[object], None], Callable[[object], None]]:
    rows = tensor.shape[0]

    def write_weights(output: object) -> None:
        for row_index in range(rows):
            packed, _ = quantize_row(source.row_floats(tensor.hf_name, row_index), group_size)
            output.write(packed)

    def write_scales(output: object) -> None:
        for row_index in range(rows):
            _, scales = quantize_row(source.row_floats(tensor.hf_name, row_index), group_size)
            output.write(scales)

    return write_weights, write_scales


def physical_tensors(source: TensorSource, config: Glm52Config) -> list[PhysicalTensor]:
    result: list[PhysicalTensor] = []
    for logical in expected_logical_tensors(config):
        actual_shape = source.shape(logical.hf_name)
        if actual_shape != logical.shape:
            raise ValueError(f"{logical.hf_name}: expected {logical.shape}, found {actual_shape}")
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
                    logical.hf_name,
                    dense_writer(source, logical),
                )
            )
        elif len(logical.shape) == 2:
            rows, columns = logical.shape
            if columns % config.quant_group:
                raise ValueError(f"{logical.hf_name}: group size {config.quant_group} does not divide {columns}")
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
                    logical.hf_name,
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
                    logical.hf_name + "#scales",
                    write_scales,
                )
            )
        else:
            raise ValueError(f"unsupported tensor rank for {logical.hf_name}")
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


def pack_header(config: Glm52Config, tensor_count: int, directory_offset: int) -> bytes:
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
        ARCH_GLM52,
        0,
        config.hidden_size,
        config.dense_intermediate_size,
        config.num_hidden_layers,
        config.num_attention_heads,
        config.num_attention_heads,
        config.num_experts,
        config.num_experts_per_tok,
        config.vocab_size,
        config.max_position_embeddings,
        config.rope_theta,
        config.eos_token_id,
        config.pad_token_id,
        config.quant_group,
        MANIFEST_TENSOR_ID,
    )
    return bytes(header)


def plan_bmoq(source: TensorSource, config: Glm52Config) -> tuple[list[PhysicalTensor], bytes, bytes]:
    config_tensor = encode_config(config.config_entries())
    tensors = physical_tensors(source, config)
    manifest_body = {
        "format": "BMOQ",
        "version": VERSION,
        "architecture": "glm52",
        "config": config.__dict__ | {"stop_token_ids": list(config.stop_token_ids), "qk_head_dim": config.qk_head_dim},
        "quantization": {"type": "grouped_symmetric_q4", "group_size": config.quant_group},
        "tensor_count_without_metadata": len(tensors),
        "config_tensor_id": CONFIG_TENSOR_ID,
        "manifest_tensor_id": MANIFEST_TENSOR_ID,
        "scale_id_offset": SCALE_ID_OFFSET,
        "tensors": [
            {
                "id": tensor.tensor_id,
                "name": tensor.hf_name,
                "short_name": tensor.short_name,
                "dtype": tensor.dtype,
                "layout": tensor.layout,
                "shape": list(tensor.dimensions),
                "bytes": tensor.byte_length,
            }
            for tensor in tensors
        ],
    }
    manifest_bytes = json.dumps(manifest_body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return tensors, config_tensor, manifest_bytes


def write_bmoq(source: TensorSource, output_path: Path, config: Glm52Config) -> dict:
    tensors, config_tensor, manifest_bytes = plan_bmoq(source, config)
    directory_offset = HEADER_BYTES
    tensor_count = len(tensors) + 2
    offset = align_up(directory_offset + tensor_count * ENTRY_BYTES)
    for tensor in tensors:
        tensor.data_offset = offset
        offset = align_up(offset + tensor.byte_length)
    config_physical = PhysicalTensor(
        CONFIG_TENSOR_ID,
        DTYPE_OPAQUE,
        0,
        (len(config_tensor), 1, 1, 1),
        len(config_tensor),
        LAYOUT_OPAQUE,
        "bmoq_cfg",
        "bmoq.config.bin",
        lambda output: output.write(config_tensor),
        offset,
    )
    offset = align_up(offset + len(config_tensor))
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
    tensors.extend([config_physical, manifest])

    with output_path.open("wb") as output:
        output.write(pack_header(config, len(tensors), directory_offset))
        for tensor in tensors:
            output.write(pack_entry(tensor))
        for tensor in tensors:
            output.seek(tensor.data_offset)
            tensor.writer(output)
    return {
        "tensor_count": len(tensors),
        "bytes": output_path.stat().st_size,
        "config_bytes": len(config_tensor),
        "manifest_bytes": len(manifest_bytes),
    }


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

    def _tensor_meta(self, name: str) -> tuple[Path, dict, int]:
        try:
            path = self.index[name]
        except KeyError as exc:
            raise KeyError(f"missing tensor {name}") from exc
        with path.open("rb") as file:
            header_len = struct.unpack("<Q", file.read(8))[0]
            header = json.loads(file.read(header_len).decode("utf-8"))
        return path, header[name], 8 + header_len

    def shape(self, name: str) -> tuple[int, ...]:
        _, meta, _ = self._tensor_meta(name)
        return tuple(int(v) for v in meta["shape"])

    def row_floats(self, name: str, row: int) -> list[float]:
        shape = self.shape(name)
        if len(shape) != 2:
            raise ValueError(f"{name} is not a matrix")
        rows, columns = shape
        if row < 0 or row >= rows:
            raise IndexError(row)
        return self._read_floats(name, row * columns, columns)

    def values_floats(self, name: str) -> list[float]:
        shape = self.shape(name)
        if len(shape) != 1:
            raise ValueError(f"{name} is not a vector")
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


def inspect_summary(source: TensorSource, config: Glm52Config) -> dict:
    tensors, config_tensor, manifest_bytes = plan_bmoq(source, config)
    return {
        "architecture": "glm52",
        "config": config.__dict__ | {"stop_token_ids": list(config.stop_token_ids)},
        "bmoq_tensor_count": len(tensors) + 2,
        "config_bytes": len(config_tensor),
        "manifest_bytes": len(manifest_bytes),
        "estimated_bmoq_bytes": align_up(HEADER_BYTES + (len(tensors) + 2) * ENTRY_BYTES)
        + sum(align_up(t.byte_length) for t in tensors)
        + align_up(len(config_tensor))
        + align_up(len(manifest_bytes)),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("model_dir", type=Path)
    parser.add_argument("output", type=Path, nargs="?")
    parser.add_argument("--group-size", type=int, default=32)
    parser.add_argument("--max-context", type=int)
    parser.add_argument("--inspect-only", action="store_true")
    args = parser.parse_args()

    config = Glm52Config.from_json(args.model_dir, args.group_size, args.max_context)
    source = SafetensorSource(args.model_dir)
    if args.inspect_only:
        print(json.dumps(inspect_summary(source, config), sort_keys=True, indent=2))
        return
    if args.output is None:
        raise SystemExit("output path is required unless --inspect-only is used")
    summary = write_bmoq(source, args.output, config)
    print(
        f"{args.output}: tensors={summary['tensor_count']} "
        f"config={summary['config_bytes']} manifest={summary['manifest_bytes']} "
        f"bytes total={summary['bytes']}"
    )


if __name__ == "__main__":
    main()
