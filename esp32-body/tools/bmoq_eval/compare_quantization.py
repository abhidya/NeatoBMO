#!/usr/bin/env python3
"""Compare BF16 reference records against BMOQ candidate records."""

from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

try:
    from .schema import EvalRecord, read_jsonl, record_key, validate_eval_record
except ImportError:  # pragma: no cover - direct script execution
    from schema import EvalRecord, read_jsonl, record_key, validate_eval_record


EPSILON = 1.0e-12


def softmax(logits: list[float]) -> list[float]:
    peak = max(logits)
    values = [math.exp(value - peak) for value in logits]
    total = sum(values)
    return [value / total for value in values]


def top_indices(values: list[float], k: int) -> list[int]:
    return sorted(range(len(values)), key=lambda index: (-values[index], index))[:k]


def top_token_ids(record: EvalRecord, k: int) -> list[int]:
    if record.logits:
        return top_indices(record.logits, min(k, len(record.logits)))
    return [int(entry["token_id"]) for entry in record.logit_top_k[:k]]


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    left = math.sqrt(sum(x * x for x in a))
    right = math.sqrt(sum(y * y for y in b))
    if left == 0.0 or right == 0.0:
        return 0.0
    return dot / (left * right)


def kl_divergence(reference_logits: list[float], candidate_logits: list[float]) -> float:
    reference = softmax(reference_logits)
    candidate = softmax(candidate_logits)
    return sum(p * math.log(max(p, EPSILON) / max(q, EPSILON)) for p, q in zip(reference, candidate))


def router_metrics(reference: EvalRecord, candidate: EvalRecord) -> list[dict[str, Any]]:
    by_layer = {int(layer["layer"]): layer for layer in candidate.router}
    result = []
    for ref_layer in reference.router:
        layer_index = int(ref_layer["layer"])
        cand_layer = by_layer.get(layer_index)
        if cand_layer is None:
            result.append({"layer": layer_index, "overlap": 0.0, "weight_mae": None})
            continue
        ref_experts = [int(value) for value in ref_layer["top_experts"]]
        cand_experts = [int(value) for value in cand_layer["top_experts"]]
        denom = max(len(ref_experts), 1)
        overlap = len(set(ref_experts) & set(cand_experts)) / denom
        ref_weights = {int(expert): float(weight) for expert, weight in zip(ref_experts, ref_layer["weights"])}
        cand_weights = {int(expert): float(weight) for expert, weight in zip(cand_experts, cand_layer["weights"])}
        union = sorted(set(ref_weights) | set(cand_weights))
        mae = sum(abs(ref_weights.get(expert, 0.0) - cand_weights.get(expert, 0.0)) for expert in union)
        mae = mae / len(union) if union else 0.0
        result.append({"layer": layer_index, "overlap": overlap, "weight_mae": mae})
    return result


def compare_pair(reference: EvalRecord, candidate: EvalRecord) -> dict[str, Any]:
    if reference.logits and candidate.logits and len(reference.logits) != len(candidate.logits):
        raise ValueError(f"{record_key(reference)}: logits length mismatch")
    ref_top1 = top_token_ids(reference, 1)[0]
    cand_top1 = top_token_ids(candidate, 1)[0]
    ref_top5 = set(top_token_ids(reference, 5))
    cand_top5 = set(top_token_ids(candidate, 5))
    top5_denominator = max(len(ref_top5), 1)
    route = router_metrics(reference, candidate)
    has_full_logits = bool(reference.logits and candidate.logits)
    return {
        "sample_id": reference.sample_id,
        "position": reference.position,
        "token_id": reference.token_id,
        "category": reference.category,
        "sequence_length": reference.sequence_length,
        "delta_nll": candidate.nll - reference.nll,
        "reference_nll": reference.nll,
        "candidate_nll": candidate.nll,
        "top1_agreement": 1.0 if ref_top1 == cand_top1 else 0.0,
        "top5_overlap": len(ref_top5 & cand_top5) / top5_denominator,
        "kl": kl_divergence(reference.logits, candidate.logits) if has_full_logits else None,
        "cosine": cosine(reference.logits, candidate.logits) if has_full_logits else None,
        "router": route,
    }


def mean(values: Iterable[float]) -> float | None:
    materialized = list(values)
    if not materialized:
        return None
    return sum(materialized) / len(materialized)


class MetricAggregate:
    def __init__(self) -> None:
        self.count = 0
        self.reference_nll = 0.0
        self.candidate_nll = 0.0
        self.top1_agreement = 0.0
        self.top5_overlap = 0.0
        self.kl = 0.0
        self.kl_count = 0
        self.cosine = 0.0
        self.cosine_count = 0
        self.router_overlap = 0.0
        self.router_overlap_count = 0
        self.router_weight_mae = 0.0
        self.router_weight_mae_count = 0

    def add(self, value: dict[str, Any], router_rows: list[dict[str, Any]] | None = None) -> None:
        self.count += 1
        self.reference_nll += value["reference_nll"]
        self.candidate_nll += value["candidate_nll"]
        self.top1_agreement += value["top1_agreement"]
        self.top5_overlap += value["top5_overlap"]
        if value["kl"] is not None:
            self.kl += value["kl"]
            self.kl_count += 1
        if value["cosine"] is not None:
            self.cosine += value["cosine"]
            self.cosine_count += 1
        for row in router_rows if router_rows is not None else value["router"]:
            self.router_overlap += row["overlap"]
            self.router_overlap_count += 1
            if row["weight_mae"] is not None:
                self.router_weight_mae += row["weight_mae"]
                self.router_weight_mae_count += 1

    def summary(self) -> dict[str, Any]:
        if self.count == 0:
            return {"count": 0}
        reference_nll = self.reference_nll / self.count
        candidate_nll = self.candidate_nll / self.count
        reference_ppl = math.exp(reference_nll)
        candidate_ppl = math.exp(candidate_nll)
        return {
            "count": self.count,
            "reference_nll": reference_nll,
            "candidate_nll": candidate_nll,
            "delta_nll": candidate_nll - reference_nll,
            "reference_perplexity": reference_ppl,
            "candidate_perplexity": candidate_ppl,
            "perplexity_increase_pct": ((candidate_ppl / reference_ppl) - 1.0) * 100.0,
            "top1_agreement": self.top1_agreement / self.count,
            "top5_overlap": self.top5_overlap / self.count,
            "kl": self.kl / self.kl_count if self.kl_count else None,
            "cosine": self.cosine / self.cosine_count if self.cosine_count else None,
            "router_overlap": self.router_overlap / self.router_overlap_count
            if self.router_overlap_count
            else None,
            "router_weight_mae": self.router_weight_mae / self.router_weight_mae_count
            if self.router_weight_mae_count
            else None,
        }


class ExpertAggregate:
    def __init__(self) -> None:
        self.count = 0
        self.router_presence_agreement = 0.0

    def add(self, matched: bool) -> None:
        self.count += 1
        self.router_presence_agreement += 1.0 if matched else 0.0

    def summary(self) -> dict[str, Any]:
        return {
            "count": self.count,
            "router_presence_agreement": self.router_presence_agreement / self.count
            if self.count
            else None,
        }


class OnlineReport:
    def __init__(self) -> None:
        self.overall = MetricAggregate()
        self.prompt_category: dict[str, MetricAggregate] = defaultdict(MetricAggregate)
        self.token_position: dict[str, MetricAggregate] = defaultdict(MetricAggregate)
        self.sequence_length: dict[str, MetricAggregate] = defaultdict(MetricAggregate)
        self.tensor_precision: dict[str, MetricAggregate] = defaultdict(MetricAggregate)
        self.layer: dict[str, MetricAggregate] = defaultdict(MetricAggregate)
        self.expert: dict[str, ExpertAggregate] = defaultdict(ExpertAggregate)
        self.top1_same = 0

    def add(self, pair: dict[str, Any], reference: EvalRecord, candidate: EvalRecord) -> None:
        tensor_precision = str(candidate.variant.get("tensor_precision", "unknown"))
        self.overall.add(pair)
        self.prompt_category[str(pair["category"])].add(pair)
        self.token_position[str(pair["position"])].add(pair)
        self.sequence_length[str(pair["sequence_length"])].add(pair)
        self.tensor_precision[tensor_precision].add(pair)
        self.top1_same += int(pair["top1_agreement"])
        for row in pair["router"]:
            self.layer[str(row["layer"])].add(pair, [row])
        cand_layers = {int(row["layer"]): row for row in candidate.router}
        for ref_layer in reference.router:
            layer = int(ref_layer["layer"])
            cand_layer = cand_layers.get(layer)
            if cand_layer is None:
                continue
            cand_experts = {int(expert) for expert in cand_layer["top_experts"]}
            for expert in ref_layer["top_experts"]:
                key = f"l{layer}:e{int(expert)}"
                self.expert[key].add(int(expert) in cand_experts)

    def by(self) -> dict[str, Any]:
        return {
            "prompt_category": summarize_map(self.prompt_category),
            "token_position": summarize_map(self.token_position),
            "sequence_length": summarize_map(self.sequence_length),
            "tensor_precision": summarize_map(self.tensor_precision),
            "layer": summarize_map(self.layer, numeric_keys=True),
            "expert": {key: agg.summary() for key, agg in sorted(self.expert.items())},
        }


def summarize_map(groups: dict[str, MetricAggregate], numeric_keys: bool = False) -> dict[str, Any]:
    if numeric_keys:
        ordered = sorted(groups.items(), key=lambda item: int(item[0]))
    else:
        ordered = sorted(groups.items())
    return {key: aggregate.summary() for key, aggregate in ordered}


def stream_records(path: Path) -> Iterable[EvalRecord]:
    for raw in read_jsonl(path):
        yield validate_eval_record(raw, path)


def assert_stable_variant(kind: str, expected: dict[str, Any] | None, record: EvalRecord) -> dict[str, Any]:
    if expected is None:
        return record.variant
    if record.variant != expected:
        raise ValueError(f"{kind} variant changed at {record_key(record)}")
    return expected


def build_report(reference_path: Path, candidate_path: Path) -> dict[str, Any]:
    reference_iter = stream_records(reference_path)
    candidate_iter = stream_records(candidate_path)
    seen_reference: set[tuple[str, int, int]] = set()
    seen_candidate: set[tuple[str, int, int]] = set()
    reference_variant: dict[str, Any] | None = None
    candidate_variant: dict[str, Any] | None = None
    aggregates = OnlineReport()

    count = 0
    while True:
        reference_record = next(reference_iter, None)
        candidate_record = next(candidate_iter, None)
        if reference_record is None and candidate_record is None:
            break
        if reference_record is None:
            raise ValueError(f"candidate has extra record {record_key(candidate_record)}")
        if candidate_record is None:
            raise ValueError(f"candidate missing record {record_key(reference_record)}")
        reference_key = record_key(reference_record)
        candidate_key = record_key(candidate_record)
        if reference_key in seen_reference:
            raise ValueError(f"reference contains duplicate record key {reference_key}")
        if candidate_key in seen_candidate:
            raise ValueError(f"candidate contains duplicate record key {candidate_key}")
        seen_reference.add(reference_key)
        seen_candidate.add(candidate_key)
        if reference_key != candidate_key:
            raise ValueError(f"misaligned records: reference {reference_key} candidate {candidate_key}")
        reference_variant = assert_stable_variant("reference", reference_variant, reference_record)
        candidate_variant = assert_stable_variant("candidate", candidate_variant, candidate_record)
        aggregates.add(compare_pair(reference_record, candidate_record), reference_record, candidate_record)
        count += 1

    reference_variant = reference_variant or {}
    candidate_variant = candidate_variant or {}
    return {
        "schema_version": 1,
        "comparison": "bf16_reference_vs_bmoq_candidate",
        "reference": {"path": str(reference_path), "variant": reference_variant},
        "candidate": {"path": str(candidate_path), "variant": candidate_variant},
        "overall": aggregates.overall.summary(),
        "by": aggregates.by(),
        "paired_categorical": {
            "tokens_compared": count,
            "top1_same": aggregates.top1_same,
            "top1_changed": count - aggregates.top1_same,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", required=True, type=Path)
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()

    report = build_report(args.reference, args.candidate)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
