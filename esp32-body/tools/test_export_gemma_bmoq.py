#!/usr/bin/env python3

from __future__ import annotations

import json
import struct
import tempfile
import unittest
from pathlib import Path

from export_gemma_gguf_bmoq import (
    ALIGNMENT,
    ARCH_GEMMA3,
    ENTRY_BYTES,
    GGUF_TYPE_F32,
    GGUF_TYPE_Q8_0,
    HEADER_BYTES,
    LAYOUT_DENSE_F32,
    LAYOUT_GROUP_SCALES_F32,
    LAYOUT_Q4_ROW_MAJOR,
    MANIFEST_TENSOR_ID,
    GemmaConfig,
    GgufSource,
    GgufTensor,
    expected_logical_tensors,
    scale_id,
    write_bmoq,
)


class SyntheticSource:
    def __init__(self, config: GemmaConfig):
        self.path = Path("synthetic.gguf")
        self.metadata = {
            "general.architecture": "gemma3",
            "gemma3.embedding_length": config.hidden_size,
            "gemma3.feed_forward_length": config.intermediate_size,
            "gemma3.block_count": config.num_hidden_layers,
            "gemma3.attention.head_count": config.num_attention_heads,
            "gemma3.attention.head_count_kv": config.num_key_value_heads,
            "gemma3.attention.key_length": config.head_dim,
            "gemma3.attention.value_length": config.head_dim,
            "gemma3.context_length": config.max_position_embeddings,
            "gemma3.rope.freq_base": float(config.rope_theta),
            "gemma3.attention.sliding_window": config.sliding_window,
            "tokenizer.ggml.tokens": [str(i) for i in range(config.vocab_size)],
            "tokenizer.ggml.eos_token_id": config.eos_token_id,
            "tokenizer.ggml.padding_token_id": config.pad_token_id,
        }
        self.tensors = {}
        self.shapes = {tensor.gguf_name: tensor.shape for tensor in expected_logical_tensors(config)}

    def shape(self, name: str) -> tuple[int, ...]:
        return self.shapes[name]

    def row_floats(self, name: str, row: int) -> list[float]:
        rows, columns = self.shapes[name]
        assert 0 <= row < rows
        salt = sum(name.encode("utf-8")) % 19
        return [((row * 5 + column * 3 + salt) % 17 - 8) / 8.0 for column in range(columns)]

    def values_floats(self, name: str) -> list[float]:
        (count,) = self.shapes[name]
        salt = sum(name.encode("utf-8")) % 11
        return [((index + salt) % 13 - 6) / 16.0 for index in range(count)]


def tiny_config() -> GemmaConfig:
    return GemmaConfig(
        hidden_size=8,
        intermediate_size=16,
        num_hidden_layers=2,
        num_attention_heads=2,
        num_key_value_heads=1,
        vocab_size=32,
        max_position_embeddings=64,
        rope_theta=1000000,
        eos_token_id=1,
        pad_token_id=0,
        quant_group=4,
        head_dim=4,
        sliding_window=8,
        tie_word_embeddings=True,
    )


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


def write_string(buf: bytearray, value: str) -> None:
    raw = value.encode("utf-8")
    buf.extend(struct.pack("<Q", len(raw)))
    buf.extend(raw)


def write_value(buf: bytearray, key: str, value_type: int, value) -> None:
    write_string(buf, key)
    buf.extend(struct.pack("<I", value_type))
    if value_type == 8:
        write_string(buf, value)
    elif value_type == 9:
        element_type, values = value
        buf.extend(struct.pack("<IQ", element_type, len(values)))
        for item in values:
            if element_type == 8:
                write_string(buf, item)
            else:
                buf.extend(struct.pack("<" + {4: "I", 5: "i", 6: "f"}[element_type], item))
    else:
        buf.extend(struct.pack("<" + {4: "I", 5: "i", 6: "f", 7: "?"}[value_type], value))


def f16(value: float) -> bytes:
    return struct.pack("<e", value)


def write_tiny_gguf(path: Path) -> None:
    metadata = bytearray()
    tokens = ["<pad>", "<eos>", "<bos>", "<unk>"]
    for key, value_type, value in [
        ("general.architecture", 8, "gemma3"),
        ("general.alignment", 4, 32),
        ("gemma3.embedding_length", 4, 32),
        ("gemma3.feed_forward_length", 4, 4),
        ("gemma3.block_count", 4, 0),
        ("gemma3.attention.head_count", 4, 1),
        ("gemma3.attention.head_count_kv", 4, 1),
        ("gemma3.attention.key_length", 4, 4),
        ("gemma3.attention.value_length", 4, 4),
        ("gemma3.context_length", 4, 16),
        ("gemma3.rope.freq_base", 6, 1000000.0),
        ("gemma3.attention.sliding_window", 4, 8),
        ("tokenizer.ggml.tokens", 9, (8, tokens)),
        ("tokenizer.ggml.eos_token_id", 4, 1),
        ("tokenizer.ggml.padding_token_id", 4, 0),
    ]:
        write_value(metadata, key, value_type, value)

    tensor_infos = bytearray()
    payload = bytearray()
    tensors = [
        ("output_norm.weight", (4,), GGUF_TYPE_F32, struct.pack("<4f", 0.0, 0.25, -0.5, 1.0)),
        ("token_embd.weight", (4, 32), GGUF_TYPE_Q8_0, None),
    ]
    q8_rows = []
    for row in range(4):
        block = bytearray(f16(0.5))
        block.extend(struct.pack("<32b", *([row + 1, -row - 1, 2, -2] + [0] * 28)))
        q8_rows.append(bytes(block))
    tensors[1] = (tensors[1][0], tensors[1][1], tensors[1][2], b"".join(q8_rows))
    for name, shape, ggml_type, data in tensors:
        offset = len(payload)
        payload.extend(data)
        write_string(tensor_infos, name)
        dims = tuple(reversed(shape))
        tensor_infos.extend(struct.pack("<I", len(dims)))
        tensor_infos.extend(struct.pack("<" + "Q" * len(dims), *dims))
        tensor_infos.extend(struct.pack("<IQ", ggml_type, offset))

    header = bytearray(b"GGUF")
    header.extend(struct.pack("<IQQ", 3, len(tensors), 15))
    raw = header + metadata + tensor_infos
    raw.extend(b"\0" * ((32 - len(raw) % 32) % 32))
    raw.extend(payload)
    path.write_bytes(raw)


class ExportGemmaBmoqTests(unittest.TestCase):
    def test_export_is_aligned_deterministic_and_manifested(self) -> None:
        config = tiny_config()
        source = SyntheticSource(config)
        with tempfile.TemporaryDirectory() as tmp:
            first = Path(tmp) / "a.bmoq"
            second = Path(tmp) / "b.bmoq"
            summary = write_bmoq(source, first, group_size=4)
            write_bmoq(source, second, group_size=4)
            self.assertEqual(first.read_bytes(), second.read_bytes())
            self.assertEqual(summary["tensor_count"], len(entries(first)))

            raw = first.read_bytes()
            self.assertEqual(struct.unpack_from("<I", raw, 32)[0], ARCH_GEMMA3)
            self.assertEqual(struct.unpack_from("<I", raw, 40)[0], config.hidden_size)
            self.assertEqual(struct.unpack_from("<I", raw, 60)[0], config.sliding_window)
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

            manifest_entry = by_id[MANIFEST_TENSOR_ID]
            manifest = json.loads(raw[manifest_entry["offset"] : manifest_entry["offset"] + manifest_entry["bytes"]])
            self.assertEqual(manifest["architecture"], "gemma3")
            self.assertEqual(manifest["config"]["head_dim"], 4)
            self.assertTrue(manifest["config"]["tie_word_embeddings"])

    def test_q8_0_gguf_source_reads_real_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tiny.gguf"
            write_tiny_gguf(path)
            source = GgufSource(path)
            self.assertEqual(source.shape("token_embd.weight"), (4, 32))
            self.assertEqual(source.values_floats("output_norm.weight"), [0.0, 0.25, -0.5, 1.0])
            self.assertEqual(source.row_floats("token_embd.weight", 2)[:4], [1.5, -1.5, 1.0, -1.0])

    def test_real_metadata_shape_can_be_inspected_if_present(self) -> None:
        path = Path("/Volumes/2TB/neatobmo-models/gemma-3-270m-q8_0/gemma-3-270m-Q8_0.gguf")
        if not path.exists():
            self.skipTest("real Gemma GGUF not present")
        source = GgufSource(path)
        config = GemmaConfig.from_metadata(source.metadata, 32)
        self.assertEqual(config.hidden_size, 640)
        self.assertEqual(config.num_hidden_layers, 18)
        self.assertEqual(config.num_attention_heads, 4)
        self.assertEqual(config.num_key_value_heads, 1)
        self.assertEqual(config.head_dim, 256)
        self.assertEqual({tensor.ggml_type for tensor in source.tensors.values()}, {GGUF_TYPE_F32, GGUF_TYPE_Q8_0})
        for tensor in expected_logical_tensors(config):
            self.assertEqual(source.shape(tensor.gguf_name), tensor.shape)


if __name__ == "__main__":
    unittest.main()
