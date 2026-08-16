# BMOQ host quantization evaluation

This package persists the first required quality gate for Colibri/OLMoE BMOQ:
run the official BF16 model and the exported `model.bmoq` on the same
teacher-forced token stream, then compare degradation before spending time on
ESP32 speed or physical parity.

The benchmark JSONL contract is intentionally streamable. Each `token_eval`
record contains one teacher-forced target token, the NLL for that token, full
or sparse top-k logits, variant metadata, prompt category, sequence length, and
per-layer router top-k experts plus weights. When sparse `logit_top_k` records
are used, NLL is still computed from the full distribution before compaction,
but KL divergence and logit cosine are reported as `null` because the full
distribution is unavailable.

## Deterministic fixture

The checked-in fixture is synthetic and does not claim real model quality:

```sh
python3 tools/bmoq_eval/make_fixture.py --output-dir tools/bmoq_eval/fixtures/tiny
python3 tools/bmoq_eval/compare_quantization.py \
  --reference tools/bmoq_eval/fixtures/tiny/bf16-results.jsonl \
  --candidate tools/bmoq_eval/fixtures/tiny/bmoq-results.jsonl \
  --report tools/bmoq_eval/fixtures/tiny/bmoq-vs-bf16.json
```

## Real 50k-token host run

Use a fixed corpus such as WikiText-2 or a held-out local JSONL file with
pre-tokenized `tokens` fields. The Hugging Face runner can tokenize `prompt`
fields for convenience, but the exact C BMOQ runner consumes `tokens`; use
pre-tokenized records for the real paired run so both sides see identical token
ids. Keep tokenizer, truncation, prompt order, and random seed identical. The C
runner handles standard short JSON escapes in `sample_id` and `category`; keep
fixture metadata ASCII for reproducible host/device logs.

```sh
make -C tools build/bmoq-eval-olmoe

python3 tools/bmoq_eval/eval_hf_olmoe.py \
  --model allenai/OLMoE-1B-7B-0924 \
  --input benchmark.jsonl \
  --output runs/olmoe-bf16-results.jsonl

python3 tools/bmoq_eval/eval_bmoq.py \
  --executable tools/build/bmoq-eval-olmoe \
  --model model.bmoq \
  --tokenizer tokenizer.ctok \
  --input benchmark.jsonl \
  --output runs/olmoe-bmoq-results.jsonl \
  --dump-logits \
  --dump-routing

python3 tools/bmoq_eval/compare_quantization.py \
  --reference runs/olmoe-bf16-results.jsonl \
  --candidate runs/olmoe-bmoq-results.jsonl \
  --report runs/bmoq-vs-bf16.json
```

The report includes overall NLL/perplexity delta, top-1 agreement, top-5
overlap, KL divergence, logit cosine, router overlap, router-weight error, and
stratification by tensor precision, layer, token position, prompt category,
expert, and sequence length.

## Variant metadata

Every result record carries `variant` metadata. For the quantizer matrix, use
stable names such as:

- `bf16-bmoq-control`
- `q4-sym-g32`
- `q4-mse-g32`
- `q4-mse-g64`
- `q4-experts-q8-router-lm-head`
- `q4-experts-f16-router-lm-head`
- `q5q6-sensitive-tensors`

Do not check in generated real-weight result files unless the command,
checkpoint hash, tokenizer hash, corpus hash, host executable commit, and
hardware/runtime metadata are also recorded.

## ESP32 parity stage

After host BF16-vs-BMOQ degradation is acceptable, run the same token stream
through the ESP32 path and compare ESP32 BMOQ greedy tokens against host BMOQ.
That later stage should target 100% token parity and 100% repeated-output
determinism; it should not replace the host quality comparison.
