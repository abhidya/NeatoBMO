# ESP32 Colibri compatibility

This component is the staging area for the standalone `esp32-colibri` ESP-IDF
package. Its compatibility target is the model-family surface published by
upstream Colibri v1.6.2. Compatibility means architecture-correct prompt-to-text
generation with the model and context state backed by attached storage; it does
not imply interactive speed on an ESP32-S3.

## Upstream Colibri families

| Family | Upstream engine | Upstream storage/attention shape | ESP32 status |
|---|---|---|---|
| OLMoE | `c/olmoe.c` | Streamed quantized experts, conventional multi-head KV, 4096-token context | In progress: BMOQ/CTOK conversion, Q4 layers, prompt-to-text generation, and 4096-token file-backed paged KV are implemented; physical SSD parity remains |
| GLM-5.2 | `c/colibri.c` | Group-quantized streamed experts, compressed MLA KV, optional MTP | Not ported |
| Inkling | `c/inkling.c` | Streamed routed experts, alternating local/global GQA, 8192-token default | Not ported |
| Kimi K3 | `c/kimi_k3.c` | Native MXFP4 experts, recurrent KDA plus MLA state | Not ported |
| DeepSeek V4 Flash | `c/deepseek_v4.c` | FP8/BF16 dense path, window and compressed sparse-attention state | Not ported |

Gemma is deliberately scheduled after the five upstream Colibri families. Its
existing experimental engine remains in this component so current Neato tests
continue to pass, but it is not part of the first standalone package milestone.

## Shared package boundary

Architecture engines may depend on these shared services only:

- 64-bit model storage reads and bounded writable KV storage;
- fixed-workspace quantized matrix/vector kernels;
- architecture-specific tokenizers behind a common encode/decode callback;
- cancellation and cooperative-yield callbacks;
- incremental decoded-token output callbacks;
- storage and latency telemetry.

An engine owns its tensor names, attention scheme, routing rules, normalization,
position encoding, sampling defaults, and model validation. A generic dispatcher
must not silently treat one family as another.

## Hardware truth

Upstream's documented host requirements range from roughly 8 GB RAM for OLMoE
to 32 GB or more for Kimi K3, with model assets from roughly 4 GB to 1.6 TB.
ESP32-S3 has orders of magnitude less resident memory and USB Full-Speed storage
bandwidth. The package therefore treats SSD as a correctness-enabling memory tier
for weights and context. Every engine must publish separate evidence for:

1. format and reference-token parity;
2. bounded resident memory at full configured context;
3. physical device generation;
4. measured cold and warm token latency;
5. whether the result is practical or only mechanically executable.

No family is marked supported until all five items have evidence.
