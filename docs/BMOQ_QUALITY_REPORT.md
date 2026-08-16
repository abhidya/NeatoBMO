# BMOQ quality and parity report

Status: **host quality measured on a real checkpoint. ESP32 parity not run.**
See [Validation status](#validation-status) for the exact line between what was
measured and what was not.

<!-- RESULTS: executive result filled from the generated tables below -->

## What was measured

A paired, teacher-forced comparison between the official BF16 OLMoE checkpoint
and the same checkpoint exported to BMOQ and executed by the actual `coli_mcu`
C runtime. Both sides consume byte-identical pre-tokenized input and are
compared token by token. No free-running generations are compared anywhere in
this report.

### Provenance

| item | value |
|---|---|
| checkpoint | `allenai/OLMoE-1B-7B-0924`, revision `main` |
| checkpoint safetensors | 13,838,721,960 bytes |
| tokenizer | `allenai/OLMoE-1B-7B-0924` (vocab 50280; model `vocab_size` 50304) |
| corpus | `Salesforce/wikitext` / `wikitext-2-raw-v1`, split `test` |
| corpus tokens | 288,730 total; 5 non-overlapping windows of 1024 used |
| evaluated target tokens | 5,115 |
| benchmark JSONL SHA256 | `c8b091b025702966130394f7a6755664977dab04a8b9199ef81cd4a0cc814866` |
| BMOQ model | `q4-sym-g32`, 4,326,221,154 bytes |
| BMOQ model SHA256 | `1854d28d650c3cce80f13cc4034dbf67dad898a506cbc21afa3ad8b49c341a6f` |
| truncation policy | drop trailing partial window; no padding token is ever scored |
| sequence-length policy | fixed non-overlapping 1024-token windows |
| seed | none; no sampling anywhere, dataset order preserved |
| host | Intel i7-9750H @ 2.60 GHz, 6 physical / 12 logical cores, 32 GB RAM, macOS 26.5.2 |
| libraries | Python 3.12.13, torch 2.2.2, transformers 4.57.6, datasets 5.0.1, numpy 1.26.4 |

`torch` is pinned at 2.2.2 because that is the last release with macOS x86_64
wheels; the reference therefore runs on CPU in bfloat16, the checkpoint's own
storage dtype.

### Exact commands

```sh
python3 esp32-body/tools/bmoq_eval/build_corpus.py \
  --tokenizer .models/OLMoE-1B-7B-0924 --sequence-length 1024 \
  --max-target-tokens 5000 --output runs/wikitext2-5k.jsonl

python3 esp32-body/tools/export_olmoe_bmoq.py \
  .models/OLMoE-1B-7B-0924 runs/olmoe-q4-sym-g32.bmoq --group-size 32

python3 esp32-body/tools/bmoq_eval/eval_hf_olmoe.py \
  --model .models/OLMoE-1B-7B-0924 --device-map none --dtype bfloat16 \
  --input runs/wikitext2-5k.jsonl --output runs/olmoe-bf16-5k.jsonl \
  --logit-top-k 32 --router-top-k 8 --full-logit-stride 32

python3 esp32-body/tools/bmoq_eval/run_bmoq_parallel.py \
  --executable esp32-body/tools/build/bmoq-eval-olmoe \
  --model runs/olmoe-q4-sym-g32.bmoq --tokenizer runs/olmoe-tokenizer.ctok \
  --input runs/wikitext2-5k.jsonl --output runs/olmoe-bmoq-q4sym-g32-5k.jsonl \
  --workers 5 --logit-top-k 32 --full-logit-stride 32 --dump-routing

python3 esp32-body/tools/bmoq_eval/compare_quantization.py \
  --reference runs/olmoe-bf16-5k.jsonl \
  --candidate runs/olmoe-bmoq-q4sym-g32-5k.jsonl \
  --report runs/bmoq-vs-bf16-5k.json
```

### Why these numbers are comparable

* **Identical token stream.** Both runners read the same pre-tokenized JSONL
  and never tokenize. Record `position` p carries target token `tokens[p]`
  scored from the distribution after consuming `tokens[p-1]`, on both sides.
  The comparator rejects misaligned, duplicated, or missing records, and
  cross-checks the corpus SHA256 recorded by each side.
* **Actual runtime, not a proxy.** The BMOQ side is `bmoq-eval-olmoe`, linking
  the same `q4_matvec.c`, `moe.c`, `olmoe.c`, and `kv_cache.c` the firmware
  builds. Nothing is reimplemented in NumPy or PyTorch. Weights stream through
  bounded workspace tiles; no expert bundle is expanded.
* **Router units reconciled.** `output_router_logits=True` returns raw
  pre-softmax gate logits; the runtime reports post-softmax routing weights.
  The reference applies the same softmax, and honours `norm_topk_prob=false` as
  OLMoE declares, so router-weight error measures quantization rather than a
  units mismatch.
* **KL and cosine are exact but sampled.** Full 50304-wide distributions are
  written on a position-keyed stride of 32 on both sides; the tables state the
  sampled count. Every record carries the target logit and a top-32.

<!-- RESULTS TABLES -->

## Validation status

<!-- RESULTS: filled at the end -->
