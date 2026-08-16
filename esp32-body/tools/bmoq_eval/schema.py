#!/usr/bin/env python3
"""Shared JSONL schema helpers for paired BMOQ quantization evaluation."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


SCHEMA_VERSION = 1


@dataclass(frozen=True)
class EvalRecord:
    sample_id: str
    position: int
    token_id: int
    nll: float
    logits: list[float]
    logit_top_k: list[dict[str, Any]]
    category: str
    sequence_length: int
    variant: dict[str, Any]
    router: list[dict[str, Any]]


def read_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as file:
        for line_number, line in enumerate(file, 1):
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: invalid JSONL") from exc


def write_jsonl(path: Path, records: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        for record in records:
            file.write(json.dumps(record, sort_keys=True, separators=(",", ":")))
            file.write("\n")


def load_eval_records(path: Path) -> list[EvalRecord]:
    records: list[EvalRecord] = []
    for raw in read_jsonl(path):
        records.append(validate_eval_record(raw, path))
    return records


def validate_eval_record(raw: dict[str, Any], path: Path | None = None) -> EvalRecord:
    where = f"{path}: " if path else ""
    if raw.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"{where}schema_version must be {SCHEMA_VERSION}")
    if raw.get("record_type") != "token_eval":
        raise ValueError(f"{where}record_type must be token_eval")
    required = [
        "sample_id",
        "position",
        "token_id",
        "nll",
        "logits",
        "category",
        "sequence_length",
        "variant",
        "router",
    ]
    for key in required:
        if key not in raw:
            raise ValueError(f"{where}missing {key}")
    logits = raw["logits"]
    logit_top_k = raw.get("logit_top_k", [])
    if not isinstance(logits, list):
        raise ValueError(f"{where}logits must be a list")
    if not isinstance(logit_top_k, list):
        raise ValueError(f"{where}logit_top_k must be a list")
    if not logits and not logit_top_k:
        raise ValueError(f"{where}logits or logit_top_k must be non-empty")
    for entry in logit_top_k:
        if "token_id" not in entry or "logit" not in entry:
            raise ValueError(f"{where}logit_top_k entries require token_id and logit")
    router = raw["router"]
    if not isinstance(router, list):
        raise ValueError(f"{where}router must be a list")
    for layer in router:
        if "layer" not in layer or "top_experts" not in layer or "weights" not in layer:
            raise ValueError(f"{where}router entries require layer, top_experts, weights")
        if len(layer["top_experts"]) != len(layer["weights"]):
            raise ValueError(f"{where}router expert and weight lengths differ")
    return EvalRecord(
        sample_id=str(raw["sample_id"]),
        position=int(raw["position"]),
        token_id=int(raw["token_id"]),
        nll=float(raw["nll"]),
        logits=[float(value) for value in logits],
        logit_top_k=[
            {"token_id": int(entry["token_id"]), "logit": float(entry["logit"])}
            for entry in logit_top_k
        ],
        category=str(raw["category"]),
        sequence_length=int(raw["sequence_length"]),
        variant=dict(raw["variant"]),
        router=list(router),
    )


def record_key(record: EvalRecord) -> tuple[str, int, int]:
    return record.sample_id, record.position, record.token_id
