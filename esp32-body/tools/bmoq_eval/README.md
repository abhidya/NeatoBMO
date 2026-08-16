# BMOQ host quantization evaluation

This package persists the first required quality gate for Colibri/OLMoE BMOQ:
run the official BF16 model and the exported `model.bmoq` on the same
teacher-forced token stream, then compare degradation before spending time on
ESP32 speed or physical parity.

## What the pairing guarantees

Both runners consume a pre-tokenized benchmark JSONL and never tokenize
anything themselves. A record at `position` p carries the *target* token
`tokens[p]`, scored from the distribution produced after consuming
`tokens[p-1]`. Both sides label records identically, so `(sample_id, position,
token_id)` aligns exactly, and the comparator refuses misaligned, duplicated,
or missing records rather than quietly averaging over them.

Two units traps are worth knowing about, because both produce confident wrong
numbers rather than errors:

* **Router weights.** `output_router_logits=True` returns raw pre-softmax gate
  logits; `coli_mcu` reports post-softmax routing weights. The reference runner
  applies the same softmax, and the same optional top-k renormalization, that
  `select_routes` applies. OLMoE-1B-7B-0924 ships `norm_topk_prob=false`, so the
  default does not renormalize.
* **Corpus identity.** The comparator cross-checks the `corpus_sha256` recorded
  in each side's `run_meta` and raises on mismatch. Comparing two runs over
  different token streams is the one failure the metrics cannot reveal on their
  own.

## Record size

Full logits for every target token cost roughly 27 GB of JSON per variant at
50k tokens, and walking a 50304-wide vocabulary per token in Python is not
viable. Every record therefore carries the target logit and a sparse
`logit_top_k`; full distributions are sampled with `--full-logit-stride`.
KL divergence and logit cosine are exact but computed only on that sampled
subset, and the report states its count. The stride is keyed on token position,
not a running counter, so the same records carry full logits regardless of how
the corpus is sharded across workers.

## Deterministic fixture

The checked-in fixture is synthetic and does not claim real model quality:

```sh
python3 tools/bmoq_eval/make_fixture.py --output-dir tools/bmoq_eval/fixtures/tiny
python3 tools/bmoq_eval/compare_quantization.py \
  --reference tools/bmoq_eval/fixtures/tiny/bf16-results.jsonl \
  --candidate tools/bmoq_eval/fixtures/tiny/bmoq-results.jsonl \
  --report tools/bmoq_eval/fixtures/tiny/bmoq-vs-bf16.json
```

## Real paired run

### 1. Build the corpus once

```sh
python3 tools/bmoq_eval/build_corpus.py \
  --tokenizer allenai/OLMoE-1B-7B-0924 \
  --sequence-length 1024 --max-target-tokens 50000 \
  --output runs/wikitext2-50k.jsonl
```

Writes the benchmark plus a `.meta.json` recording the benchmark SHA256,
checkpoint and tokenizer identity, corpus id/split, total tokens, sequence
length and truncation policy, tool commit, host, and library versions. Feed the
benchmark SHA256 to both runners so the comparator can verify it.

### 2. BF16 control

```sh
python3 tools/bmoq_eval/eval_hf_olmoe.py \
  --model allenai/OLMoE-1B-7B-0924 --device-map none --dtype bfloat16 \
  --input runs/wikitext2-50k.jsonl --output runs/olmoe-bf16.jsonl \
  --logit-top-k 32 --router-top-k 8 --full-logit-stride 32 \
  --corpus-sha256 <sha> --tool-commit <commit>
```

### 3. BMOQ candidate through the actual C runtime

```sh
make -C tools build/bmoq-eval-olmoe

python3 tools/bmoq_eval/run_bmoq_parallel.py \
  --executable tools/build/bmoq-eval-olmoe \
  --model runs/olmoe-q4-sym-g32.bmoq --tokenizer runs/olmoe-tokenizer.ctok \
  --input runs/wikitext2-50k.jsonl --output runs/olmoe-bmoq.jsonl \
  --workers 6 --logit-top-k 32 --full-logit-stride 32 --dump-routing \
  --variant-name q4-sym-g32 --corpus-sha256 <sha> --tool-commit <commit>
```

`run_bmoq_parallel.py` shards windows across processes and merges results back
into window order. Each window is an independent sequence with its own KV
cache, so sharding changes no arithmetic. Use `bmoq-eval-olmoe` directly for a
single-process run.

### 4. Compare

```sh
python3 tools/bmoq_eval/compare_quantization.py \
  --reference runs/olmoe-bf16.jsonl \
  --candidate runs/olmoe-bmoq.jsonl \
  --report runs/bmoq-vs-bf16.json
```

The report carries NLL/perplexity deltas, top-1 agreement, top-5 overlap, KL,
logit cosine, router overlap and router-weight error; percentile tails for each
of those rather than means alone; a catastrophic-outlier count for tokens where
BF16 was confidently correct and BMOQ changed the top-1; and stratification by
tensor precision, layer, token position, prompt category, expert, and sequence
length.

## Cost

Measured on an Intel i7-9750H, single thread, warm page cache: the Q4 matvec
kernel runs at 0.354 GMAC/s built `-O2` with the mmap-backed host store, and
OLMoE-1B-7B needs 1.177 GMAC per token. That is ~3.3 s/token, so 50k tokens
costs ~46 h in one process and ~8 h across six workers. Budget accordingly
before starting a variant matrix.

## Variant metadata

Every result record carries `variant` metadata. Use stable names such as
`bf16-hf`, `q4-sym-g32`, `q4-sym-g64`, `q4-sym-g128`.

**Implemented today:** grouped symmetric Q4 with absmax scales
(`scale = max_abs / 7`), applied to every 2-D tensor including the router and
the LM head, at any group size that divides each row. `--group-size` selects it.

**Not implemented:** MSE-optimal scale search, and any mixed precision
(`q4-mse-*`, Q8/Q5/Q6/F16 router or LM head). Those need new dtypes in the
exporter and the model format *and* new kernels in `q4_matvec.c`; they do not
exist, and no result should be reported for them until they do.

Do not check in generated real-weight result files unless the command,
checkpoint hash, tokenizer hash, corpus hash, host executable commit, and
hardware/runtime metadata are also recorded.

## ESP32 parity stage

After host BF16-vs-BMOQ degradation is acceptable, run the same token stream
through the ESP32 path and compare ESP32 BMOQ greedy tokens against host BMOQ.
That later stage should target 100% token parity and 100% repeated-output
determinism; it should not replace the host quality comparison.
