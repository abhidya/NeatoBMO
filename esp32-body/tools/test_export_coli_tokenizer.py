#!/usr/bin/env python3

from __future__ import annotations

import struct
import unittest

from export_coli_tokenizer import (
    PRETOKENIZER_KIMI_K3,
    build_ctok,
    bytes_to_unicode,
)


class ExportColiTokenizerTests(unittest.TestCase):
    def test_kimi_no_merges_exports_rank_bpe_and_family(self) -> None:
        byte_map = bytes_to_unicode()
        vocab = {piece: token_id for token_id, piece in enumerate(byte_map.values())}
        vocab["<pad>"] = 256
        vocab["<eos>"] = 257
        vocab["ab"] = 258
        vocab["abc"] = 259

        ctok = build_ctok(
            {
                "added_tokens": [
                    {"id": 256, "content": "<pad>", "special": True},
                    {"id": 257, "content": "<eos>", "special": True},
                ],
                "pre_tokenizer": {
                    "pretokenizers": [
                        {"pattern": {"Regex": r"[\p{Han}]+|[\p{Lu}\p{Ll}]+"}}
                    ]
                },
                "model": {"type": "BPE", "vocab": vocab, "merges": []},
            }
        )

        self.assertEqual(struct.unpack_from("<H", ctok, 62)[0], PRETOKENIZER_KIMI_K3)
        merge_count = struct.unpack_from("<I", ctok, 12)[0]
        merge_offset = struct.unpack_from("<Q", ctok, 40)[0]
        records = [
            struct.unpack_from("<IIII", ctok, merge_offset + i * 16)
            for i in range(merge_count)
        ]
        self.assertIn((vocab["a"], vocab["b"], vocab["ab"], vocab["ab"]), records)
        self.assertIn((vocab["ab"], vocab["c"], vocab["abc"], vocab["abc"]), records)


if __name__ == "__main__":
    unittest.main()
