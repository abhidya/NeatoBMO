#!/usr/bin/env python3
"""Build a deterministic pre-tokenized benchmark corpus for paired BMOQ evaluation.

The corpus is written once, as explicit token ids, so the BF16 reference runner
and the BMOQ C runner consume byte-identical inputs. Neither runner re-tokenizes
anything, which removes the only silent way the two sides could drift apart.

Default source is the WikiText-2 raw *test* split: a public, held-out, widely
used language-model perplexity corpus that is not prompt-shaped or synthetic.

Text is concatenated in dataset order and chunked into fixed-length windows.
Every window of length L contributes L-1 teacher-forced target tokens (position
0 has no prediction to score), and a trailing partial window is dropped rather
than padded, so no padding token ever enters a perplexity average.

Writes two files:
  <output>            JSONL, one record per window, carrying `tokens`
  <output>.meta.json  provenance: corpus/tokenizer/checkpoint identity, hashes,
                      truncation and sequence-length policy, library versions
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
import sys
from pathlib import Path
from typing import Any

try:
    from .schema import write_jsonl
except ImportError:  # pragma: no cover - direct script execution
    from schema import write_jsonl


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def tool_commit() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            cwd=str(Path(__file__).resolve().parent),
        ).stdout.strip()
    except Exception:  # pragma: no cover - git optional
        return ""


def load_source_text(args: argparse.Namespace) -> tuple[str, dict[str, Any]]:
    if args.text_file:
        text = Path(args.text_file).read_text(encoding="utf-8")
        return text, {
            "corpus_id": f"file:{args.text_file}",
            "corpus_revision": "",
            "corpus_split": "",
        }
    from datasets import load_dataset

    dataset = load_dataset(args.dataset, args.dataset_config, split=args.split)
    text = "\n\n".join(dataset[args.text_column])
    return text, {
        "corpus_id": f"{args.dataset}/{args.dataset_config}",
        "corpus_revision": args.dataset_revision or "",
        "corpus_split": args.split,
        "corpus_rows": len(dataset),
    }


def library_versions() -> dict[str, str]:
    versions: dict[str, str] = {"python": sys.version.split()[0]}
    for module in ("torch", "transformers", "datasets", "numpy", "tokenizers"):
        try:
            versions[module] = __import__(module).__version__
        except Exception:  # pragma: no cover
            versions[module] = "absent"
    return versions


def build(args: argparse.Namespace) -> dict[str, Any]:
    from transformers import AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(args.tokenizer, revision=args.revision)
    text, corpus_meta = load_source_text(args)

    encoded = tokenizer(text, add_special_tokens=args.add_special_tokens)["input_ids"]
    window = args.sequence_length
    windows = len(encoded) // window
    if args.max_target_tokens > 0:
        needed = -(-args.max_target_tokens // (window - 1))
        windows = min(windows, needed)
    if windows == 0:
        raise SystemExit("corpus too short for one full window")

    records = []
    targets = 0
    for index in range(windows):
        chunk = encoded[index * window : (index + 1) * window]
        records.append(
            {
                "sample_id": f"{args.sample_prefix}-{index:05d}",
                "category": args.category,
                "tokens": [int(value) for value in chunk],
            }
        )
        targets += len(chunk) - 1

    output = Path(args.output)
    write_jsonl(output, records)

    meta = {
        "schema_version": 1,
        "record_type": "corpus_meta",
        "benchmark_jsonl": str(output),
        "benchmark_sha256": sha256_file(output),
        "checkpoint_id": args.tokenizer,
        "checkpoint_revision": args.revision or "main",
        "tokenizer_id": args.tokenizer,
        "tokenizer_vocab_size": int(tokenizer.vocab_size),
        "tokenizer_signature_sha256": sha256_text(
            json.dumps(
                {
                    "name": tokenizer.__class__.__name__,
                    "vocab_size": int(tokenizer.vocab_size),
                    "probe": tokenizer(
                        "BMOQ determinism probe 0123456789",
                        add_special_tokens=args.add_special_tokens,
                    )["input_ids"],
                },
                sort_keys=True,
            )
        ),
        "source_text_sha256": sha256_text(text),
        "source_text_characters": len(text),
        "total_tokens": len(encoded),
        "sequence_length": window,
        "windows": windows,
        "target_tokens": targets,
        "add_special_tokens": bool(args.add_special_tokens),
        "truncation_policy": "drop trailing partial window; no padding",
        "sequence_length_policy": f"fixed non-overlapping windows of {window} tokens",
        "seed": None,
        "deterministic": "no sampling; dataset order preserved",
        "tool_commit": tool_commit(),
        "host": {
            "platform": platform.platform(),
            "machine": platform.machine(),
            "processor": platform.processor(),
        },
        "libraries": library_versions(),
        **corpus_meta,
    }
    meta_path = output.with_suffix(output.suffix + ".meta.json")
    meta_path.write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return meta


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tokenizer", default="allenai/OLMoE-1B-7B-0924")
    parser.add_argument("--revision", default=None)
    parser.add_argument("--dataset", default="Salesforce/wikitext")
    parser.add_argument("--dataset-config", default="wikitext-2-raw-v1")
    parser.add_argument("--dataset-revision", default=None)
    parser.add_argument("--split", default="test")
    parser.add_argument("--text-column", default="text")
    parser.add_argument("--text-file", default=None, help="use a local text file instead")
    parser.add_argument("--sequence-length", type=int, default=1024)
    parser.add_argument(
        "--max-target-tokens",
        type=int,
        default=0,
        help="stop after roughly this many evaluated target tokens; 0 uses all",
    )
    parser.add_argument("--sample-prefix", default="wikitext2-test")
    parser.add_argument("--category", default="wikitext2-raw-test")
    parser.add_argument("--add-special-tokens", action="store_true")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    meta = build(args)
    print(
        f"{meta['benchmark_jsonl']}: windows={meta['windows']} "
        f"target_tokens={meta['target_tokens']} "
        f"sha256={meta['benchmark_sha256']}"
    )


if __name__ == "__main__":
    main()
