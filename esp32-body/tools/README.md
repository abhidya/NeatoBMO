# coli_mcu host checks

Run the portable BMOQ parser and tiled-read test from `esp32-body`:

```sh
cc -std=c11 -D_FILE_OFFSET_BITS=64 -Wall -Wextra -Werror \
  -Icomponents/coli_mcu/include \
  components/coli_mcu/model_format.c components/coli_mcu/store_file.c \
  tools/test_bmoq.c -o /tmp/test_bmoq && /tmp/test_bmoq
```

The fixture contains a deterministic 16 MiB tensor and the test reads only
small tiles at distant offsets, including a tile crossing a 4 KiB boundary.

Run the streamed Q4 golden test:

```sh
cc -std=c11 -D_FILE_OFFSET_BITS=64 -D_POSIX_C_SOURCE=200809L \
  -Wall -Wextra -Werror -Icomponents/coli_mcu/include \
  components/coli_mcu/model_format.c components/coli_mcu/store_file.c \
  components/coli_mcu/q4_matvec.c tools/test_q4_matvec.c -lm \
  -o /tmp/test_q4_matvec && /tmp/test_q4_matvec
```

It streams a deterministic Q4 matrix whose packed weights exceed 8 MiB,
compares all 40,000 output rows with an independent formula reference, and
asserts a 257-byte caller workspace plus cross-4-KiB reads. To retain a fixture
for device testing, run `python3 tools/export_bmoq_q4_fixture.py model.bmoq`.

Run the portable transformer primitive checks:

```sh
cc -std=c11 -Wall -Wextra -Werror -Icomponents/coli_mcu/include \
  components/coli_mcu/ops.c tools/test_coli_ops.c -lm \
  -o /tmp/test_coli_ops && /tmp/test_coli_ops
```

This covers heap-free caller-buffer RMSNorm, RoPE, single-token causal
attention decode, residual add, SiLU/gated multiply, and bounded KV-cache
layout math for OLMoE hidden=2048, 16-head attention.

Run the single-layer OLMoE decode check:

```sh
cc -std=c11 -D_FILE_OFFSET_BITS=64 -D_POSIX_C_SOURCE=200809L \
  -Wall -Wextra -Werror -Icomponents/coli_mcu/include \
  components/coli_mcu/model_format.c components/coli_mcu/store_file.c \
  components/coli_mcu/q4_matvec.c components/coli_mcu/ops.c \
  components/coli_mcu/moe.c components/coli_mcu/olmoe.c \
  tools/test_coli_olmoe_layer.c -lm -o /tmp/test_coli_olmoe_layer && \
  /tmp/test_coli_olmoe_layer
```

It connects dense RMSNorm tensors, streamed Q/K/V/O Q4 projections, RoPE,
KV-cache writes, causal single-token attention, residuals, and streamed MoE for
one decoder layer against an independent identity-weight reference.

Run the streamed MoE golden test:

```sh
cc -std=c11 -D_FILE_OFFSET_BITS=64 -D_POSIX_C_SOURCE=200809L \
  -Wall -Wextra -Werror -Icomponents/coli_mcu/include \
  components/coli_mcu/model_format.c components/coli_mcu/store_file.c \
  components/coli_mcu/q4_matvec.c components/coli_mcu/moe.c \
  tools/test_coli_moe.c -lm -o /tmp/test_coli_moe && /tmp/test_coli_moe
```

It runs a deterministic router, verifies top-8 tie-breaking and unnormalized
softmax routing weights, executes selected gate/up/down experts serially, and
compares the final hidden vector with an independent dense reference.

Run the CTOK tokenizer substrate check:

```sh
cc -std=c11 -D_FILE_OFFSET_BITS=64 -D_POSIX_C_SOURCE=200809L \
  -Wall -Wextra -Werror -Icomponents/coli_mcu/include \
  components/coli_mcu/store_file.c components/coli_mcu/tokenizer.c \
  tools/test_coli_tokenizer.c -o /tmp/test_coli_tokenizer && \
  /tmp/test_coli_tokenizer
```

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
