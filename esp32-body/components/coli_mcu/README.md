# ESP32 Colibri (`coli_mcu`)

This directory is a Git-importable ESP-IDF component and the staging source for
the standalone ESP32 Colibri repository. It currently has an OLMoE BMOQ+CTOK prompt-to-text path and a
separate experimental Gemma path. See [COMPATIBILITY.md](COMPATIBILITY.md) for
the upstream-family matrix and the evidence required before claiming support.

Until the standalone repository exists, pin this component directly from the
NeatoBMO repository using ESP-IDF Component Manager:

```yaml
dependencies:
  coli_mcu:
    git: https://github.com/abhidya/NeatoBMO.git
    path: esp32-body/components/coli_mcu
    version: e8687e6
```

The manifest intentionally restricts the target to ESP32-S3. When the package
is split into its own repository, consumers can omit `path`; no source layout
change inside the component is required.

The application owns the one USB Host Library installation and its daemon.
`coli_mcu_start()` installs only Espressif's MSC class client. The M0 task mounts
the first FAT-formatted MSC device at `/usb`, prints descriptors and capacity,
and benchmarks bounded reads of `/usb/model.bmoq` if that file exists.
The mount path and OLMoE model, tokenizer, and KV filenames are configurable in
menuconfig. The autorun generation demo is disabled by default for package
consumers; NeatoBMO enables it in `sdkconfig.defaults`. When enabled and the
configured model and tokenizer exist, the component starts a low-priority
`coli_generate` task. The task reads `/usb/prompt.txt` when present
or falls back to a short fixed prompt, opens the BMOQ model and CTOK tokenizer,
encodes the prompt, runs the OLMoE greedy token loop with its 4096-token logical
context backed by `/usb/olmoe.kv`, and
logs decoded chunks. It yields through callback hooks and cancels on MSC
removal; CDC/safety work must remain higher priority than this demo.

OLMoE KV data now uses a writable file backend with a fixed 64 KiB resident
page. Host tests compare it against the original contiguous-RAM attention path.
This removes the full-context PSRAM allocation, but physical FAT/USB behavior and
latency are not yet proven.

The tensor layer uses a 64-bit `read_at` interface and never assumes that a
tensor fits in PSRAM. BMOQ stores a 4 KiB little-endian header, a fixed 64-byte
tensor directory, and aligned tensor payloads. Offsets are byte offsets within
the BMOQ file, allowing the public MSC VFS interface to be used today. The
directory starts after the fixed header and may contain up to 4096 entries;
tensor extents follow it at converter-chosen alignment boundaries.

No `esp_private` MSC headers are included. Espressif exposes raw sector helpers
only as deprecated/private APIs, so this slice intentionally uses its supported
VFS mount API. If raw extents become necessary, their use belongs in one future
storage backend here, never in tensor or inference code.

## Streamed Q4 matrix-vector kernel

`coli_q4_matvec()` is the first compute slice. A matrix uses two aligned BMOQ
tensors:

- `BMOQ_DTYPE_Q4_SYM` + `BMOQ_LAYOUT_Q4_ROW_MAJOR`: signed nibbles in
  row-major order, low nibble first, with no row padding.
- `BMOQ_DTYPE_F32` + `BMOQ_LAYOUT_GROUP_SCALES_F32`: one little-endian scale
  per row/group.

Columns must be an exact multiple of an even group size. The parser checks
dimension multiplication, exact payload length, 4 KiB extent alignment,
ordered non-overlap, and file bounds. The kernel takes caller-owned workspace,
allocates nothing, groups as many quantization groups as fit in each tile, and
reports exact weight/scale bytes read, storage calls, page-boundary crossings,
and peak workspace use.

The OLMoE path is intentionally OLMoE BMOQ only: CTOK byte-level BPE tokenizer,
Q4 embeddings/projections/LM head, dense norm vectors, RoPE, KV cache, streamed
attention, and streamed MoE. It does not imply Gemma compatibility.

## Gemma 3 path

`coli_gemma_*` is a separate dense Gemma 3 decode path. It uses the shared BMOQ
reader and streamed Q4 kernels, but does not assume OLMoE tensor names or MoE
routing. The current Gemma converter targets the local
`gemma-3-270m-Q8_0.gguf` shape: hidden 640, 18 layers, 4 query heads, 1 KV head,
256-wide Q/K/V heads, 2048 FFN width, 262144 tokens, and tied embeddings. The
runtime applies Gemma's `(1 + weight)` RMSNorm convention, Q/K head norms, RoPE,
GQA decode attention, post-attention/post-FFN norms, gated FFN, and streamed
output argmax.

`coli_spm_*` is the matching Gemma SentencePiece tokenizer path. The
`export_gemma_tokenizer.py` tool reads `tokenizer.ggml.tokens`, scores,
`token_type`, and BOS/EOS/PAD/UNK metadata from the local GGUF and writes a
compact `CSPM` file. Token entries and the trie stay in `coli_store_t`; encode
uses bounded per-input Viterbi state and byte fallback tokens rather than
loading the 262k vocabulary into SRAM.
