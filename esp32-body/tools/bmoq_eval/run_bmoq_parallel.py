#!/usr/bin/env python3
"""Run the exact BMOQ C evaluator over a benchmark corpus using N worker processes.

Parallelism is at the sample level, never inside a kernel. Each benchmark
window is an independent teacher-forced sequence with its own KV cache, so
sharding windows across processes cannot change any arithmetic: every token is
produced by the same code, in the same order, from the same weights. The merged
output is byte-comparable with a single-process run over the same corpus.

This is what makes a real-checkpoint run finishable. A single process evaluates
OLMoE-1B-7B at roughly 3.3 s/token on this class of host, so a 50k-token corpus
costs ~46 h serially and ~8 h across six workers.

Full-logit sampling is keyed on token position, not on a per-process counter,
so the same records carry full distributions regardless of worker count.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

try:
    from .schema import RUN_META_RECORD, read_jsonl
except ImportError:  # pragma: no cover - direct script execution
    from schema import RUN_META_RECORD, read_jsonl


def shard_benchmark(benchmark: Path, workers: int, directory: Path) -> tuple[list[Path], list[str]]:
    """Round-robin windows across shards, returning shard paths and sample order."""
    order: list[str] = []
    handles = [(directory / f"shard-{index:03d}.jsonl").open("w", encoding="utf-8") for index in range(workers)]
    try:
        index = 0
        with benchmark.open("r", encoding="utf-8") as source:
            for line in source:
                line = line.strip()
                if not line:
                    continue
                record = json.loads(line)
                if record.get("record_type") == RUN_META_RECORD:
                    continue
                order.append(str(record["sample_id"]))
                handles[index % workers].write(line + "\n")
                index += 1
    finally:
        for handle in handles:
            handle.close()
    paths = [directory / f"shard-{index:03d}.jsonl" for index in range(workers)]
    return [path for path in paths if path.stat().st_size > 0], order


def worker_command(args: argparse.Namespace, shard: Path, output: Path) -> list[str]:
    command = [
        str(args.executable),
        "--model",
        str(args.model),
        "--tokenizer",
        str(args.tokenizer),
        "--input",
        str(shard),
        "--output",
        str(output),
        "--teacher-force",
        "--logit-top-k",
        str(args.logit_top_k),
        "--full-logit-stride",
        str(args.full_logit_stride),
        "--variant-name",
        args.variant_name,
    ]
    if args.corpus_sha256:
        command += ["--corpus-sha256", args.corpus_sha256]
    if args.tool_commit:
        command += ["--tool-commit", args.tool_commit]
    if args.workspace_bytes:
        command += ["--workspace-bytes", str(args.workspace_bytes)]
    if args.dump_routing:
        command.append("--dump-routing")
    return command


class ShardReader:
    """Streams one shard's token records, grouped by sample id in file order."""

    def __init__(self, path: Path) -> None:
        self.handle = path.open("r", encoding="utf-8")
        self.path = path
        self.run_meta: dict[str, Any] | None = None
        self.pending: dict[str, Any] | None = None
        self._advance()

    def _advance(self) -> None:
        for line in self.handle:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            if record.get("record_type") == RUN_META_RECORD:
                self.run_meta = record
                continue
            self.pending = record
            return
        self.pending = None

    def take_sample(self, sample_id: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        while self.pending is not None and str(self.pending["sample_id"]) == sample_id:
            rows.append(self.pending)
            self._advance()
        return rows

    def close(self) -> None:
        self.handle.close()


def merge(shard_outputs: list[Path], order: list[str], output: Path) -> int:
    readers = [ShardReader(path) for path in shard_outputs]
    written = 0
    try:
        run_meta = next((reader.run_meta for reader in readers if reader.run_meta), None)
        with output.open("w", encoding="utf-8") as sink:
            if run_meta is not None:
                sink.write(json.dumps(run_meta, sort_keys=True, separators=(",", ":")) + "\n")
            for sample_id in order:
                rows: list[dict[str, Any]] = []
                for reader in readers:
                    rows = reader.take_sample(sample_id)
                    if rows:
                        break
                if not rows:
                    raise ValueError(f"no worker produced records for sample {sample_id}")
                for row in rows:
                    sink.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
                    written += 1
        for reader in readers:
            if reader.pending is not None:
                raise ValueError(f"{reader.path}: unconsumed records after merge")
    finally:
        for reader in readers:
            reader.close()
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--executable", required=True, type=Path)
    parser.add_argument("--model", required=True, type=Path)
    parser.add_argument("--tokenizer", required=True, type=Path)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--logit-top-k", type=int, default=32)
    parser.add_argument("--full-logit-stride", type=int, default=0)
    parser.add_argument("--variant-name", default="bmoq-host")
    parser.add_argument("--corpus-sha256", default=None)
    parser.add_argument("--tool-commit", default=None)
    parser.add_argument("--workspace-bytes", type=int, default=0)
    parser.add_argument("--dump-routing", action="store_true")
    parser.add_argument("--keep-shards", type=Path, default=None)
    args = parser.parse_args()

    workspace = Path(args.keep_shards) if args.keep_shards else Path(tempfile.mkdtemp(prefix="bmoq-shards-"))
    workspace.mkdir(parents=True, exist_ok=True)
    try:
        shards, order = shard_benchmark(args.input, max(1, args.workers), workspace)
        print(f"sharded {len(order)} windows across {len(shards)} workers", flush=True)
        processes = []
        outputs = []
        for index, shard in enumerate(shards):
            shard_output = workspace / f"result-{index:03d}.jsonl"
            outputs.append(shard_output)
            processes.append(subprocess.Popen(worker_command(args, shard, shard_output)))
        failures = [index for index, process in enumerate(processes) if process.wait() != 0]
        if failures:
            raise SystemExit(f"workers failed: {failures}")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        written = merge(outputs, order, args.output)
        print(f"{args.output}: {written} token records from {len(shards)} workers")
    finally:
        if not args.keep_shards:
            shutil.rmtree(workspace, ignore_errors=True)


if __name__ == "__main__":
    main()
