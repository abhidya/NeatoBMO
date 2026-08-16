#!/usr/bin/env python3
"""Teacher-forced Hugging Face BF16 OLMoE evaluator.

Heavy dependencies are imported only inside the real runner so unit tests can
exercise the package without downloading model weights or installing PyTorch.

Two properties matter for the paired comparison to mean anything:

* Token pairing. Record ``position`` p carries the *target* token
  ``tokens[p]`` scored from the distribution produced after consuming
  ``tokens[p-1]``. The BMOQ C runner labels its records the same way, so
  ``(sample_id, position, token_id)`` aligns exactly between the two files.
* Router units. ``output_router_logits=True`` hands back the raw pre-softmax
  gate logits. The C runtime reports post-softmax routing weights, so this
  module applies the same softmax (and the same optional top-k
  renormalization) before recording weights. Comparing raw logits against
  probabilities would report a units mismatch as quantization error.
"""

from __future__ import annotations

import argparse
import json
import math
import platform
import sys
from pathlib import Path
from typing import Any, Iterable

try:
    from .schema import SCHEMA_VERSION, RUN_META_RECORD, read_jsonl, write_jsonl
except ImportError:  # pragma: no cover - direct script execution
    from schema import SCHEMA_VERSION, RUN_META_RECORD, read_jsonl, write_jsonl


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


def softmax_values(values: list[float]) -> list[float]:
    peak = max(values)
    exponentials = [math.exp(value - peak) for value in values]
    total = sum(exponentials)
    return [value / total for value in exponentials]


def router_topk(
    router_logits: Any,
    token_index: int,
    top_k: int,
    norm_topk_prob: bool = False,
) -> list[dict[str, Any]]:
    """Top-k experts with post-softmax weights, matching coli_mcu select_routes.

    ``router_logits`` holds the raw gate logits Hugging Face returns. Ranking is
    unchanged by the softmax (it is monotonic), but the recorded weights must be
    probabilities to be comparable with the runtime's routing weights.
    """
    result = []
    for layer_index, layer_logits in enumerate(router_logits or []):
        if isinstance(layer_logits, list):
            values = softmax_values(list(layer_logits[token_index]))
            order = sorted(range(len(values)), key=lambda index: (-values[index], index))[:top_k]
            weights = [float(values[index]) for index in order]
            if norm_topk_prob:
                total = sum(weights)
                weights = [value / total for value in weights]
            result.append(
                {
                    "layer": layer_index,
                    "top_experts": [int(index) for index in order],
                    "weights": weights,
                }
            )
            continue
        import torch

        values = layer_logits.reshape(-1, layer_logits.shape[-1])[token_index]
        probabilities = torch.softmax(values.float(), dim=-1)
        top = torch.topk(probabilities, min(top_k, probabilities.shape[-1]))
        weights = [float(value) for value in top.values.detach().cpu().tolist()]
        if norm_topk_prob:
            total = sum(weights)
            weights = [value / total for value in weights]
        result.append(
            {
                "layer": layer_index,
                "top_experts": [int(value) for value in top.indices.detach().cpu().tolist()],
                "weights": weights,
            }
        )
    return result


def host_metadata() -> dict[str, Any]:
    info: dict[str, Any] = {
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "python": sys.version.split()[0],
    }
    try:
        import torch

        info["torch"] = torch.__version__
        info["torch_threads"] = torch.get_num_threads()
    except Exception:  # pragma: no cover - torch optional for unit tests
        pass
    try:
        import transformers

        info["transformers"] = transformers.__version__
    except Exception:  # pragma: no cover
        pass
    return info


def run_hf(args: argparse.Namespace) -> Iterable[dict[str, Any]]:
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(args.model, revision=args.revision)
    model = AutoModelForCausalLM.from_pretrained(
        args.model,
        revision=args.revision,
        torch_dtype=getattr(torch, args.dtype),
        device_map=None if args.device_map in ("none", "", None) else args.device_map,
        trust_remote_code=args.trust_remote_code,
    )
    model.eval()
    norm_topk_prob = bool(getattr(model.config, "norm_topk_prob", False))

    variant = {
        "name": args.variant_name,
        "model": args.model,
        "revision": args.revision or "main",
        "tensor_precision": args.dtype.upper(),
        "runtime": "transformers",
    }

    yield {
        "schema_version": SCHEMA_VERSION,
        "record_type": RUN_META_RECORD,
        "checkpoint_id": args.model,
        "checkpoint_revision": args.revision or "main",
        "tokenizer_id": args.model,
        "corpus_sha256": args.corpus_sha256 or "",
        "corpus_path": str(args.input),
        "tool_commit": args.tool_commit or "",
        "full_logit_stride": args.full_logit_stride,
        "logit_top_k": args.logit_top_k,
        "router_top_k": args.router_top_k,
        "norm_topk_prob": norm_topk_prob,
        "host": host_metadata(),
        "variant": variant,
    }

    for sample_index, sample in enumerate(read_jsonl(args.input)):
        if sample.get("record_type") == RUN_META_RECORD:
            continue
        sample_id = str(sample.get("sample_id", sample.get("id", sample_index)))
        category = str(sample.get("category", "uncategorized"))
        if "tokens" in sample:
            input_ids = torch.tensor([sample["tokens"]], dtype=torch.long, device=model.device)
        else:
            encoded = tokenizer(str(sample["prompt"]), return_tensors="pt")
            input_ids = encoded["input_ids"].to(model.device)
        with torch.no_grad():
            output = model(input_ids=input_ids, output_router_logits=True)

        # Vectorized scoring: a pure-Python pass over a 50304-wide vocabulary
        # for every target token is what made the original runner unusable at
        # benchmark scale.
        logits = output.logits[0].detach().float().cpu()
        token_ids = input_ids[0].detach().cpu()
        targets = token_ids[1:]
        predictions = logits[:-1]
        log_probabilities = torch.log_softmax(predictions, dim=-1)
        nlls = -log_probabilities.gather(1, targets[:, None]).squeeze(1)
        target_logits = predictions.gather(1, targets[:, None]).squeeze(1)
        keep = max(args.logit_top_k, 0)
        if keep:
            top = torch.topk(predictions, min(keep, predictions.shape[-1]), dim=-1)
            top_indices = top.indices.tolist()
            top_values = top.values.tolist()
        else:
            top_indices = top_values = None

        router_logits = getattr(output, "router_logits", None)
        router_top: list[tuple[list[list[int]], list[list[float]]]] = []
        if args.router_top_k > 0 and router_logits:
            for layer_logits in router_logits:
                flattened = layer_logits.reshape(-1, layer_logits.shape[-1]).detach().float().cpu()
                probabilities = torch.softmax(flattened, dim=-1)
                layer_top = torch.topk(
                    probabilities, min(args.router_top_k, probabilities.shape[-1]), dim=-1
                )
                weights = layer_top.values
                if norm_topk_prob:
                    weights = weights / weights.sum(dim=-1, keepdim=True)
                router_top.append((layer_top.indices.tolist(), weights.tolist()))

        nll_list = nlls.tolist()
        target_logit_list = target_logits.tolist()
        for offset in range(len(nll_list)):
            position = offset + 1
            dense: list[float] = []
            # Position-keyed, matching the C runner, so both sides carry full
            # logits on exactly the same records under any sharding.
            if args.full_logit_stride > 0 and position % args.full_logit_stride == 0:
                dense = predictions[offset].tolist()
            sparse = []
            if top_indices is not None:
                sparse = [
                    {"token_id": int(token), "logit": float(value)}
                    for token, value in zip(top_indices[offset], top_values[offset])
                ]
            router = [
                {
                    "layer": layer_index,
                    "top_experts": [int(value) for value in indices[offset]],
                    "weights": [float(value) for value in weights[offset]],
                }
                for layer_index, (indices, weights) in enumerate(router_top)
            ]
            yield {
                "schema_version": SCHEMA_VERSION,
                "record_type": "token_eval",
                "sample_id": sample_id,
                "position": position,
                "token_id": int(targets[offset]),
                "nll": float(nll_list[offset]),
                "target_logit": float(target_logit_list[offset]),
                "logits": dense,
                "logit_top_k": sparse,
                "category": category,
                "sequence_length": int(token_ids.shape[0]),
                "variant": variant,
                "router": router,
            }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="allenai/OLMoE-1B-7B-0924")
    parser.add_argument("--revision", default=None)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--device-map", default="auto")
    parser.add_argument("--dtype", default="bfloat16", choices=["bfloat16", "float16", "float32"])
    parser.add_argument("--trust-remote-code", action="store_true")
    parser.add_argument("--router-top-k", type=int, default=8)
    parser.add_argument("--logit-top-k", type=int, default=32, help="0 disables sparse top-k")
    parser.add_argument(
        "--full-logit-stride",
        type=int,
        default=0,
        help="emit full logits every N records for exact KL/cosine; 0 disables",
    )
    parser.add_argument("--variant-name", default="bf16-hf")
    parser.add_argument("--corpus-sha256", default=None)
    parser.add_argument("--tool-commit", default=None)
    args = parser.parse_args()

    write_jsonl(args.output, run_hf(args))


if __name__ == "__main__":
    main()
