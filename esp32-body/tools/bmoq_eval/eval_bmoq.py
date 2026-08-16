#!/usr/bin/env python3
"""Run or adapt a host BMOQ evaluator into benchmark JSONL records."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

try:
    from .schema import read_jsonl, validate_eval_record, write_jsonl
except ImportError:  # pragma: no cover - direct script execution
    from schema import read_jsonl, validate_eval_record, write_jsonl


def copy_validated_jsonl(input_path: Path, output_path: Path) -> None:
    records = []
    for record in read_jsonl(input_path):
        validate_eval_record(record, input_path)
        records.append(record)
    write_jsonl(output_path, records)


def run_host(
    executable: Path,
    model: Path,
    tokenizer: Path,
    benchmark: Path,
    output: Path,
    dump_logits: bool,
    dump_routing: bool,
) -> None:
    command = [
        str(executable),
        "--model",
        str(model),
        "--tokenizer",
        str(tokenizer),
        "--input",
        str(benchmark),
        "--output",
        str(output),
        "--teacher-force",
    ]
    if dump_logits:
        command.append("--dump-logits")
    if dump_routing:
        command.append("--dump-routing")
    subprocess.run(command, check=True)
    for record in read_jsonl(output):
        validate_eval_record(record, output)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path)
    parser.add_argument("--tokenizer", type=Path)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--executable", type=Path, help="host bmoq-eval executable")
    parser.add_argument("--fixture-results", type=Path, help="validated synthetic BMOQ JSONL for tests")
    parser.add_argument("--dump-logits", action="store_true")
    parser.add_argument("--dump-routing", action="store_true")
    args = parser.parse_args()

    if args.fixture_results:
        copy_validated_jsonl(args.fixture_results, args.output)
        return
    if not args.executable:
        print("--executable is required unless --fixture-results is used", file=sys.stderr)
        raise SystemExit(2)
    if not args.model or not args.tokenizer:
        print("--model and --tokenizer are required for host execution", file=sys.stderr)
        raise SystemExit(2)
    run_host(args.executable, args.model, args.tokenizer, args.input, args.output, args.dump_logits, args.dump_routing)


if __name__ == "__main__":
    main()

