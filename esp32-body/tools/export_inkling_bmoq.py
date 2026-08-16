#!/usr/bin/env python3
"""Convert a local Inkling text checkpoint into streamed BMOQ v2.

The converter follows the upstream text-only `c/inkling.c` tensor contract:
local/global GQA projections, learned relative-bias banks, q/k norms, four
short-conv filters, dense MLP layers, and sparse MoE layers with fused routed
expert tensors. Safetensors shards are opened tensor-by-tensor and matrices are
quantized row-by-row into grouped symmetric Q4 plus float32 scales.
"""

from __future__ import annotations

import argparse
import json
import math
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

from bmoq_config import TYPE_F32, TYPE_U32, TYPE_U32_ARRAY, encode_config

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
LAYOUT_Q4_EXPERT_BUNDLE = 4
LAYOUT_EXPERT_GROUP_SCALES_F32 = 5

ARCH_INKLING = 4
CONFIG_TENSOR_ID = 0x32474643
MANIFEST_TENSOR_ID = 0x494E4B4C
SCALE_ID_OFFSET = 0x00800000

CFG_STOP_TOKEN_IDS = 1
CFG_RMS_NORM_EPS = 2
CFG_ROUTE_SCALE = 4
CFG_MOE_INTERMEDIATE_SIZE = 1000
CFG_SLIDING_WINDOW_SIZE = 2000
CFG_UNPADDED_VOCAB_SIZE = 5000
CFG_SWA_ATTENTION_HEADS = 5001
CFG_SWA_KEY_VALUE_HEADS = 5002
CFG_SWA_HEAD_DIM = 5003
CFG_HEAD_DIM = 5004
CFG_D_REL = 5005
CFG_REL_EXTENT = 5006
CFG_SCONV_KERNEL_SIZE = 5007
CFG_LOG_SCALING_N_FLOOR = 5008
CFG_LOG_SCALING_ALPHA = 5009
CFG_DENSE_MLP_IDX = 5010
CFG_SHARED_EXPERTS = 5011
CFG_LOGITS_MUP_WIDTH_MULTIPLIER = 5012
CFG_DENSE_INTERMEDIATE_SIZE = 5013
CFG_LOCAL_LAYER_IDS = 5014
CFG_SPARSE_LAYER_IDS = 5015


@dataclass(frozen=True)
class InklingConfig:
    hidden_size: int
    dense_intermediate_size: int
    moe_intermediate_size: int
    num_hidden_layers: int
    num_attention_heads: int
    num_key_value_heads: int
    head_dim: int
    swa_num_attention_heads: int
    swa_num_key_value_heads: int
    swa_head_dim: int
    sliding_window_size: int
    d_rel: int
    rel_extent: int
    sconv_kernel_size: int
    n_routed_experts: int
    num_experts_per_tok: int
    n_shared_experts: int
    dense_mlp_idx: int
    vocab_size: int
    unpadded_vocab_size: int
    max_position_embeddings: int
    eos_token_id: int
    pad_token_id: int
    rms_norm_epsilon: float
    route_scale: float
    logits_mup_width_multiplier: float
    log_scaling_n_floor: int
    log_scaling_alpha: float
    local_layer_ids: tuple[int, ...]
    sparse_layer_ids: tuple[int, ...]
    stop_token_ids: tuple[int, ...]
    quant_group: int

    @classmethod
    def from_json(cls, model_dir: Path, quant_group: int, max_context: int | None = None) -> "InklingConfig":
        root = json.loads((model_dir / "config.json").read_text(encoding="utf-8"))
        data = root.get("text_config") or root
        generation = _read_optional_json(model_dir / "generation_config.json")
        hidden = int(data.get("hidden_size", 6144))
        dense_i, moe_i = _mlp_widths(data)
        layers = int(data.get("num_hidden_layers", 66))
        local_ids = _local_layer_ids(data, layers)
        sparse_ids = _sparse_layer_ids(data, layers)
        stop_ids = _merged_stop_ids(root.get("eos_token_id"), data.get("eos_token_id"), generation.get("eos_token_id"))
        eos = stop_ids[0] if stop_ids else _nullable_u32(root.get("eos_token_id", data.get("eos_token_id")), 0xFFFFFFFF)
        config = cls(
            hidden_size=hidden,
            dense_intermediate_size=dense_i,
            moe_intermediate_size=moe_i,
            num_hidden_layers=layers,
            num_attention_heads=int(data.get("num_attention_heads", 64)),
            num_key_value_heads=int(data.get("num_key_value_heads", 8)),
            head_dim=int(data.get("head_dim", 128)),
            swa_num_attention_heads=int(data.get("swa_num_attention_heads", data.get("num_attention_heads", 64))),
            swa_num_key_value_heads=int(data.get("swa_num_key_value_heads", 16)),
            swa_head_dim=int(data.get("swa_head_dim", data.get("head_dim", 128))),
            sliding_window_size=int(data.get("sliding_window_size", 512)),
            d_rel=int(data.get("d_rel", 16)),
            rel_extent=int(data.get("rel_extent", 1024)),
            sconv_kernel_size=int(data.get("sconv_kernel_size", data.get("conv_kernel_size", 4))),
            n_routed_experts=int(data.get("n_routed_experts", 256)),
            num_experts_per_tok=int(data.get("num_experts_per_tok", 6)),
            n_shared_experts=int(data.get("n_shared_experts", 2)),
            dense_mlp_idx=int(data.get("dense_mlp_idx", 0)),
            vocab_size=int(data.get("vocab_size", 201024)),
            unpadded_vocab_size=int(data.get("unpadded_vocab_size", data.get("vocab_size", 201024))),
            max_position_embeddings=_read_max_context(root, data, max_context),
            eos_token_id=eos,
            pad_token_id=int(data.get("pad_token_id", root.get("pad_token_id", 0))),
            rms_norm_epsilon=float(data.get("rms_norm_eps", 1.0e-6)),
            route_scale=float(data.get("route_scale", 8.0)),
            logits_mup_width_multiplier=float(data.get("logits_mup_width_multiplier", 24.0)),
            log_scaling_n_floor=int(float(data.get("log_scaling_n_floor", 0))),
            log_scaling_alpha=float(data.get("log_scaling_alpha", 0.1)),
            local_layer_ids=local_ids,
            sparse_layer_ids=sparse_ids,
            stop_token_ids=tuple(stop_ids[:8]),
            quant_group=quant_group,
        )
        config.validate()
        return config

    def validate(self) -> None:
        checks = {
            "hidden_size": (self.hidden_size, 1, 1 << 20),
            "num_hidden_layers": (self.num_hidden_layers, 1, 256),
            "num_attention_heads": (self.num_attention_heads, 1, 4096),
            "num_key_value_heads": (self.num_key_value_heads, 1, 4096),
            "head_dim": (self.head_dim, 1, 4096),
            "swa_num_attention_heads": (self.swa_num_attention_heads, 1, 4096),
            "swa_num_key_value_heads": (self.swa_num_key_value_heads, 1, 4096),
            "swa_head_dim": (self.swa_head_dim, 1, 4096),
            "d_rel": (self.d_rel, 1, 4096),
            "rel_extent": (self.rel_extent, 1, 1 << 20),
            "sconv_kernel_size": (self.sconv_kernel_size, 1, 64),
            "n_routed_experts": (self.n_routed_experts, 1, 4096),
            "num_experts_per_tok": (self.num_experts_per_tok, 1, 64),
            "n_shared_experts": (self.n_shared_experts, 0, 256),
            "vocab_size": (self.vocab_size, 1, 1 << 24),
            "max_position_embeddings": (self.max_position_embeddings, 1, 0xFFFFFFFF),
        }
        for name, (value, low, high) in checks.items():
            if value < low or value > high:
                raise ValueError(f"{name}={value} is outside [{low},{high}]")
        if self.num_attention_heads % self.num_key_value_heads:
            raise ValueError("num_attention_heads must divide num_key_value_heads for GQA")
        if self.swa_num_attention_heads % self.swa_num_key_value_heads:
            raise ValueError("swa_num_attention_heads must divide swa_num_key_value_heads for GQA")
        if self.num_experts_per_tok > self.n_routed_experts:
            raise ValueError("num_experts_per_tok exceeds n_routed_experts")
        if self.quant_group < 2 or self.quant_group % 2:
            raise ValueError("group size must be even")

    def is_local(self, layer: int) -> bool:
        return layer in self.local_layer_ids

    def is_sparse(self, layer: int) -> bool:
        return layer in self.sparse_layer_ids

    def layer_heads(self, layer: int) -> int:
        return self.swa_num_attention_heads if self.is_local(layer) else self.num_attention_heads

    def layer_kv_heads(self, layer: int) -> int:
        return self.swa_num_key_value_heads if self.is_local(layer) else self.num_key_value_heads

    def layer_head_dim(self, layer: int) -> int:
        return self.swa_head_dim if self.is_local(layer) else self.head_dim

    def layer_rel_extent(self, layer: int) -> int:
        return self.sliding_window_size if self.is_local(layer) else self.rel_extent

    def config_entries(self) -> dict[int, tuple[int, object]]:
        entries = {
            CFG_STOP_TOKEN_IDS: (TYPE_U32_ARRAY, self.stop_token_ids or (self.eos_token_id,)),
            CFG_RMS_NORM_EPS: (TYPE_F32, self.rms_norm_epsilon),
            CFG_ROUTE_SCALE: (TYPE_F32, self.route_scale),
            CFG_LOG_SCALING_ALPHA: (TYPE_F32, self.log_scaling_alpha),
            CFG_LOGITS_MUP_WIDTH_MULTIPLIER: (TYPE_F32, self.logits_mup_width_multiplier),
            CFG_UNPADDED_VOCAB_SIZE: (TYPE_U32, self.unpadded_vocab_size),
            CFG_HEAD_DIM: (TYPE_U32, self.head_dim),
            CFG_SWA_ATTENTION_HEADS: (TYPE_U32, self.swa_num_attention_heads),
            CFG_SWA_KEY_VALUE_HEADS: (TYPE_U32, self.swa_num_key_value_heads),
            CFG_SWA_HEAD_DIM: (TYPE_U32, self.swa_head_dim),
            CFG_SLIDING_WINDOW_SIZE: (TYPE_U32, self.sliding_window_size),
            CFG_D_REL: (TYPE_U32, self.d_rel),
            CFG_REL_EXTENT: (TYPE_U32, self.rel_extent),
            CFG_LOG_SCALING_N_FLOOR: (TYPE_U32, self.log_scaling_n_floor),
            CFG_SCONV_KERNEL_SIZE: (TYPE_U32, self.sconv_kernel_size),
            CFG_MOE_INTERMEDIATE_SIZE: (TYPE_U32, self.moe_intermediate_size),
            CFG_DENSE_INTERMEDIATE_SIZE: (TYPE_U32, self.dense_intermediate_size),
            CFG_DENSE_MLP_IDX: (TYPE_U32, self.dense_mlp_idx),
            CFG_SHARED_EXPERTS: (TYPE_U32, self.n_shared_experts),
        }
        if self.local_layer_ids:
            entries[CFG_LOCAL_LAYER_IDS] = (TYPE_U32_ARRAY, self.local_layer_ids)
        if self.sparse_layer_ids:
            entries[CFG_SPARSE_LAYER_IDS] = (TYPE_U32_ARRAY, self.sparse_layer_ids)
        return entries


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
        return [int(item) for item in value if item is not None]
    return [int(value)]


def _merged_stop_ids(*values: object) -> list[int]:
    result: list[int] = []
    for value in values:
        for item in _as_ids(value):
            if item not in result and len(result) < 8:
                result.append(item)
    return result


def _nullable_u32(value: object, default: int) -> int:
    ids = _as_ids(value)
    return ids[0] if ids else default


def _read_max_context(root: dict, data: dict, override: int | None) -> int:
    value = override
    if value is None:
        value = data.get("max_position_embeddings", root.get("max_position_embeddings", 1_048_576))
    parsed = int(value)
    if parsed < 1 or parsed > 0xFFFFFFFF:
        raise ValueError(f"max_position_embeddings={parsed} is outside [1,{0xFFFFFFFF}]")
    return parsed


def _mlp_widths(data: dict) -> tuple[int, int]:
    if "dense_intermediate_size" in data:
        return int(data["dense_intermediate_size"]), int(data.get("intermediate_size", 3072))
    return int(data.get("intermediate_size", 24576)), int(data.get("moe_intermediate_size", 3072))


def _local_layer_ids(data: dict, layers: int) -> tuple[int, ...]:
    fallback = _fallback_local_layer_ids(data, layers)
    layer_types = data.get("layer_types")
    if isinstance(layer_types, list):
        result = []
        for layer in range(layers):
            value = layer_types[layer] if layer < len(layer_types) else None
            if value == "hybrid_sliding":
                result.append(layer)
            elif value == "hybrid":
                continue
            elif layer in fallback:
                result.append(layer)
        return tuple(result)
    return tuple(sorted(fallback))


def _fallback_local_layer_ids(data: dict, layers: int) -> set[int]:
    ids = data.get("local_layer_ids")
    if isinstance(ids, list):
        return {int(item) for item in ids if 0 <= int(item) < layers}
    return {i for i in range(layers) if (i + 1) % 6 != 0}


def _sparse_layer_ids(data: dict, layers: int) -> tuple[int, ...]:
    dense_idx = int(data.get("dense_mlp_idx", 0))
    layer_types = data.get("mlp_layer_types")
    if isinstance(layer_types, list):
        result = []
        for layer in range(layers):
            value = layer_types[layer] if layer < len(layer_types) else None
            if value == "sparse":
                result.append(layer)
            elif value == "dense":
                continue
            elif layer >= dense_idx:
                result.append(layer)
        return tuple(result)
    return tuple(i for i in range(layers) if i >= dense_idx)


def align_up(value: int, alignment: int = ALIGNMENT) -> int:
    return (value + alignment - 1) // alignment * alignment


def layer_base(layer: int) -> int:
    return 1000 + layer * 10000


def scale_id(tensor_id: int) -> int:
    return tensor_id + SCALE_ID_OFFSET


def dense_gate_id(layer: int) -> int:
    return layer_base(layer) + 40


def sparse_router_id(layer: int) -> int:
    return layer_base(layer) + 50


def routed_gate_id(layer: int) -> int:
    return layer_base(layer) + 70


def routed_up_id(layer: int) -> int:
    return layer_base(layer) + 71


def routed_down_id(layer: int) -> int:
    return layer_base(layer) + 72


def expected_logical_tensors(config: InklingConfig) -> list[LogicalTensor]:
    h = config.hidden_size
    tensors = [
        LogicalTensor(1, "model.embed_tokens.weight", "tok_emb", (config.vocab_size, h)),
        LogicalTensor(2, "model.embed_norm.weight", "emb_norm", (h,)),
        LogicalTensor(3, "model.norm.weight", "norm", (h,)),
        LogicalTensor(4, "lm_head.weight", "lm_head", (config.vocab_size, h)),
    ]
    for layer in range(config.num_hidden_layers):
        base = layer_base(layer)
        prefix = f"model.layers.{layer}"
        tag = f"l{layer:02d}"
        heads = config.layer_heads(layer)
        kv_heads = config.layer_kv_heads(layer)
        hd = config.layer_head_dim(layer)
        qdim = heads * hd
        kvdim = kv_heads * hd
        rel_extent = config.layer_rel_extent(layer)
        tensors.extend(
            [
                LogicalTensor(base + 1, f"{prefix}.input_layernorm.weight", f"{tag}.in_norm", (h,)),
                LogicalTensor(base + 2, f"{prefix}.post_attention_layernorm.weight", f"{tag}.postnorm", (h,)),
                LogicalTensor(base + 10, f"{prefix}.self_attn.q_proj.weight", f"{tag}.q", (qdim, h)),
                LogicalTensor(base + 11, f"{prefix}.self_attn.k_proj.weight", f"{tag}.k", (kvdim, h)),
                LogicalTensor(base + 12, f"{prefix}.self_attn.v_proj.weight", f"{tag}.v", (kvdim, h)),
                LogicalTensor(base + 13, f"{prefix}.self_attn.r_proj.weight", f"{tag}.r", (heads * config.d_rel, h)),
                LogicalTensor(base + 14, f"{prefix}.self_attn.o_proj.weight", f"{tag}.o", (h, qdim)),
                LogicalTensor(base + 15, f"{prefix}.self_attn.q_norm.weight", f"{tag}.qn", (hd,)),
                LogicalTensor(base + 16, f"{prefix}.self_attn.k_norm.weight", f"{tag}.kn", (hd,)),
                LogicalTensor(base + 17, f"{prefix}.self_attn.rel_logits_proj.proj", f"{tag}.relp", (config.d_rel, rel_extent)),
                LogicalTensor(base + 18, f"{prefix}.self_attn.k_sconv.conv1d.weight", f"{tag}.kconv", (kvdim, 1, config.sconv_kernel_size)),
                LogicalTensor(base + 19, f"{prefix}.self_attn.v_sconv.conv1d.weight", f"{tag}.vconv", (kvdim, 1, config.sconv_kernel_size)),
                LogicalTensor(base + 20, f"{prefix}.attn_sconv.conv1d.weight", f"{tag}.aconv", (h, 1, config.sconv_kernel_size)),
                LogicalTensor(base + 21, f"{prefix}.mlp_sconv.conv1d.weight", f"{tag}.mconv", (h, 1, config.sconv_kernel_size)),
            ]
        )
        if not config.is_sparse(layer):
            tensors.extend(
                [
                    LogicalTensor(dense_gate_id(layer), f"{prefix}.mlp.gate_proj.weight", f"{tag}.dgate", (config.dense_intermediate_size, h)),
                    LogicalTensor(dense_gate_id(layer) + 1, f"{prefix}.mlp.up_proj.weight", f"{tag}.dup", (config.dense_intermediate_size, h)),
                    LogicalTensor(dense_gate_id(layer) + 2, f"{prefix}.mlp.down_proj.weight", f"{tag}.ddown", (h, config.dense_intermediate_size)),
                    LogicalTensor(dense_gate_id(layer) + 3, f"{prefix}.mlp.global_scale", f"{tag}.dscale", (1,)),
                ]
            )
        else:
            tensors.extend(
                [
                    LogicalTensor(sparse_router_id(layer), f"{prefix}.mlp.gate.weight", f"{tag}.router", (config.n_routed_experts + config.n_shared_experts, h)),
                    LogicalTensor(sparse_router_id(layer) + 1, f"{prefix}.mlp.gate.e_score_correction_bias", f"{tag}.rbias", (config.n_routed_experts,)),
                    LogicalTensor(sparse_router_id(layer) + 2, f"{prefix}.mlp.gate.global_scale", f"{tag}.rscale", (1,)),
                    LogicalTensor(sparse_router_id(layer) + 10, f"{prefix}.mlp.shared_experts.gate_proj", f"{tag}.shgate", (config.n_shared_experts, config.moe_intermediate_size, h)),
                    LogicalTensor(sparse_router_id(layer) + 11, f"{prefix}.mlp.shared_experts.up_proj", f"{tag}.shup", (config.n_shared_experts, config.moe_intermediate_size, h)),
                    LogicalTensor(sparse_router_id(layer) + 12, f"{prefix}.mlp.shared_experts.down_proj", f"{tag}.shdown", (config.n_shared_experts, h, config.moe_intermediate_size)),
                    LogicalTensor(routed_gate_id(layer), f"{prefix}.mlp.experts.gate_up_proj", f"{tag}.egup", (config.n_routed_experts, 2 * config.moe_intermediate_size, h)),
                    LogicalTensor(routed_down_id(layer), f"{prefix}.mlp.experts.down_proj", f"{tag}.edown", (config.n_routed_experts, h, config.moe_intermediate_size)),
                ]
            )
    return tensors


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


def _flat_rows(shape: tuple[int, ...]) -> int:
    return math.prod(shape[:-1])


def _numel(shape: tuple[int, ...]) -> int:
    return math.prod(shape)


def dense_writer(source: TensorSource, tensor: LogicalTensor) -> Callable[[object], None]:
    def write(output: object) -> None:
        for value in source.values_floats(tensor.hf_name):
            output.write(struct.pack("<f", value))

    return write


def q4_writers(source: TensorSource, tensor: LogicalTensor, group_size: int) -> tuple[Callable[[object], None], Callable[[object], None]]:
    rows = _flat_rows(tensor.shape)

    def write_weights(output: object) -> None:
        for row_index in range(rows):
            packed, _ = quantize_row(source.row_floats(tensor.hf_name, row_index), group_size)
            output.write(packed)

    def write_scales(output: object) -> None:
        for row_index in range(rows):
            _, scales = quantize_row(source.row_floats(tensor.hf_name, row_index), group_size)
            output.write(scales)

    return write_weights, write_scales


def q4_sliced_bundle_writers(
    source: TensorSource,
    hf_name: str,
    experts: int,
    rows_per_expert: int,
    source_rows_per_expert: int,
    source_row_offset: int,
    group_size: int,
) -> tuple[Callable[[object], None], Callable[[object], None]]:
    def source_row(expert: int, row: int) -> int:
        return expert * source_rows_per_expert + source_row_offset + row

    def write_weights(output: object) -> None:
        for expert in range(experts):
            for row in range(rows_per_expert):
                packed, _ = quantize_row(source.row_floats(hf_name, source_row(expert, row)), group_size)
                output.write(packed)

    def write_scales(output: object) -> None:
        for expert in range(experts):
            for row in range(rows_per_expert):
                _, scales = quantize_row(source.row_floats(hf_name, source_row(expert, row)), group_size)
                output.write(scales)

    return write_weights, write_scales


def physical_tensors(source: TensorSource, config: InklingConfig) -> list[PhysicalTensor]:
    result: list[PhysicalTensor] = []
    for logical in expected_logical_tensors(config):
        actual_shape = source.shape(logical.hf_name)
        if actual_shape != logical.shape:
            raise ValueError(f"{logical.hf_name}: expected {logical.shape}, found {actual_shape}")
        if logical.hf_name.endswith(".mlp.experts.gate_up_proj"):
            _append_split_gate_up_bundle(result, source, logical, config)
            continue
        if _is_dense_f32_tensor(logical):
            result.append(
                PhysicalTensor(
                    logical.tensor_id,
                    DTYPE_F32,
                    0,
                    _physical_dimensions(logical.shape),
                    _numel(logical.shape) * 4,
                    LAYOUT_DENSE_F32,
                    logical.short_name,
                    logical.hf_name,
                    dense_writer(source, logical),
                )
            )
            continue
        rows = _flat_rows(logical.shape)
        columns = logical.shape[-1]
        if columns % config.quant_group:
            raise ValueError(f"{logical.hf_name}: group size {config.quant_group} does not divide {columns}")
        groups = columns // config.quant_group
        write_weights, write_scales = q4_writers(source, logical, config.quant_group)
        is_bundle = logical.hf_name.endswith(".mlp.experts.gate_up_proj") or logical.hf_name.endswith(".mlp.experts.down_proj")
        dimensions = _physical_dimensions(logical.shape)
        result.append(
            PhysicalTensor(
                logical.tensor_id,
                DTYPE_Q4_SYM,
                config.quant_group,
                dimensions,
                rows * columns // 2,
                LAYOUT_Q4_EXPERT_BUNDLE if is_bundle else LAYOUT_Q4_ROW_MAJOR,
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
                _scale_dimensions(logical.shape, groups),
                rows * groups * 4,
                LAYOUT_EXPERT_GROUP_SCALES_F32 if is_bundle else LAYOUT_GROUP_SCALES_F32,
                logical.short_name + ".s",
                logical.hf_name + "#scales",
                write_scales,
            )
        )
    return result


def _is_dense_f32_tensor(logical: LogicalTensor) -> bool:
    name = logical.hf_name
    return (
        len(logical.shape) == 1
        or name.endswith(".self_attn.q_norm.weight")
        or name.endswith(".self_attn.k_norm.weight")
        or name.endswith(".self_attn.rel_logits_proj.proj")
        or name.endswith(".self_attn.k_sconv.conv1d.weight")
        or name.endswith(".self_attn.v_sconv.conv1d.weight")
        or name.endswith(".attn_sconv.conv1d.weight")
        or name.endswith(".mlp_sconv.conv1d.weight")
        or name.endswith(".mlp.gate.weight")
        or name.endswith(".mlp.gate.e_score_correction_bias")
        or name.endswith(".mlp.gate.global_scale")
        or name.endswith(".mlp.global_scale")
    )


def _append_split_gate_up_bundle(
    result: list[PhysicalTensor],
    source: TensorSource,
    logical: LogicalTensor,
    config: InklingConfig,
) -> None:
    experts, fused_rows, columns = logical.shape
    rows = fused_rows // 2
    if fused_rows != 2 * config.moe_intermediate_size:
        raise ValueError(f"{logical.hf_name}: expected fused gate/up rows")
    if columns % config.quant_group:
        raise ValueError(f"{logical.hf_name}: group size {config.quant_group} does not divide {columns}")
    groups = columns // config.quant_group
    for tensor_id, short_name, name_suffix, row_offset in (
        (logical.tensor_id, logical.short_name.replace("egup", "egate"), "#gate", 0),
        (routed_up_id((logical.tensor_id - 1000) // 10000), logical.short_name.replace("egup", "eup"), "#up", rows),
    ):
        write_weights, write_scales = q4_sliced_bundle_writers(
            source,
            logical.hf_name,
            experts,
            rows,
            fused_rows,
            row_offset,
            config.quant_group,
        )
        result.append(
            PhysicalTensor(
                tensor_id,
                DTYPE_Q4_SYM,
                config.quant_group,
                (experts, rows, columns, 1),
                experts * rows * columns // 2,
                LAYOUT_Q4_EXPERT_BUNDLE,
                short_name,
                logical.hf_name + name_suffix,
                write_weights,
            )
        )
        result.append(
            PhysicalTensor(
                scale_id(tensor_id),
                DTYPE_F32,
                config.quant_group,
                (experts, rows, groups, 1),
                experts * rows * groups * 4,
                LAYOUT_EXPERT_GROUP_SCALES_F32,
                short_name + ".s",
                logical.hf_name + name_suffix + "#scales",
                write_scales,
            )
        )


def _physical_dimensions(shape: tuple[int, ...]) -> tuple[int, int, int, int]:
    if len(shape) == 1:
        return (shape[0], 1, 1, 1)
    if len(shape) == 2:
        return (shape[0], shape[1], 1, 1)
    if len(shape) == 3:
        return (shape[0], shape[1], shape[2], 1)
    raise ValueError(f"unsupported tensor rank {len(shape)}")


def _scale_dimensions(shape: tuple[int, ...], groups: int) -> tuple[int, int, int, int]:
    if len(shape) == 2:
        return (shape[0], groups, 1, 1)
    if len(shape) == 3:
        return (shape[0], shape[1], groups, 1)
    raise ValueError(f"unsupported tensor rank {len(shape)}")


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


def pack_header(config: InklingConfig, tensor_count: int, directory_offset: int) -> bytes:
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
        ARCH_INKLING,
        0,
        config.hidden_size,
        config.dense_intermediate_size,
        config.num_hidden_layers,
        config.num_attention_heads,
        config.num_key_value_heads,
        config.n_routed_experts,
        config.num_experts_per_tok,
        config.vocab_size,
        config.max_position_embeddings,
        0,
        config.eos_token_id,
        config.pad_token_id,
        config.quant_group,
        MANIFEST_TENSOR_ID,
    )
    return bytes(header)


def plan_bmoq(source: TensorSource, config: InklingConfig) -> tuple[list[PhysicalTensor], bytes, bytes]:
    config_tensor = encode_config(config.config_entries())
    tensors = physical_tensors(source, config)
    manifest_body = {
        "format": "BMOQ",
        "version": VERSION,
        "architecture": "inkling",
        "config": config.__dict__ | {
            "local_layer_ids": list(config.local_layer_ids),
            "sparse_layer_ids": list(config.sparse_layer_ids),
            "stop_token_ids": list(config.stop_token_ids),
        },
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


def write_bmoq(source: TensorSource, output_path: Path, config: InklingConfig) -> dict:
    tensors, config_tensor, manifest_bytes = plan_bmoq(source, config)
    tensor_count = len(tensors) + 2
    directory_offset = HEADER_BYTES
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
        return tuple(int(value) for value in meta["shape"])

    def row_floats(self, name: str, row: int) -> list[float]:
        shape = self.shape(name)
        if len(shape) < 2:
            raise ValueError(f"{name} is not row-addressable")
        columns = shape[-1]
        rows = math.prod(shape[:-1])
        if row < 0 or row >= rows:
            raise IndexError(row)
        return self._read_floats(name, row * columns, columns)

    def values_floats(self, name: str) -> list[float]:
        return self._read_floats(name, 0, math.prod(self.shape(name)))

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
            return [float(value) for value in struct.unpack("<" + "e" * count, data)]
        return [_bf16_to_float(data[i : i + 2]) for i in range(0, len(data), 2)]


def _bf16_to_float(two: bytes) -> float:
    bits = struct.unpack("<H", two)[0] << 16
    return struct.unpack("<f", struct.pack("<I", bits))[0]


def inspect_summary(source: TensorSource, config: InklingConfig) -> dict:
    tensors, config_tensor, manifest_bytes = plan_bmoq(source, config)
    return {
        "architecture": "inkling",
        "config": config.__dict__ | {
            "local_layer_ids": list(config.local_layer_ids),
            "sparse_layer_ids": list(config.sparse_layer_ids),
            "stop_token_ids": list(config.stop_token_ids),
        },
        "bmoq_tensor_count": len(tensors) + 2,
        "config_bytes": len(config_tensor),
        "manifest_bytes": len(manifest_bytes),
        "estimated_bmoq_bytes": align_up(HEADER_BYTES + (len(tensors) + 2) * ENTRY_BYTES)
        + sum(align_up(tensor.byte_length) for tensor in tensors)
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

    config = InklingConfig.from_json(args.model_dir, args.group_size, args.max_context)
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
