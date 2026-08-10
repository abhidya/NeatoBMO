#!/usr/bin/env python3

from __future__ import annotations

import struct
import tempfile
import unittest
from pathlib import Path

from export_gemma_tokenizer import (
    EDGE_BYTES,
    ENTRY_BYTES,
    HEADER_BYTES,
    NODE_BYTES,
    TYPE_BYTE,
    TYPE_CONTROL,
    TYPE_NORMAL,
    GemmaTokenizerMetadata,
    build_cspm,
    inspect_summary,
    metadata_from_gguf,
)


def tiny_metadata() -> GemmaTokenizerMetadata:
    tokens = [
        "<pad>",
        "<eos>",
        "<bos>",
        "<unk>",
        "a",
        "b",
        "ab",
        "\u2581hi",
    ] + [f"<0x{i:02X}>" for i in range(256)]
    scores = [-1000.0, -1000.0, -1000.0, -1000.0, -1.0, -1.0, -0.1, -0.2] + [0.0] * 256
    token_types = [TYPE_CONTROL, TYPE_CONTROL, TYPE_CONTROL, TYPE_CONTROL]
    token_types += [TYPE_NORMAL, TYPE_NORMAL, TYPE_NORMAL, TYPE_NORMAL]
    token_types += [TYPE_BYTE] * 256
    return GemmaTokenizerMetadata(
        tokens=tokens,
        scores=scores,
        token_types=token_types,
        bos_token_id=2,
        eos_token_id=1,
        pad_token_id=0,
        unk_token_id=3,
        add_bos_token=True,
        add_eos_token=False,
        add_space_prefix=False,
    )


class ExportGemmaTokenizerTests(unittest.TestCase):
    def test_cspm_header_and_offsets_are_deterministic(self) -> None:
        metadata = tiny_metadata()
        first = build_cspm(metadata)
        second = build_cspm(metadata)
        self.assertEqual(first, second)
        self.assertEqual(first[:4], b"CSPM")
        self.assertEqual(struct.unpack_from("<H", first, 4)[0], 1)
        self.assertEqual(struct.unpack_from("<H", first, 6)[0], HEADER_BYTES)
        vocab_size, node_count, edge_count = struct.unpack_from("<III", first, 8)
        self.assertEqual(vocab_size, len(metadata.tokens))
        self.assertGreater(node_count, 1)
        self.assertGreater(edge_count, 1)
        self.assertEqual(struct.unpack_from("<III", first, 20), (ENTRY_BYTES, NODE_BYTES, EDGE_BYTES))
        entry_offset, node_offset, edge_offset, piece_offset = struct.unpack_from("<4Q", first, 32)
        self.assertEqual(entry_offset, HEADER_BYTES)
        self.assertEqual(node_offset, HEADER_BYTES + len(metadata.tokens) * ENTRY_BYTES)
        self.assertEqual(edge_offset, node_offset + node_count * NODE_BYTES)
        self.assertEqual(piece_offset, edge_offset + edge_count * EDGE_BYTES)

    def test_summary_reports_real_metadata_if_present(self) -> None:
        path = Path("/Volumes/2TB/neatobmo-models/gemma-3-270m-q8_0/gemma-3-270m-Q8_0.gguf")
        if not path.exists():
            self.skipTest("real Gemma GGUF not present")
        metadata = metadata_from_gguf(path)
        cspm = build_cspm(metadata)
        summary = inspect_summary(metadata, cspm)
        self.assertEqual(summary["vocab_size"], 262144)
        self.assertEqual(summary["bos_token_id"], 2)
        self.assertEqual(summary["eos_token_id"], 1)
        self.assertEqual(summary["pad_token_id"], 0)
        self.assertEqual(summary["unk_token_id"], 3)
        self.assertEqual(summary["token_type_counts"][TYPE_BYTE], 256)
        self.assertLess(summary["max_piece_bytes"], 64)


if __name__ == "__main__":
    unittest.main()
