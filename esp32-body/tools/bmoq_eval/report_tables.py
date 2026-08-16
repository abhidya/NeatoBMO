#!/usr/bin/env python3
"""Render markdown tables straight from comparison reports.

Numbers in the written report come from this, not from hand transcription, so
a figure in the prose cannot drift from the JSON it claims to summarize.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def fmt(value: Any, digits: int = 4) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def variant_row(name: str, report: dict[str, Any], size_bytes: int | None, base_bytes: int | None) -> str:
    overall = report["overall"]
    size = f"{size_bytes / 1e9:.2f} GB" if size_bytes else "n/a"
    ratio = f"{base_bytes / size_bytes:.2f}x" if size_bytes and base_bytes else "n/a"
    return " | ".join(
        [
            name,
            size,
            ratio,
            fmt(overall["candidate_nll"]),
            fmt(overall["delta_nll"]),
            fmt(overall["candidate_perplexity"], 3),
            fmt(overall["perplexity_increase_pct"], 3) + "%",
            fmt(overall["top1_agreement"] * 100.0, 2) + "%",
            fmt(overall["top5_overlap"] * 100.0, 2) + "%",
            fmt(overall["kl"], 6),
            fmt(overall["cosine"], 6),
            fmt(overall["router_overlap"] * 100.0, 2) + "%",
            fmt(overall["router_weight_mae"], 6),
        ]
    )


def quantization_table(entries: list[tuple[str, dict[str, Any], int | None]], base_bytes: int | None) -> str:
    header = (
        "| variant | size | compression | NLL | ΔNLL | perplexity | Δppl % | "
        "top-1 | top-5 | KL | cosine | router overlap | router w MAE |"
    )
    rule = "|" + "---|" * 13
    rows = [f"| {variant_row(name, report, size, base_bytes)} |" for name, report, size in entries]
    return "\n".join([header, rule, *rows])


def tails_table(report: dict[str, Any]) -> str:
    distributions = report["distributions"]
    header = "| metric | p50 | p90 | p99 | p99.9 | min | max |"
    rule = "|" + "---|" * 7
    rows = []
    for key in ("delta_nll", "kl", "cosine", "target_logit_delta", "router_overlap"):
        values = distributions.get(key)
        if not values:
            continue
        rows.append(
            "| "
            + " | ".join(
                [
                    key,
                    fmt(values["p50"], 5),
                    fmt(values["p90"], 5),
                    fmt(values["p99"], 5),
                    fmt(values["p99_9"], 5),
                    fmt(values["min"], 5),
                    fmt(values["max"], 5),
                ]
            )
            + " |"
        )
    return "\n".join([header, rule, *rows])


def layer_table(report: dict[str, Any], limit: int = 20) -> str:
    layers = report["by"]["layer"]
    header = "| layer | tokens | ΔNLL | router overlap | router w MAE |"
    rule = "|" + "---|" * 5
    rows = []
    for key, value in list(layers.items())[:limit]:
        rows.append(
            "| "
            + " | ".join(
                [
                    key,
                    str(value["count"]),
                    fmt(value["delta_nll"], 5),
                    fmt((value["router_overlap"] or 0.0) * 100.0, 2) + "%",
                    fmt(value["router_weight_mae"], 6),
                ]
            )
            + " |"
        )
    return "\n".join([header, rule, *rows])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", action="append", required=True, help="name=path.json")
    parser.add_argument("--size", action="append", default=[], help="name=bytes")
    parser.add_argument("--base-bytes", type=int, default=None)
    args = parser.parse_args()

    sizes = {}
    for item in args.size:
        name, _, value = item.partition("=")
        sizes[name] = int(value)

    entries = []
    for item in args.report:
        name, _, path = item.partition("=")
        report = json.loads(Path(path).read_text(encoding="utf-8"))
        entries.append((name, report, sizes.get(name)))

    print("## Quantization table\n")
    print(quantization_table(entries, args.base_bytes))
    for name, report, _ in entries:
        print(f"\n## Tails — {name}\n")
        print(tails_table(report))
        outliers = report["outliers"]
        print(
            f"\nHigh-confidence BF16 tokens: {outliers['high_confidence_tokens']}; "
            f"catastrophic flips: {outliers['catastrophic_flips']}; "
            f"rate: {fmt(outliers['catastrophic_rate'], 6)}"
        )
        print(f"\n## Per-layer router agreement — {name}\n")
        print(layer_table(report))


if __name__ == "__main__":
    main()
