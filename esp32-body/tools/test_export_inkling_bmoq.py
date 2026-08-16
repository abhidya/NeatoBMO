#!/usr/bin/env python3

from __future__ import annotations

import json
import math
import struct
import tempfile
import unittest
from pathlib import Path

from export_inkling_bmoq import (
    ALIGNMENT,
    ARCH_INKLING,
    CONFIG_TENSOR_ID,
    DTYPE_F32,
    DTYPE_Q4_SYM,
    ENTRY_BYTES,
    LAYOUT_DENSE_F32,
    LAYOUT_EXPERT_GROUP_SCALES_F32,
    LAYOUT_GROUP_SCALES_F32,
    LAYOUT_OPAQUE,
    LAYOUT_Q4_EXPERT_BUNDLE,
    LAYOUT_Q4_ROW_MAJOR,
    MANIFEST_TENSOR_ID,
    VERSION,
    InklingConfig,
    SafetensorSource,
    dense_gate_id,
    expected_logical_tensors,
    physical_tensors,
    quantize_row,
    routed_gate_id,
    routed_down_id,
    routed_up_id,
    scale_id,
    sparse_router_id,
    write_bmoq,
)

TMP_ROOT = Path("/Volumes/2TB") if Path("/Volumes/2TB").exists() else None


class SyntheticSource:
    def __init__(self, config: InklingConfig):
        self.shapes = {tensor.hf_name: tensor.shape for tensor in expected_logical_tensors(config)}

    def shape(self, name: str) -> tuple[int, ...]:
        return self.shapes[name]

    def row_floats(self, name: str, row: int) -> list[float]:
        shape = self.shapes[name]
        columns = shape[-1]
        rows = math.prod(shape[:-1])
        assert 0 <= row < rows
        salt = sum(name.encode("utf-8")) % 23
        return [((row * 5 + column * 3 + salt) % 19 - 9) / 8.0 for column in range(columns)]

    def values_floats(self, name: str) -> list[float]:
        count = math.prod(self.shapes[name])
        salt = sum(name.encode("utf-8")) % 17
        return [((index + salt) % 13 - 6) / 16.0 for index in range(count)]


def entries(path: Path) -> list[dict]:
    data = path.read_bytes()
    assert data[:4] == b"BMOQ"
    count = struct.unpack_from("<I", data, 16)[0]
    directory = struct.unpack_from("<Q", data, 24)[0]
    parsed = []
    for index in range(count):
        raw = data[directory + index * ENTRY_BYTES : directory + (index + 1) * ENTRY_BYTES]
        fields = struct.unpack("<IHH4IQQII16s", raw)
        parsed.append(
            {
                "id": fields[0],
                "dtype": fields[1],
                "group": fields[2],
                "shape": fields[3:7],
                "offset": fields[7],
                "bytes": fields[8],
                "layout": fields[9],
                "name": fields[11].rstrip(b"\0").decode("ascii"),
            }
        )
    return parsed


class ExportInklingBmoqTests(unittest.TestCase):
    def tempdir(self) -> tempfile.TemporaryDirectory:
        return tempfile.TemporaryDirectory(dir=TMP_ROOT)

    def tiny_config(self) -> InklingConfig:
        return InklingConfig(
            hidden_size=8,
            dense_intermediate_size=12,
            moe_intermediate_size=4,
            num_hidden_layers=3,
            num_attention_heads=2,
            num_key_value_heads=1,
            head_dim=4,
            swa_num_attention_heads=2,
            swa_num_key_value_heads=2,
            swa_head_dim=4,
            sliding_window_size=8,
            d_rel=2,
            rel_extent=12,
            sconv_kernel_size=4,
            n_routed_experts=3,
            num_experts_per_tok=2,
            n_shared_experts=2,
            dense_mlp_idx=1,
            vocab_size=16,
            unpadded_vocab_size=14,
            max_position_embeddings=32,
            eos_token_id=15,
            pad_token_id=0,
            rms_norm_epsilon=1.0e-6,
            route_scale=2.0,
            logits_mup_width_multiplier=4.0,
            log_scaling_n_floor=8,
            log_scaling_alpha=0.1,
            local_layer_ids=(0, 2),
            sparse_layer_ids=(1, 2),
            stop_token_ids=(15, 17),
            quant_group=4,
        )

    def test_export_is_bmoq_v2_deterministic_and_covers_inkling_text_tensors(self) -> None:
        config = self.tiny_config()
        source = SyntheticSource(config)
        with self.tempdir() as tmp:
            first = Path(tmp) / "a.bmoq"
            second = Path(tmp) / "b.bmoq"
            summary = write_bmoq(source, first, config)
            write_bmoq(source, second, config)

            self.assertEqual(first.read_bytes(), second.read_bytes())
            self.assertEqual(summary["tensor_count"], len(entries(first)))

            raw = first.read_bytes()
            self.assertEqual(struct.unpack_from("<H", raw, 4)[0], VERSION)
            self.assertEqual(struct.unpack_from("<I", raw, 32)[0], ARCH_INKLING)
            self.assertEqual(struct.unpack_from("<I", raw, 40)[0], config.hidden_size)
            self.assertEqual(struct.unpack_from("<I", raw, 52)[0], config.num_attention_heads)
            self.assertEqual(struct.unpack_from("<I", raw, 56)[0], config.num_key_value_heads)
            self.assertEqual(struct.unpack_from("<I", raw, 88)[0], config.quant_group)
            self.assertEqual(struct.unpack_from("<I", raw, 92)[0], MANIFEST_TENSOR_ID)

            parsed = entries(first)
            offsets = [entry["offset"] for entry in parsed]
            self.assertEqual(offsets, sorted(offsets))
            self.assertTrue(all(offset % ALIGNMENT == 0 for offset in offsets))

            by_id = {entry["id"]: entry for entry in parsed}
            self.assertEqual(by_id[1]["layout"], LAYOUT_Q4_ROW_MAJOR)
            self.assertEqual(by_id[2]["layout"], LAYOUT_DENSE_F32)
            self.assertEqual(by_id[dense_gate_id(0)]["shape"], (config.dense_intermediate_size, config.hidden_size, 1, 1))
            self.assertEqual(by_id[sparse_router_id(1)]["shape"], (config.n_routed_experts + config.n_shared_experts, config.hidden_size, 1, 1))
            self.assertEqual(by_id[routed_gate_id(1)]["layout"], LAYOUT_Q4_EXPERT_BUNDLE)
            self.assertEqual(by_id[routed_up_id(1)]["layout"], LAYOUT_Q4_EXPERT_BUNDLE)
            self.assertEqual(by_id[routed_down_id(2)]["layout"], LAYOUT_Q4_EXPERT_BUNDLE)
            self.assertEqual(
                by_id[routed_gate_id(1)]["shape"],
                (config.n_routed_experts, config.moe_intermediate_size, config.hidden_size, 1),
            )
            self.assertEqual(
                by_id[routed_up_id(1)]["shape"],
                (config.n_routed_experts, config.moe_intermediate_size, config.hidden_size, 1),
            )
            self.assertEqual(
                by_id[scale_id(routed_gate_id(1))]["layout"],
                LAYOUT_EXPERT_GROUP_SCALES_F32,
            )
            self.assertNotIn(scale_id(sparse_router_id(1)), by_id)
            self.assertEqual(by_id[CONFIG_TENSOR_ID]["layout"], LAYOUT_OPAQUE)
            self.assertEqual(by_id[MANIFEST_TENSOR_ID]["layout"], LAYOUT_OPAQUE)

            cfg = raw[by_id[CONFIG_TENSOR_ID]["offset"] : by_id[CONFIG_TENSOR_ID]["offset"] + by_id[CONFIG_TENSOR_ID]["bytes"]]
            self.assertEqual(cfg[:4], b"BCFG")
            self.assertIn(struct.pack("<II", 15, 17), cfg)

            manifest_entry = by_id[MANIFEST_TENSOR_ID]
            manifest = json.loads(raw[manifest_entry["offset"] : manifest_entry["offset"] + manifest_entry["bytes"]])
            self.assertEqual(manifest["architecture"], "inkling")
            self.assertEqual(manifest["config"]["local_layer_ids"], [0, 2])
            self.assertEqual(manifest["config"]["sparse_layer_ids"], [1, 2])
            self.assertIn("model.layers.1.mlp.experts.gate_up_proj#gate", {item["name"] for item in manifest["tensors"]})
            self.assertIn("model.layers.1.mlp.experts.gate_up_proj#up", {item["name"] for item in manifest["tensors"]})
            self.assertIn("model.layers.2.self_attn.rel_logits_proj.proj", {item["name"] for item in manifest["tensors"]})
            self.assertEqual(manifest["tensor_count_without_metadata"], len(parsed) - 2)

    def test_dtype_policy_keeps_runtime_dense_tensors_f32_and_only_q4_tensors_have_scales(self) -> None:
        config = self.tiny_config()
        with self.tempdir() as tmp:
            path = Path(tmp) / "policy.bmoq"
            write_bmoq(SyntheticSource(config), path, config)
            raw = path.read_bytes()
            by_id = {entry["id"]: entry for entry in entries(path)}
            manifest = json.loads(
                raw[
                    by_id[MANIFEST_TENSOR_ID]["offset"] : by_id[MANIFEST_TENSOR_ID]["offset"]
                    + by_id[MANIFEST_TENSOR_ID]["bytes"]
                ]
            )
            by_name = {item["name"]: item for item in manifest["tensors"]}

            dense_names = [
                "model.layers.1.self_attn.q_norm.weight",
                "model.layers.1.self_attn.k_norm.weight",
                "model.layers.1.self_attn.rel_logits_proj.proj",
                "model.layers.1.self_attn.k_sconv.conv1d.weight",
                "model.layers.1.self_attn.v_sconv.conv1d.weight",
                "model.layers.1.attn_sconv.conv1d.weight",
                "model.layers.1.mlp_sconv.conv1d.weight",
                "model.layers.1.mlp.gate.weight",
                "model.layers.1.mlp.gate.e_score_correction_bias",
                "model.layers.1.mlp.gate.global_scale",
                "model.layers.0.mlp.global_scale",
            ]
            for name in dense_names:
                self.assertEqual(by_name[name]["dtype"], DTYPE_F32, name)
                self.assertEqual(by_name[name]["layout"], LAYOUT_DENSE_F32, name)
                self.assertNotIn(name + "#scales", by_name)

            q4_names = [
                "model.layers.1.self_attn.q_proj.weight",
                "model.layers.1.self_attn.o_proj.weight",
                "model.layers.0.mlp.gate_proj.weight",
                "model.layers.1.mlp.shared_experts.gate_proj",
                "model.layers.1.mlp.experts.gate_up_proj#gate",
                "model.layers.1.mlp.experts.gate_up_proj#up",
                "model.layers.1.mlp.experts.down_proj",
            ]
            for name in q4_names:
                self.assertEqual(by_name[name]["dtype"], DTYPE_Q4_SYM, name)
                self.assertIn(name + "#scales", by_name)

    def test_first_fused_expert_row_matches_expected_quantization(self) -> None:
        config = self.tiny_config()
        source = SyntheticSource(config)
        with self.tempdir() as tmp:
            path = Path(tmp) / "model.bmoq"
            write_bmoq(source, path, config)
            raw = path.read_bytes()
            by_id = {entry["id"]: entry for entry in entries(path)}
            weights = by_id[routed_up_id(1)]
            scales = by_id[scale_id(routed_up_id(1))]

            row = source.row_floats("model.layers.1.mlp.experts.gate_up_proj", config.moe_intermediate_size)
            expected_weight_row, expected_scale_row = quantize_row(row, config.quant_group)
            self.assertEqual(raw[weights["offset"] : weights["offset"] + len(expected_weight_row)], expected_weight_row)
            self.assertEqual(raw[scales["offset"] : scales["offset"] + len(expected_scale_row)], expected_scale_row)

    def test_shape_mismatch_fails_before_writing(self) -> None:
        config = self.tiny_config()
        source = SyntheticSource(config)
        source.shapes["model.layers.1.mlp.experts.down_proj"] = (
            config.n_routed_experts,
            config.hidden_size,
            config.moe_intermediate_size + 1,
        )
        with self.tempdir() as tmp:
            output = Path(tmp) / "bad.bmoq"
            with self.assertRaisesRegex(ValueError, "expected"):
                write_bmoq(source, output, config)
            self.assertFalse(output.exists())

    def test_config_json_accepts_flat_and_nested_inkling_forms(self) -> None:
        with self.tempdir() as tmp:
            model_dir = Path(tmp)
            (model_dir / "config.json").write_text(
                json.dumps(
                    {
                        "eos_token_id": [15, 17],
                        "text_config": {
                            "vocab_size": 16,
                            "unpadded_vocab_size": 14,
                            "hidden_size": 8,
                            "num_hidden_layers": 3,
                            "num_attention_heads": 2,
                            "num_key_value_heads": 1,
                            "head_dim": 4,
                            "swa_num_attention_heads": 2,
                            "swa_num_key_value_heads": 2,
                            "swa_head_dim": 4,
                            "sliding_window_size": 8,
                            "d_rel": 2,
                            "rel_extent": 12,
                            "log_scaling_n_floor": 8,
                            "log_scaling_alpha": 0.1,
                            "local_layer_ids": [0, 2],
                            "dense_mlp_idx": 1,
                            "dense_intermediate_size": 12,
                            "intermediate_size": 4,
                            "n_routed_experts": 3,
                            "num_experts_per_tok": 2,
                            "n_shared_experts": 2,
                            "route_scale": 2.0,
                            "logits_mup_width_multiplier": 4.0,
                            "max_position_embeddings": 32,
                            "rms_norm_eps": 1.0e-6,
                        },
                    }
                ),
                encoding="utf-8",
            )
            (model_dir / "generation_config.json").write_text(json.dumps({"eos_token_id": [17, 19]}), encoding="utf-8")
            parsed = InklingConfig.from_json(model_dir, quant_group=4)
            self.assertEqual(parsed.stop_token_ids, (15, 17, 19))
            self.assertEqual(parsed.local_layer_ids, (0, 2))
            self.assertEqual(parsed.sparse_layer_ids, (1, 2))
            self.assertEqual(parsed.moe_intermediate_size, 4)

    def test_short_or_unknown_layer_type_arrays_fall_back_per_layer(self) -> None:
        with self.tempdir() as tmp:
            model_dir = Path(tmp)
            (model_dir / "config.json").write_text(
                json.dumps(
                    {
                        "hidden_size": 8,
                        "num_hidden_layers": 6,
                        "num_attention_heads": 2,
                        "num_key_value_heads": 1,
                        "head_dim": 4,
                        "swa_num_attention_heads": 2,
                        "swa_num_key_value_heads": 2,
                        "swa_head_dim": 4,
                        "layer_types": ["hybrid", "unknown", "hybrid_sliding"],
                        "local_layer_ids": [1, 4],
                        "mlp_layer_types": ["dense", "mystery", "sparse"],
                        "dense_mlp_idx": 1,
                        "dense_intermediate_size": 12,
                        "intermediate_size": 4,
                        "vocab_size": 16,
                        "n_routed_experts": 3,
                        "num_experts_per_tok": 2,
                        "n_shared_experts": 2,
                    }
                ),
                encoding="utf-8",
            )
            parsed = InklingConfig.from_json(model_dir, quant_group=4)
            self.assertEqual(parsed.local_layer_ids, (2, 4))
            self.assertEqual(parsed.sparse_layer_ids, (2, 3, 4, 5))

    def test_context_ignores_tokenizer_sentinel_and_validates_uint32_bounds(self) -> None:
        with self.tempdir() as tmp:
            model_dir = Path(tmp)
            config = {
                "hidden_size": 8,
                "num_hidden_layers": 1,
                "num_attention_heads": 2,
                "num_key_value_heads": 1,
                "head_dim": 4,
                "swa_num_key_value_heads": 1,
                "vocab_size": 16,
                "n_routed_experts": 3,
                "num_experts_per_tok": 2,
                "n_shared_experts": 2,
            }
            (model_dir / "config.json").write_text(json.dumps(config), encoding="utf-8")
            (model_dir / "tokenizer_config.json").write_text(
                json.dumps({"model_max_length": 1000000000000000019884624838656}),
                encoding="utf-8",
            )
            parsed = InklingConfig.from_json(model_dir, quant_group=4)
            self.assertEqual(parsed.max_position_embeddings, 1_048_576)

            (model_dir / "config.json").write_text(json.dumps(config | {"max_position_embeddings": 0x100000000}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "max_position_embeddings"):
                InklingConfig.from_json(model_dir, quant_group=4)

            parsed = InklingConfig.from_json(model_dir, quant_group=4, max_context=1234)
            self.assertEqual(parsed.max_position_embeddings, 1234)

    def test_local_safetensors_source_exports_rank_three_tensors_without_optional_packages(self) -> None:
        config = self.tiny_config()
        with self.tempdir() as tmp:
            model_dir = Path(tmp)
            self.write_safetensors_fixture(model_dir / "model.safetensors", config)
            output = model_dir / "from_safe.bmoq"
            summary = write_bmoq(SafetensorSource(model_dir), output, config)
            self.assertGreater(summary["tensor_count"], 0)
            self.assertIn(CONFIG_TENSOR_ID, {entry["id"] for entry in entries(output)})

    def test_official_sparse_directory_bound_stays_below_bmoq_limit(self) -> None:
        config = InklingConfig(
            hidden_size=6144,
            dense_intermediate_size=24576,
            moe_intermediate_size=3072,
            num_hidden_layers=66,
            num_attention_heads=64,
            num_key_value_heads=8,
            head_dim=128,
            swa_num_attention_heads=64,
            swa_num_key_value_heads=16,
            swa_head_dim=128,
            sliding_window_size=512,
            d_rel=16,
            rel_extent=1024,
            sconv_kernel_size=4,
            n_routed_experts=256,
            num_experts_per_tok=6,
            n_shared_experts=2,
            dense_mlp_idx=2,
            vocab_size=201024,
            unpadded_vocab_size=201000,
            max_position_embeddings=4096,
            eos_token_id=0xFFFFFFFF,
            pad_token_id=0,
            rms_norm_epsilon=1.0e-6,
            route_scale=8.0,
            logits_mup_width_multiplier=24.0,
            log_scaling_n_floor=0,
            log_scaling_alpha=0.1,
            local_layer_ids=tuple(i for i in range(66) if (i + 1) % 6 != 0),
            sparse_layer_ids=tuple(range(2, 66)),
            stop_token_ids=(),
            quant_group=32,
        )
        tensor_count = len(physical_tensors(SyntheticSource(config), config)) + 2
        self.assertEqual(tensor_count, 2236)
        self.assertLessEqual(tensor_count, 8192)

    def write_safetensors_fixture(self, path: Path, config: InklingConfig) -> None:
        header = {}
        payload = bytearray()
        for tensor in expected_logical_tensors(config):
            start = len(payload)
            count = math.prod(tensor.shape)
            salt = sum(tensor.hf_name.encode("utf-8")) % 29
            for index in range(count):
                payload.extend(struct.pack("<f", ((index + salt) % 17 - 8) / 8.0))
            header[tensor.hf_name] = {
                "dtype": "F32",
                "shape": list(tensor.shape),
                "data_offsets": [start, len(payload)],
            }
        header_bytes = json.dumps(header, sort_keys=True, separators=(",", ":")).encode("utf-8")
        path.write_bytes(struct.pack("<Q", len(header_bytes)) + header_bytes + payload)



if __name__ == "__main__":
    unittest.main()
