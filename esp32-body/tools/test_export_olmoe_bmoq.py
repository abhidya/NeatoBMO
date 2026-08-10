#!/usr/bin/env python3

from __future__ import annotations

import json
import struct
import tempfile
import unittest
from pathlib import Path

from export_olmoe_bmoq import (
    ALIGNMENT,
    ARCH_OLMOE,
    ENTRY_BYTES,
    HEADER_BYTES,
    LAYOUT_DENSE_F32,
    LAYOUT_GROUP_SCALES_F32,
    LAYOUT_Q4_ROW_MAJOR,
    MANIFEST_TENSOR_ID,
    OlmoeConfig,
    SafetensorSource,
    expected_logical_tensors,
    scale_id,
    write_bmoq,
)


class SyntheticSource:
    def __init__(self, config: OlmoeConfig):
        self.config = config
        self.shapes = {tensor.hf_name: tensor.shape for tensor in expected_logical_tensors(config)}

    def shape(self, name: str) -> tuple[int, ...]:
        return self.shapes[name]

    def row_floats(self, name: str, row: int) -> list[float]:
        rows, columns = self.shapes[name]
        assert 0 <= row < rows
        salt = sum(name.encode("utf-8")) % 19
        return [((row * 7 + column * 3 + salt) % 17 - 8) / 8.0 for column in range(columns)]

    def values_floats(self, name: str) -> list[float]:
        (count,) = self.shapes[name]
        salt = sum(name.encode("utf-8")) % 11
        return [((index + salt) % 13 - 6) / 16.0 for index in range(count)]


def entries(path: Path) -> list[dict]:
    data = path.read_bytes()
    assert data[:4] == b"BMOQ"
    count = struct.unpack_from("<I", data, 16)[0]
    directory = struct.unpack_from("<Q", data, 24)[0]
    result = []
    for index in range(count):
        raw = data[directory + index * ENTRY_BYTES : directory + (index + 1) * ENTRY_BYTES]
        fields = struct.unpack("<IHH4IQQII16s", raw)
        result.append(
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
    return result


class ExportOlmoeBmoqTests(unittest.TestCase):
    def tiny_config(self) -> OlmoeConfig:
        return OlmoeConfig(
            hidden_size=8,
            intermediate_size=4,
            num_hidden_layers=2,
            num_attention_heads=2,
            num_key_value_heads=2,
            num_experts=3,
            num_experts_per_tok=2,
            vocab_size=16,
            max_position_embeddings=32,
            rope_theta=10000,
            eos_token_id=15,
            pad_token_id=1,
            tie_word_embeddings=False,
        )

    def test_export_is_aligned_deterministic_and_manifested(self) -> None:
        config = self.tiny_config()
        source = SyntheticSource(config)
        with tempfile.TemporaryDirectory() as tmp:
            first = Path(tmp) / "a.bmoq"
            second = Path(tmp) / "b.bmoq"
            summary = write_bmoq(source, first, config, group_size=4)
            write_bmoq(source, second, config, group_size=4)

            self.assertEqual(first.read_bytes(), second.read_bytes())
            self.assertEqual(summary["tensor_count"], len(entries(first)))

            raw = first.read_bytes()
            self.assertEqual(struct.unpack_from("<I", raw, 32)[0], ARCH_OLMOE)
            self.assertEqual(struct.unpack_from("<I", raw, 40)[0], config.hidden_size)
            self.assertEqual(struct.unpack_from("<I", raw, 88)[0], 4)
            self.assertEqual(struct.unpack_from("<I", raw, 92)[0], MANIFEST_TENSOR_ID)

            parsed = entries(first)
            offsets = [entry["offset"] for entry in parsed]
            self.assertEqual(offsets, sorted(offsets))
            self.assertTrue(all(offset % ALIGNMENT == 0 for offset in offsets))

            by_id = {entry["id"]: entry for entry in parsed}
            self.assertEqual(by_id[1]["layout"], LAYOUT_Q4_ROW_MAJOR)
            self.assertEqual(by_id[scale_id(1)]["layout"], LAYOUT_GROUP_SCALES_F32)
            self.assertEqual(by_id[2]["layout"], LAYOUT_DENSE_F32)
            self.assertIn(MANIFEST_TENSOR_ID, by_id)

            manifest_entry = by_id[MANIFEST_TENSOR_ID]
            manifest = json.loads(raw[manifest_entry["offset"] : manifest_entry["offset"] + manifest_entry["bytes"]])
            self.assertEqual(manifest["architecture"], "olmoe")
            self.assertEqual(manifest["config"]["num_experts"], config.num_experts)
            self.assertEqual(manifest["quantization"]["group_size"], 4)
            self.assertEqual(manifest["tensor_count_without_manifest"], len(parsed) - 1)

    def test_first_q4_row_matches_expected_quantization(self) -> None:
        config = self.tiny_config()
        source = SyntheticSource(config)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "model.bmoq"
            write_bmoq(source, path, config, group_size=4)
            raw = path.read_bytes()
            by_id = {entry["id"]: entry for entry in entries(path)}
            weights = by_id[1]
            scales = by_id[scale_id(1)]

            row = source.row_floats("model.embed_tokens.weight", 0)
            expected_packed = bytearray()
            expected_scales = bytearray()
            for start in range(0, len(row), 4):
                group = row[start : start + 4]
                scale = max(abs(v) for v in group) / 7.0
                expected_scales.extend(struct.pack("<f", scale))
                q = [max(-8, min(7, int(round(v / scale)))) & 0xF for v in group]
                expected_packed.extend([q[0] | (q[1] << 4), q[2] | (q[3] << 4)])

            self.assertEqual(raw[weights["offset"] : weights["offset"] + 4], bytes(expected_packed))
            self.assertEqual(raw[scales["offset"] : scales["offset"] + 8], bytes(expected_scales))

    def test_shape_mismatch_fails_before_writing(self) -> None:
        config = self.tiny_config()
        source = SyntheticSource(config)
        source.shapes["model.embed_tokens.weight"] = (config.vocab_size, config.hidden_size + 1)
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "expected"):
                write_bmoq(source, Path(tmp) / "bad.bmoq", config, group_size=4)

    def test_group_size_mismatch_fails_before_writing(self) -> None:
        config = self.tiny_config()
        source = SyntheticSource(config)
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "bad.bmoq"
            with self.assertRaisesRegex(ValueError, "does not divide"):
                write_bmoq(source, output, config, group_size=6)
            self.assertFalse(output.exists())

    def test_local_safetensors_source_exports_without_optional_packages(self) -> None:
        config = self.tiny_config()
        with tempfile.TemporaryDirectory() as tmp:
            model_dir = Path(tmp)
            self.write_safetensors_fixture(model_dir / "model.safetensors", config)
            output = model_dir / "from_safe.bmoq"
            summary = write_bmoq(SafetensorSource(model_dir), output, config, group_size=4)
            self.assertGreater(summary["tensor_count"], 0)
            self.assertIn(MANIFEST_TENSOR_ID, {entry["id"] for entry in entries(output)})

    def write_safetensors_fixture(self, path: Path, config: OlmoeConfig) -> None:
        header = {}
        payload = bytearray()
        for tensor in expected_logical_tensors(config):
            start = len(payload)
            count = 1
            for dimension in tensor.shape:
                count *= dimension
            salt = sum(tensor.hf_name.encode("utf-8")) % 23
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
