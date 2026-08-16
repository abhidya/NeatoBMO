# coli_mcu host checks

Build every host test harness with the Makefile (binaries land in
`tools/build/`), or run them all in one shot; each generates its own
deterministic fixture and needs no arguments:

```sh
make -C tools        # build all thirteen with -Wall -Wextra -Werror
make -C tools test   # build and run them
```

What each test proves:

- `test_bmoq` — BMOQ parser plus tiled reads: a deterministic 16 MiB tensor
  read only in small tiles at distant offsets, including a tile crossing a
  4 KiB boundary.
- `test_q4_matvec` — streamed Q4 golden test: a deterministic Q4 matrix whose
  packed weights exceed 8 MiB, all 40,000 output rows compared with an
  independent formula reference, plus a 257-byte caller workspace and
  cross-4-KiB reads. To retain a fixture for device testing, run
  `python3 tools/export_bmoq_q4_fixture.py model.bmoq`.
- `test_coli_ops` — heap-free caller-buffer RMSNorm, RoPE, single-token causal
  attention decode, residual add, SiLU/gated multiply, and bounded KV-cache
  layout math for OLMoE hidden=2048, 16-head attention.
- `test_coli_olmoe_layer` — single-layer OLMoE decode: dense RMSNorm tensors,
  streamed Q/K/V/O Q4 projections, RoPE, KV-cache writes, causal single-token
  attention, residuals, streamed MoE, embedding row dequantization, final
  norm, and streamed LM-head argmax against an independent identity-weight
  reference, then a bounded greedy token-id generation loop.
- `test_coli_moe` — streamed MoE golden test: deterministic router, top-8
  tie-breaking, unnormalized softmax routing weights, serial gate/up/down
  experts, final hidden vector against an independent dense reference.
- `test_coli_tokenizer` — CTOK tokenizer substrate check.
- `test_coli_gemma` — single-layer Gemma 3 decode: Gemma-specific RMSNorm
  weights, Q/K head norms, GQA attention, post-attention/post-FFN norms, gated
  dense FFN, tied/untied lm-head fallback, and greedy token-id generation over
  a tiny BMOQ fixture.
- `test_coli_gemma_generate` — Gemma app-level prompt-to-text wrapper:
  callback tokenizer encode/decode, bounded allocation, yield/cancel callback
  plumbing, telemetry, and decoded output over the tiny Gemma fixture.
- `test_coli_generate` — OLMoE app-level prompt-to-text wrapper: callback
  tokenizer encode/decode, bounded allocation, yield/cancel plumbing, and
  decoded output over a tiny OLMoE fixture.
- `test_coli_spm` — Gemma SentencePiece tokenizer substrate check.
- `test_coli_kv_cache` — file-backed paged KV-cache read/write golden test
  against the contiguous-RAM reference.
- `test_coli_kv_attention` — streamed KV-cache attention decode golden test.
- `test_coli_glm52` — bounded GLM-5.2 attention state layout and decode golden
  test.

CTOK is a compact preconverted GPT-NeoX/OLMo byte-level BPE format for firmware:
128-byte little-endian header, a dense 24-byte token directory indexed by token
id, contiguous token bytes, and sorted 16-byte merge records
`left_id,right_id,result_id,rank`. The firmware keeps only the 256-entry byte
fallback map in RAM and uses caller-owned encode/decode buffers. Convert a
pre-downloaded Hugging Face tokenizer JSON with:

```sh
python3 tools/export_coli_tokenizer.py tokenizer.json tokenizer.ctok
```

Export a pre-downloaded OLMoE checkpoint directory to BMOQ:

```sh
python3 tools/export_olmoe_bmoq.py /path/to/OLMoE-1B-7B-0924 model.bmoq
```

The directory must already contain `config.json`, local safetensors shards, and
optionally `model.safetensors.index.json`; the exporter never downloads
weights. It validates the official `allenai/OLMoE-1B-7B-0924` shape by default,
streams tensors row-by-row, writes grouped symmetric Q4 weights plus float32
scale tensors, and embeds an OLMoE manifest/config block for firmware loading.
Use `--allow-nonstandard` only for synthetic fixtures or deliberate forks.

Inspect or export a local GLM-5.2 safetensors checkpoint to BMOQ v2:

```sh
python3 tools/export_glm52_bmoq.py /path/to/GLM-5.2 --inspect-only
python3 tools/export_glm52_bmoq.py /path/to/GLM-5.2 model.bmoq
```

The exporter reads only local `config.json`, optional `generation_config.json`,
and safetensors shards. It streams rows into grouped symmetric Q4, writes BMOQ
version 2, and emits the bounded binary config tensor used by the ESP32 GLM
loader for MLA ranks, RoPE dimensions, MoE routing, and stop-token metadata.

Inspect or export a local Inkling text safetensors checkpoint to BMOQ v2:

```sh
python3 tools/export_inkling_bmoq.py /path/to/Inkling --inspect-only
python3 tools/export_inkling_bmoq.py /path/to/Inkling model.bmoq
```

The exporter follows the upstream text-only `c/inkling.c` snapshot contract and
streams local/global GQA projections, relative-bias banks, q/k norms, short
convs, dense MLPs, shared experts, and fused routed expert tensors without
loading the checkpoint into memory.

Inspect or export a local Gemma 3 GGUF (the matching host check is
`test_coli_gemma`, built and run by the Makefile above):

```sh
python3 tools/export_gemma_gguf_bmoq.py \
  /Volumes/2TB/neatobmo-models/gemma-3-270m-q8_0/gemma-3-270m-Q8_0.gguf \
  --inspect-only

python3 tools/export_gemma_gguf_bmoq.py \
  /Volumes/2TB/neatobmo-models/gemma-3-270m-q8_0/gemma-3-270m-Q8_0.gguf \
  /Volumes/2TB/neatobmo-models/gemma-3-270m-q4.bmoq
```

The exporter supports GGUF v3 `general.architecture=gemma3` with F32 norm
tensors and Q8_0 matrices. It records Gemma metadata in the BMOQ manifest and
streams Q8_0 rows into grouped symmetric Q4 without loading the whole model.

Export the matching Gemma SentencePiece tokenizer from the same GGUF metadata:

```sh
python3 tools/export_gemma_tokenizer.py \
  /Volumes/2TB/neatobmo-models/gemma-3-270m-q8_0/gemma-3-270m-Q8_0.gguf \
  /Volumes/2TB/neatobmo-models/gemma-3-270m-q8_0/tokenizer.cspm
```

The `CSPM` tokenizer artifact keeps the 262k vocabulary and trie in
`coli_store_t`, preserves BOS/EOS/PAD/UNK ids plus byte fallback tokens, and
uses deterministic Viterbi segmentation over escaped whitespace pieces.

After building `tools/build/test_coli_gemma_generate`, an opt-in real-weight
smoke can generate exactly one greedy token from a converted Gemma BMOQ and
Gemma CSPM tokenizer asset:

```sh
tools/build/test_coli_gemma_generate /usb/model.bmoq /usb/tokenizer.cspm "hi"
```
