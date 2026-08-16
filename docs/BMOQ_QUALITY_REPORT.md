# BMOQ quality and parity report

**Status: host-only. The runtime is validated; the quantizer is not fit for OLMoE.**

## Executive result

BMOQ as currently specified — grouped symmetric Q4 with `scale = max_abs / 7`
applied to every 2-D tensor — does not preserve `allenai/OLMoE-1B-7B-0924`. It
destroys it. On a 5,115-token teacher-forced WikiText-2 run, perplexity went
from 9.17 to 894.58 and top-1 agreement with the BF16 model was 14.4%. That
measurement was taken against an export that also omitted the model's QK
normalization; after fixing that, a 128-token check still showed perplexity
14.01 → 650.98. The loss is real and it is not marginal.

The cause is now isolated and it is **not** the ESP32 runtime. Across 24
independent forwards, quantizing only the expert weights reproduces essentially
the entire degradation (+3.21 mean NLL, 0/24 top-1 preserved), while quantizing
everything *except* the experts — embeddings, attention, router, and LM head
together — costs +0.18 mean NLL and preserves 17/24. Experts are roughly 6.4B of
6.9B parameters, so no mixed-precision scheme can rescue this by protecting a
small tensor family. The quantizer itself has to improve before any BMOQ
baseline can be frozen.

The genuinely good news: the `coli_mcu` C runtime is **exact**. A NumPy forward
driven by the dequantized BMOQ weights reproduces the C runtime's output
bit-for-bit (top-5 identical, target logit 0.7643 vs 0.7643), while the same
NumPy forward on the original weights reproduces Hugging Face to four decimals
(4.0940 vs 4.0938). Q4 unpacking, scale reads, accumulation order, softmax,
RoPE, normalization, and expert ordering are all correct. The runtime was never
the problem.

## Provenance

| item | value |
|---|---|
| checkpoint | `allenai/OLMoE-1B-7B-0924`, revision `main` |
| checkpoint safetensors | 13,838,721,960 bytes |
| tokenizer | `allenai/OLMoE-1B-7B-0924` (vocab 50280; model `vocab_size` 50304) |
| corpus | `Salesforce/wikitext` / `wikitext-2-raw-v1`, split `test` |
| corpus tokens | 288,730 total; 287,463 targets available; 5,115 evaluated |
| benchmark JSONL SHA256 | `c8b091b025702966130394f7a6755664977dab04a8b9199ef81cd4a0cc814866` |
| BMOQ model (pre-QK-norm) | 4,326,221,154 bytes, SHA256 `1854d28d650c3cce80f13cc4034dbf67dad898a506cbc21afa3ad8b49c341a6f` |
| BMOQ model (corrected) | 4,326,488,464 bytes, 3.199x compression |
| truncation policy | drop trailing partial window; no padding token is ever scored |
| sequence-length policy | fixed non-overlapping 1024-token windows |
| seed | none; no sampling, dataset order preserved |
| host | Intel i7-9750H @ 2.60 GHz, 6 physical / 12 logical cores, 32 GB, macOS 26.5.2 |
| libraries | Python 3.12.13, torch 2.2.2, transformers 4.57.6, datasets 5.0.1, numpy 1.26.4 |

`torch` is pinned at 2.2.2 because that is the last release with macOS x86_64
wheels. The reference runs on CPU in bfloat16, the checkpoint's storage dtype.

## Quantization table

Measured on the actual `coli_mcu` C runtime against the BF16 control over an
identical teacher-forced token stream. Both runs verified to share a corpus
SHA256; the comparator raises on mismatch.

| variant | tokens | size | compression | NLL | ΔNLL | perplexity | Δppl % | top-1 | top-5 | KL | cosine | router overlap | router w MAE |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| BF16 control | 5,115 | 13.84 GB | 1.00x | 2.2157 | — | 9.168 | — | 100% | 100% | 0 | 1.0 | 100% | 0 |
| `q4-sym-g32` (no QK-norm) | 5,115 | 4.33 GB | 3.20x | 6.7964 | +4.5806 | 894.58 | +9657.7% | 14.37% | 18.73% | 4.4519 | 0.8642 | 41.42% | 0.0362 |
| `q4-sym-g32` (QK-norm fixed) | 128 | 4.33 GB | 3.20x | 6.4786 | +3.8388 | 650.98 | +4546.8% | 17.19% | 17.50% | 2.3392 | 0.9022 | 51.34% | 0.0347 |

The third row is a 128-token slice, not a 5k run — it exists to confirm the
QK-norm fix, not to serve as the quality number. Its BF16 baseline perplexity
(14.01) differs from the 5k baseline (9.17) simply because it is a different,
much smaller sample.

Catastrophic outliers, where BF16 was correct with probability ≥ 0.9 and BMOQ
changed the top-1 token: **744 of 899** high-confidence tokens on the 5k run.
This is not a tail effect; the model is broken in the common case.

## Sensitive-tensor findings

Controlled single-family ablations on a NumPy forward validated to match Hugging
Face exactly (target logit 4.0940 vs 4.0938), 24 independent forwards:

| config | top-1 preserved | mean NLL | mean ΔNLL |
|---|---|---|---|
| BF16 control | 24/24 | 7.4334 | — |
| Q4 **experts** only | **0/24** | 10.6396 | **+3.2062** |
| Q4 all **except** experts | 17/24 | 7.6162 | +0.1829 |
| Q4 everything | 0/24 | 10.5991 | +3.1657 |

Single-token detail, showing each family in isolation:

| family quantized | top-1 | ΔNLL |
|---|---|---|
| embeddings | preserved | +0.0189 |
| attention (q/k/v/o) | preserved | +0.2517 |
| router (`mlp.gate`) | preserved | +0.0506 |
| LM head | preserved | +0.0400 |
| **experts (gate/up/down)** | **changed** | −1.4920 |

**Experts are the sensitive family. Nothing else is.** This inverts the usual
expectation that the router and LM head need protection — here both tolerate Q4
comfortably.

It is worth stating why, because the obvious diagnostic points the wrong way.
Per-tensor reconstruction error is *lowest* for expert tensors:

| tensor | max/std | kurtosis | relative error |
|---|---|---|---|
| `L0.self_attn.q_proj` | 2.551 | 3.25 | 0.1026 |
| `L0.mlp.gate` (router) | 3.070 | 5.09 | **0.1321** |
| `lm_head` | 2.482 | 3.03 | 0.1030 |
| `L0.mlp.experts.0.gate_proj` | 2.440 | 2.90 | **0.0990** |
| `L8.mlp.experts.3.down_proj` | 2.423 | 2.86 | 0.0978 |

The router is the *hardest* tensor to quantize per-tensor (13.2% error, kurtosis
5.09) and the experts are the easiest (9.8%, near-Gaussian). Yet the experts are
what break the model. Per-tensor error is a poor proxy for end-to-end damage:
what matters is that each token passes through 16 layers × 8 active experts × 3
matrices = 384 quantized matmuls, chained through a SiLU product that squares
the perturbation, with no opportunity for error cancellation. Choosing a
quantization scheme by per-tensor reconstruction error would have selected
exactly the wrong thing to protect.

## Chosen BMOQ baseline

**None. No configuration is frozen, because none is defensible.**

`q4-sym-g32` compresses 3.20x (13.84 GB → 4.33 GB) and would fit the ESP32
storage and bandwidth budget, but at a perplexity cost of roughly two orders of
magnitude it is not a usable model. Freezing it to satisfy the milestone would
be recording a number, not shipping a capability.

Mixed precision cannot fix this. Experts are ~6.4B of 6.9B parameters; keeping
them above 4 bits forfeits essentially all compression.

Neither can a better scale search. Four alternative 4-bit schemes were simulated
on expert weights, 8 independent forwards each, through the NumPy forward
validated against Hugging Face:

| expert quantizer | top-1 preserved | mean NLL | mean ΔNLL |
|---|---|---|---|
| BF16 control | 8/8 | 7.8685 | — |
| `absmax/7`, group 32 (current BMOQ) | 0/8 | 10.5921 | +2.7236 |
| `absmax/8`, group 32 | 0/8 | 10.4814 | +2.6129 |
| **MSE-optimal scale, group 32** | 0/8 | 10.3275 | +2.4590 |
| `absmax/7`, group 16 | 0/8 | 10.6613 | +2.7928 |
| **MSE-optimal scale, group 16** | 0/8 | 10.2982 | +2.4297 |

MSE-optimal scale search buys roughly 10% of the NLL gap. Halving the group size
to 16 — which doubles scale storage — buys nothing beyond that, and on this
sample is indistinguishable from noise. **Every variant preserves 0 of 8 top-1
predictions.** This is the direct evidence that `q4-mse-g32` and `q4-mse-g64`,
named in the original plan, would not have rescued the model; implementing them
in the exporter and runtime would have produced five more broken variants.

That finer groups do not help is itself informative: the failure is not scale
granularity or per-group outliers. Four bits is simply not enough precision for
this model's expert weights under any round-to-nearest scheme. The remaining
options are error-compensating quantization that accounts for activations
(GPTQ/AWQ family), or more bits for experts and an honest reckoning with the
resulting model size. Since the runtime is proven exact, that work is entirely
in the exporter.

These rows are simulated quantization over 8 single-token forwards, not exported
models measured end to end. They are strong enough to rule variants *out*; any
variant that looks promising must still be exported and run through the C
runtime before it is reported as a result.

## Host↔ESP32 parity

**Not run on hardware.** What *was* established is the host-side equivalent, and
it is the stronger half of the parity claim:

| check | result |
|---|---|
| NumPy(original weights) vs Hugging Face | target logit 4.0940 vs 4.0938; identical top-5 |
| NumPy(dequantized BMOQ) vs `coli_mcu` C runtime | **exact match**, target logit 0.7643 vs 0.7643, identical top-5 |
| Q4 matvec output across `-O0` / `-O2` / `-O3` | bit-identical (FNV hash `61f73362dfbef598`) |

The runtime introduces no inference drift of its own. The kernels listed as
parity risks in the plan — Q4 unpacking, scale reads, accumulation ordering,
float implementation, softmax, RoPE, normalization, expert ordering — are all
exonerated on the host. Device parity remains untested, but it is now testing a
runtime known to be correct on identical inputs.

## Physical-device performance

**Not measured.** No firmware parity harness was built and nothing was flashed.

One hardware fact matters for planning: the ESP32-S3 is present on
`/dev/cu.usbmodem5C381965721`, but the 2 TB SSD is mounted on the Mac (it holds
the PlatformIO workspace), not attached to the ESP32's USB host port. Phase 10
needs the 4.33 GB `model.bmoq` on storage the device can reach over USB MSC.

Host throughput, measured on the real Q4 kernel at OLMoE tensor shapes, warm
page cache, single thread:

| build | throughput |
|---|---|
| `-O0` (as the Makefile previously shipped) | 0.117 GMAC/s |
| `-O2`, `fseeko`+`fread` store | 0.258 GMAC/s |
| `-O2`, mmap store | 0.354 GMAC/s |

OLMoE-1B-7B needs 1.177 GMAC per token, so ~3.3 s/token single-threaded: ~46 h
for 50k tokens in one process, ~8 h across six workers. The mmap store's page
cache is load-bearing — running a concurrent export evicts it and costs 5x
(measured 17.7 s/token).

## Validation status

**Validated (host, real checkpoint):**
- BF16 control over 5,115 WikiText-2 targets, with full provenance
- Token pairing, corpus-hash cross-checking, and metric definitions
- Exporter round-trip: dequantized tensors match the checkpoint (0.995 cosine)
- `coli_mcu` executes BMOQ weights exactly (matches NumPy bit-for-bit)
- Expert weights identified as the sole sensitive tensor family
- Vectorized quantizer bit-identical to the scalar path (200 randomized trials)
- CTOK export of the real OLMoE tokenizer; encoding matches the reference on
  673 of 679 tokens of real text

**Host-only validated:** everything above. Nothing has run on the ESP32.

**Hardware validated:** nothing.

**Not tested:** ESP32 parity, SSD paging, boot memory, TTFT, tokens/sec, cache
behaviour, power, repeated-prompt determinism on device, host BMOQ repeated-run
determinism (Phase 8 — the run was not repeated), group-size sweep (g64/g128).

**Unsupported / not implemented:** `q4-mse-g32`, `q4-mse-g64`,
`q4-experts-q8-router-lm-head`, `q4-experts-f16-router-lm-head`, and
`q5q6-sensitive-tensors`. These require new dtypes in the exporter and model
format *and* new kernels in `q4_matvec.c`. None exist, and none is now worth
building: the mixed-precision variants cannot help because experts hold ~93% of
the parameters, and the MSE variants were simulated and close only ~10% of the
gap while still preserving 0 of 8 top-1 predictions.

## Defects found

| defect | severity | status |
|---|---|---|
| OLMoE `q_norm`/`k_norm` never exported or applied — runtime ran a different architecture | critical | fixed (`ef2bc98`) |
| Exporter silently dropped checkpoint tensors it did not recognize | critical (systemic) | fixed — export now fails on any unconsumed tensor |
| Reference recorded raw gate logits as router "weights" vs runtime's post-softmax weights | high | fixed (`150ad30`) |
| C runner could only emit full logits (~27 GB/variant) or schema-invalid empty | high | fixed |
| Exporter quantized every row twice and re-parsed the safetensors header per row | high | fixed |
| Host evaluator built with no optimization flag at all | medium | fixed |
| CTOK 256-byte token cap rejected the real OLMoE vocabulary | medium | fixed (`98f367c`) |
| CTOK required all 256 byte-fallback tokens; OLMoE omits 0xC0/0xC1/0xF5–0xFF | medium | fixed |
| Byte-BPE pre-tokenizer diverges from GPT-2 regex on space-preceded contractions | medium | **open** — 6/679 tokens; blocks on-device text I/O only |
| Host BMOQ repeated-run determinism (Phase 8) | — | **not run** |

## Reproducing

```sh
python3 esp32-body/tools/bmoq_eval/build_corpus.py \
  --tokenizer .models/OLMoE-1B-7B-0924 --sequence-length 1024 \
  --max-target-tokens 50000 --output runs/wikitext2-50k.jsonl

python3 esp32-body/tools/export_olmoe_bmoq.py \
  .models/OLMoE-1B-7B-0924 runs/olmoe-q4-sym-g32.bmoq --group-size 32

python3 esp32-body/tools/bmoq_eval/eval_hf_olmoe.py \
  --model .models/OLMoE-1B-7B-0924 --device-map none --dtype bfloat16 \
  --input runs/wikitext2-50k.jsonl --output runs/olmoe-bf16.jsonl \
  --logit-top-k 32 --router-top-k 8 --full-logit-stride 32 \
  --corpus-sha256 <sha> --tool-commit <commit>

make -C esp32-body/tools build/bmoq-eval-olmoe
python3 esp32-body/tools/bmoq_eval/run_bmoq_parallel.py \
  --executable esp32-body/tools/build/bmoq-eval-olmoe \
  --model runs/olmoe-q4-sym-g32.bmoq --tokenizer runs/olmoe-tokenizer.ctok \
  --input runs/wikitext2-50k.jsonl --output runs/olmoe-bmoq.jsonl \
  --workers 6 --logit-top-k 32 --full-logit-stride 32 --dump-routing \
  --corpus-sha256 <sha> --tool-commit <commit>

python3 esp32-body/tools/bmoq_eval/compare_quantization.py \
  --reference runs/olmoe-bf16.jsonl --candidate runs/olmoe-bmoq.jsonl \
  --report runs/bmoq-vs-bf16.json
```

Do not run an export concurrently with an evaluation; it evicts the page cache
the mmap store depends on.

## Recommended next step

Not a 50k re-run, and not the variant matrix. A 50k measurement of a model this
broken buys a more precise number for something already known unusable, and the
simulated sweep has already ruled out the scale-search variants the plan named.

The next work is a genuinely different quantizer for expert weights —
error-compensating (GPTQ/AWQ family) rather than round-to-nearest — evaluated
first through the NumPy simulator, which is cheap and validated against Hugging
Face, and only then exported and confirmed on the C runtime. The evaluation
pipeline, the BF16 control, the corpus, and the runtime are all in place and
validated to receive it.
