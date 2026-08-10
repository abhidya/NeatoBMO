# coli_mcu foundation

This component is an ESP-IDF-native storage and model-format foundation. It is
not an OLMoE runtime yet.

The application owns the one USB Host Library installation and its daemon.
`coli_mcu_start()` installs only Espressif's MSC class client. The M0 task mounts
the first FAT-formatted MSC device at `/usb`, prints descriptors and capacity,
and benchmarks bounded reads of `/usb/model.bmoq` if that file exists.

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

This is not token generation or an OLMoE implementation. The upstream
OLMoE-1B-7B-0924 shape (16 layers, hidden size 2048, 64 experts with top-8
routing) still requires attention, routing, expert scheduling, KV state, and
the remaining kernels. Its roughly 1.3B active parameters per token make
bytes-touched scheduling the next system constraint; this primitive only proves
that one quantized matrix can be evaluated without fitting in PSRAM.

## Gemma 3 path

`coli_gemma_*` is a separate dense Gemma 3 decode path. It uses the shared BMOQ
reader and streamed Q4 kernels, but does not assume OLMoE tensor names or MoE
routing. The current Gemma converter targets the local
`gemma-3-270m-Q8_0.gguf` shape: hidden 640, 18 layers, 4 query heads, 1 KV head,
256-wide Q/K/V heads, 2048 FFN width, 262144 tokens, and tied embeddings. The
runtime applies Gemma's `(1 + weight)` RMSNorm convention, Q/K head norms, RoPE,
GQA decode attention, post-attention/post-FFN norms, gated FFN, and streamed
output argmax.
