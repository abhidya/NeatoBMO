#!/usr/bin/env python3
"""Teacher-forced Hugging Face BF16 OLMoE evaluator.

Heavy dependencies are imported only inside the real runner so unit tests can
exercise the package without downloading model weights or installing PyTorch.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, Iterable

try:
    from .schema import SCHEMA_VERSION, read_jsonl, write_jsonl
except ImportError:  # pragma: no cover - direct script execution
    from schema import SCHEMA_VERSION, read_jsonl, write_jsonl


def log_softmax_nll(logits: list[float], token_id: int) -> float:
    peak = max(logits)
    total = sum(math.exp(value - peak) for value in logits)
    return math.log(total) + peak - logits[token_id]


def logit_payload(logits: list[float], token_id: int, top_k: int) -> tuple[float, list[float], list[dict[str, float | int]]]:
    nll = log_softmax_nll(logits, token_id)
    if top_k <= 0:
        return nll, logits, []
    keep = sorted(range(len(logits)), key=lambda index: (-logits[index], index))[:top_k]
    return nll, [], [{"token_id": int(index), "logit": float(logits[index])} for index in keep]


def router_topk(router_logits: Any, token_index: int, top_k: int) -> list[dict[str, Any]]:
    result = []
    for layer_index, layer_logits in enumerate(router_logits or []):
        if isinstance(layer_logits, list):
            values = layer_logits[token_index]
            order = sorted(range(len(values)), key=lambda index: (-values[index], index))[:top_k]
            result.append(
                {
                    "layer": layer_index,
                    "top_experts": [int(index) for index in order],
                    "weights": [float(values[index]) for index in order],
                }
            )
            continue
        import torch

        values = layer_logits.reshape(-1, layer_logits.shape[-1])[token_index]
        top = torch.topk(values, min(top_k, values.shape[-1]))
        result.append(
            {
                "layer": layer_index,
                "top_experts": [int(value) for value in top.indices.detach().cpu().tolist()],
                "weights": [float(value) for value in top.values.detach().cpu().tolist()],
            }
        )
    return result


def run_hf(args: argparse.Namespace) -> Iterable[dict[str, Any]]:
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(args.model)
    model = AutoModelForCausalLM.from_pretrained(
        args.model,
        torch_dtype=torch.bfloat16,
        device_map=args.device_map,
        trust_remote_code=args.trust_remote_code,
    )
    model.eval()
    for sample_index, sample in enumerate(read_jsonl(args.input)):
        sample_id = str(sample.get("sample_id", sample.get("id", sample_index)))
        category = str(sample.get("category", "uncategorized"))
        if "tokens" in sample:
            input_ids = torch.tensor([sample["tokens"]], dtype=torch.long, device=model.device)
        else:
            encoded = tokenizer(str(sample["prompt"]), return_tensors="pt")
            input_ids = encoded["input_ids"].to(model.device)
        with torch.no_grad():
            output = model(input_ids=input_ids, output_router_logits=True)
        logits = output.logits[0].detach().float().cpu()
        router_logits = getattr(output, "router_logits", None)
        token_ids = input_ids[0].detach().cpu().tolist()
        for position in range(1, len(token_ids)):
            prediction = logits[position - 1].tolist()
            nll, dense_logits, sparse_logits = logit_payload(
                prediction, int(token_ids[position]), args.logit_top_k
            )
            yield {
                "schema_version": SCHEMA_VERSION,
                "record_type": "token_eval",
                "sample_id": sample_id,
                "position": position,
                "token_id": int(token_ids[position]),
                "nll": nll,
                "logits": dense_logits,
                "logit_top_k": sparse_logits,
                "category": category,
                "sequence_length": len(token_ids),
                "variant": {
                    "name": "bf16-hf",
                    "model": args.model,
                    "tensor_precision": "BF16",
                    "runtime": "transformers",
                },
                "router": router_topk(router_logits, position - 1, args.router_top_k),
            }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="allenai/OLMoE-1B-7B-0924")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--device-map", default="auto")
    parser.add_argument("--trust-remote-code", action="store_true")
    parser.add_argument("--router-top-k", type=int, default=8)
    parser.add_argument("--logit-top-k", type=int, default=0, help="0 writes full logits")
    args = parser.parse_args()

    write_jsonl(args.output, run_hf(args))


if __name__ == "__main__":
    main()
