#!/usr/bin/env python3

from __future__ import annotations

import json
import struct
import tempfile
import unittest
from pathlib import Path

from export_glm52_bmoq import (
    ALIGNMENT,
    ARCH_GLM52,
    CONFIG_TENSOR_ID,
    ENTRY_BYTES,
    LAYOUT_DENSE_F32,
    LAYOUT_EXPERT_GROUP_SCALES_F32,
    LAYOUT_GROUP_SCALES_F32,
    LAYOUT_OPAQUE,
    LAYOUT_Q4_EXPERT_BUNDLE,
    LAYOUT_Q4_ROW_MAJOR,
    MANIFEST_TENSOR_ID,
    VERSION,
    Glm52Config,
    SafetensorSource,
    dense_gate_id,
    expected_logical_tensors,
    expert_gate_id,
    quantize_row,
    scale_id,
    sparse_router_id,
    write_bmoq,
)

TMP_ROOT = Path("/Volumes/2TB") if Path("/Volumes/2TB").exists() else None


class SyntheticSource:
    def __init__(self, config: Glm52Config):
        self.shapes = {tensor.hf_name: tensor.shape for tensor in expected_logical_tensors(config)}

    def shape(self, name: str) -> tuple[int, ...]:
        return self.shapes[name]

    def row_floats(self, name: str, row: int) -> list[float]:
        rows, columns = self.shapes[name]
        assert 0 <= row < rows
        salt = sum(name.encode("utf-8")) % 23
        return [((row * 5 + column * 3 + salt) % 19 - 9) / 8.0 for column in range(columns)]

    def values_floats(self, name: str) -> list[float]:
        (count,) = self.shapes[name]
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


class ExportGlm52BmoqTests(unittest.TestCase):
    def tempdir(self) -> tempfile.TemporaryDirectory:
        return tempfile.TemporaryDirectory(dir=TMP_ROOT)

    def tiny_config(self) -> Glm52Config:
        return Glm52Config(
            hidden_size=8,
            dense_intermediate_size=12,
            num_hidden_layers=2,
            num_attention_heads=2,
            num_experts=2,
            num_experts_per_tok=1,
            moe_intermediate_size=4,
            first_dense_layers=1,
            q_lora_rank=4,
            kv_lora_rank=4,
            qk_nope_head_dim=2,
            qk_rope_head_dim=2,
            v_head_dim=2,
            shared_experts=1,
            vocab_size=16,
            max_position_embeddings=32,
            rope_theta=10000,
            eos_token_id=15,
            pad_token_id=0,
            expert_groups=1,
            topk_groups=1,
            normalize_topk=True,
            rms_norm_epsilon=1.0e-6,
            routed_scale=2.5,
            stop_token_ids=(15, 17, 19),
            quant_group=4,
        )

    def test_export_is_bmoq_v2_deterministic_and_complete_for_dense_and_sparse_layers(self) -> None:
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
            self.assertEqual(struct.unpack_from("<I", raw, 32)[0], ARCH_GLM52)
            self.assertEqual(struct.unpack_from("<I", raw, 40)[0], config.hidden_size)
            self.assertEqual(struct.unpack_from("<I", raw, 56)[0], config.num_attention_heads)
            self.assertEqual(struct.unpack_from("<I", raw, 88)[0], config.quant_group)
            self.assertEqual(struct.unpack_from("<I", raw, 92)[0], MANIFEST_TENSOR_ID)

            parsed = entries(first)
            offsets = [entry["offset"] for entry in parsed]
            self.assertEqual(offsets, sorted(offsets))
            self.assertTrue(all(offset % ALIGNMENT == 0 for offset in offsets))

            by_id = {entry["id"]: entry for entry in parsed}
            self.assertEqual(by_id[dense_gate_id(0)]["layout"], LAYOUT_Q4_ROW_MAJOR)
            self.assertEqual(by_id[scale_id(dense_gate_id(0))]["layout"], LAYOUT_GROUP_SCALES_F32)
            self.assertEqual(by_id[sparse_router_id(1)]["shape"], (config.num_experts, config.hidden_size, 1, 1))
            self.assertIn(expert_gate_id(1, 0), by_id)
            self.assertNotIn(expert_gate_id(1, 1), by_id)
            gate_bundle = by_id[expert_gate_id(1, 0)]
            self.assertEqual(gate_bundle["layout"], LAYOUT_Q4_EXPERT_BUNDLE)
            self.assertEqual(
                gate_bundle["shape"],
                (
                    config.num_experts,
                    config.moe_intermediate_size,
                    config.hidden_size,
                    1,
                ),
            )
            self.assertEqual(
                by_id[scale_id(expert_gate_id(1, 0))]["layout"],
                LAYOUT_EXPERT_GROUP_SCALES_F32,
            )
            second_expert = source.row_floats(
                "model.layers.1.mlp.experts.1.gate_proj.weight", 0
            )
            expected_weight_row, expected_scale_row = quantize_row(
                second_expert, config.quant_group
            )
            expert_weight_stride = (
                config.moe_intermediate_size * config.hidden_size // 2
            )
            expert_scale_stride = (
                config.moe_intermediate_size
                * (config.hidden_size // config.quant_group)
                * 4
            )
            self.assertEqual(
                raw[
                    gate_bundle["offset"]
                    + expert_weight_stride : gate_bundle["offset"]
                    + expert_weight_stride
                    + len(expected_weight_row)
                ],
                expected_weight_row,
            )
            gate_scales = by_id[scale_id(expert_gate_id(1, 0))]
            self.assertEqual(
                raw[
                    gate_scales["offset"]
                    + expert_scale_stride : gate_scales["offset"]
                    + expert_scale_stride
                    + len(expected_scale_row)
                ],
                expected_scale_row,
            )
            self.assertEqual(by_id[2]["layout"], LAYOUT_DENSE_F32)
            self.assertEqual(by_id[CONFIG_TENSOR_ID]["layout"], LAYOUT_OPAQUE)
            self.assertEqual(by_id[MANIFEST_TENSOR_ID]["layout"], LAYOUT_OPAQUE)

            cfg = raw[by_id[CONFIG_TENSOR_ID]["offset"] : by_id[CONFIG_TENSOR_ID]["offset"] + by_id[CONFIG_TENSOR_ID]["bytes"]]
            self.assertEqual(cfg[:4], b"BCFG")
            self.assertIn(struct.pack("<III", 15, 17, 19), cfg)

            manifest_entry = by_id[MANIFEST_TENSOR_ID]
            manifest = json.loads(raw[manifest_entry["offset"] : manifest_entry["offset"] + manifest_entry["bytes"]])
            self.assertEqual(manifest["architecture"], "glm52")
            self.assertEqual(manifest["config"]["kv_lora_rank"], config.kv_lora_rank)
            self.assertEqual(manifest["config"]["stop_token_ids"], [15, 17, 19])
            self.assertEqual(manifest["tensor_count_without_metadata"], len(parsed) - 2)

    def test_first_q4_row_matches_expected_quantization(self) -> None:
        config = self.tiny_config()
        source = SyntheticSource(config)
        with self.tempdir() as tmp:
            path = Path(tmp) / "model.bmoq"
            write_bmoq(source, path, config)
            raw = path.read_bytes()
            by_id = {entry["id"]: entry for entry in entries(path)}
            weights = by_id[1]
            scales = by_id[scale_id(1)]

            row = source.row_floats("model.embed_tokens.weight", 0)
            expected_packed = bytearray()
            expected_scales = bytearray()
            for start in range(0, len(row), config.quant_group):
                group = row[start : start + config.quant_group]
                scale = max(abs(value) for value in group) / 7.0
                expected_scales.extend(struct.pack("<f", scale))
                q = [max(-8, min(7, int(round(value / scale)))) & 0xF for value in group]
                expected_packed.extend([q[0] | (q[1] << 4), q[2] | (q[3] << 4)])

            self.assertEqual(raw[weights["offset"] : weights["offset"] + 4], bytes(expected_packed))
            self.assertEqual(raw[scales["offset"] : scales["offset"] + 8], bytes(expected_scales))

    def test_shape_mismatch_fails_before_writing(self) -> None:
        config = self.tiny_config()
        source = SyntheticSource(config)
        source.shapes["model.layers.1.mlp.experts.0.gate_proj.weight"] = (
            config.moe_intermediate_size,
            config.hidden_size + 1,
        )
        with self.tempdir() as tmp:
            output = Path(tmp) / "bad.bmoq"
            with self.assertRaisesRegex(ValueError, "expected"):
                write_bmoq(source, output, config)
            self.assertFalse(output.exists())

    def test_config_json_unions_generation_stop_ids(self) -> None:
        with self.tempdir() as tmp:
            model_dir = Path(tmp)
            (model_dir / "config.json").write_text(
                json.dumps(
                    {
                        "hidden_size": 8,
                        "intermediate_size": 12,
                        "num_hidden_layers": 2,
                        "num_attention_heads": 2,
                        "n_routed_experts": 2,
                        "num_experts_per_tok": 1,
                        "moe_intermediate_size": 4,
                        "first_k_dense_replace": 1,
                        "q_lora_rank": 4,
                        "kv_lora_rank": 4,
                        "qk_nope_head_dim": 2,
                        "qk_rope_head_dim": 2,
                        "v_head_dim": 2,
                        "n_shared_experts": 1,
                        "vocab_size": 16,
                        "max_position_embeddings": 32,
                        "rope_parameters": {"rope_theta": 10000},
                        "eos_token_id": [15, 17],
                        "pad_token_id": 0,
                        "n_group": 1,
                        "topk_group": 1,
                        "norm_topk_prob": True,
                        "rms_norm_eps": 1.0e-6,
                        "routed_scaling_factor": 2.5,
                    }
                ),
                encoding="utf-8",
            )
            (model_dir / "generation_config.json").write_text(
                json.dumps({"eos_token_id": [17, 19]}),
                encoding="utf-8",
            )
            parsed = Glm52Config.from_json(model_dir, quant_group=4)
            self.assertEqual(parsed.stop_token_ids, (15, 17, 19))
            self.assertEqual(parsed.eos_token_id, 15)

    def test_local_safetensors_source_exports_without_optional_packages(self) -> None:
        config = self.tiny_config()
        with self.tempdir() as tmp:
            model_dir = Path(tmp)
            self.write_safetensors_fixture(model_dir / "model.safetensors", config)
            output = model_dir / "from_safe.bmoq"
            summary = write_bmoq(SafetensorSource(model_dir), output, config)
            self.assertGreater(summary["tensor_count"], 0)
            self.assertIn(CONFIG_TENSOR_ID, {entry["id"] for entry in entries(output)})

    def test_expert_bundles_fit_official_sparse_directory_bound(self) -> None:
        config = Glm52Config(
            hidden_size=6144,
            dense_intermediate_size=18432,
            num_hidden_layers=78,
            num_attention_heads=128,
            num_experts=256,
            num_experts_per_tok=8,
            moe_intermediate_size=2048,
            first_dense_layers=3,
            q_lora_rank=1536,
            kv_lora_rank=512,
            qk_nope_head_dim=128,
            qk_rope_head_dim=64,
            v_head_dim=128,
            shared_experts=1,
            vocab_size=151552,
            max_position_embeddings=4096,
            rope_theta=10000,
            eos_token_id=151329,
            pad_token_id=0,
            expert_groups=1,
            topk_groups=1,
            normalize_topk=True,
            rms_norm_epsilon=1.0e-6,
            routed_scale=2.5,
            stop_token_ids=(151329, 151336, 151338),
            quant_group=32,
        )
        logical = expected_logical_tensors(config)
        unbundled_count = sum(1 if len(tensor.shape) == 1 else 2 for tensor in logical) + 2
        self.assertGreater(unbundled_count, 8192)
        dense_layers = config.first_dense_layers
        sparse_layers = config.num_hidden_layers - dense_layers
        bundled_count = (
            5
            + config.num_hidden_layers * 14
            + dense_layers * 6
            + sparse_layers * 15
            + 2
        )
        self.assertLessEqual(bundled_count, 8192)
        self.assertEqual(bundled_count, 2242)

    def write_safetensors_fixture(self, path: Path, config: Glm52Config) -> None:
        header = {}
        payload = bytearray()
        for tensor in expected_logical_tensors(config):
            start = len(payload)
            count = 1
            for dimension in tensor.shape:
                count *= dimension
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
