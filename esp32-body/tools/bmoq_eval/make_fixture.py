#!/usr/bin/env python3
"""Write a deterministic tiny paired benchmark fixture."""

from __future__ import annotations

import argparse
import math
from pathlib import Path

try:
    from .compare_quantization import build_report
    from .eval_hf_olmoe import log_softmax_nll
    from .schema import SCHEMA_VERSION, write_jsonl
except ImportError:  # pragma: no cover - direct script execution
    from compare_quantization import build_report
    from eval_hf_olmoe import log_softmax_nll
    from schema import SCHEMA_VERSION, write_jsonl


def records(variant: str) -> list[dict]:
    rows = []
    samples = [
        ("hello", "bmo-cue", [0, 1, 2, 3]),
        ("drive", "motion-safety", [3, 2, 1, 0]),
    ]
    for sample_id, category, tokens in samples:
        for position, token_id in enumerate(tokens[1:], 1):
            base = [0.1 * (index + 1) + position * 0.03 for index in range(6)]
            base[token_id] += 1.1
            logits = base[:]
            tensor_precision = "BF16"
            if variant == "bmoq":
                tensor_precision = "Q4_GROUP32"
                logits = [value - 0.015 + ((index + position) % 3) * 0.01 for index, value in enumerate(base)]
                logits[token_id] -= 0.02
            router = []
            for layer in range(2):
                experts = [layer * 10 + value for value in [0, 1, 2, 3, 4, 5, 6, 7]]
                if variant == "bmoq" and position == 2 and layer == 1:
                    experts = experts[1:] + [layer * 10 + 8]
                weights = [math.exp(-slot / 4.0) for slot in range(8)]
                total = sum(weights)
                weights = [value / total for value in weights]
                if variant == "bmoq":
                    weights = [max(0.0, value + (0.001 if slot % 2 else -0.001)) for slot, value in enumerate(weights)]
                router.append({"layer": layer, "top_experts": experts, "weights": weights})
            rows.append(
                {
                    "schema_version": SCHEMA_VERSION,
                    "record_type": "token_eval",
                    "sample_id": sample_id,
                    "position": position,
                    "token_id": token_id,
                    "nll": log_softmax_nll(logits, token_id),
                    "logits": logits,
                    "category": category,
                    "sequence_length": len(tokens),
                    "variant": {
                        "name": "fixture-" + variant,
                        "model": "synthetic-olmoe-tiny",
                        "tensor_precision": tensor_precision,
                        "quant_group_size": 32 if variant == "bmoq" else None,
                        "runtime": "deterministic-fixture",
                    },
                    "router": router,
                }
            )
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    benchmark = [
        {"schema_version": 1, "sample_id": "hello", "category": "bmo-cue", "prompt": "Say hello and look happy", "tokens": [0, 1, 2, 3]},
        {"schema_version": 1, "sample_id": "drive", "category": "motion-safety", "prompt": "Drive down the stairs", "tokens": [3, 2, 1, 0]},
    ]
    write_jsonl(args.output_dir / "benchmark.jsonl", benchmark)
    write_jsonl(args.output_dir / "bf16-results.jsonl", records("bf16"))
    write_jsonl(args.output_dir / "bmoq-results.jsonl", records("bmoq"))
    report = build_report(args.output_dir / "bf16-results.jsonl", args.output_dir / "bmoq-results.jsonl")
    (args.output_dir / "bmoq-vs-bf16.json").write_text(
        __import__("json").dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()

