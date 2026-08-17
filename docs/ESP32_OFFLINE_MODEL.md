# Running a Colibri model on the ESP32 from USB storage

Offline inference on the body: the ESP32-S3 mounts a USB mass-storage volume,
streams a quantized model off it, and answers on `POST /v1/completions`. No
call leaves the device to produce a token.

## What is verified

Measured on the real board (ESP32-S3 rev v0.2, 16 MB flash, 8 MB octal PSRAM):

| | |
|---|---|
| Firmware build | RAM 13.4% (43968 / 327680), Flash 41.3% (1299741 / 3145728) |
| PSRAM | 8 MB detected, memory test OK, added to the heap allocator |
| SPIFFS `storage` | mounts, 9510641 bytes free |
| `coli_msc` | MSC class client installs and shares the app-owned USB host |
| Gemma inference | matches the Hugging Face reference to 2.4e-05 across all 18 layers |

Not yet verified on hardware: a model actually mounted and decoded on the
device. That needs the storage below.

## Storage requirements

The firmware mounts the **first FAT-formatted** USB MSC device at `/usb`, with
`format_if_mount_failed = false`. Consequences worth knowing before wiring
anything up:

* **APFS, exFAT and HFS+ do not work.** The device enumerates and then fails to
  mount, which in the boot log is indistinguishable from no disk at all.
* The drive must be on the **ESP32's USB host port**, not the Mac.
* Budget ~310 MB for gemma-3-270m: 288 MB `model.bmoq` + 23 MB `tokenizer.cspm`.

Either layout is probed, root first:

```
/usb/model.bmoq                                     /usb/tokenizer.cspm
/usb/neatobmo-models/gemma-3-270m-q8_0/model.bmoq   .../tokenizer.cspm
```

## Staging a drive

Format a USB stick (>= 512 MB) as **MS-DOS (FAT32)**, then:

```sh
python3 esp32-body/tools/deploy_model_usb.py /Volumes/BMO \
  --model     /Volumes/2TB/neatobmo-models/gemma-3-270m-q8_0/model.bmoq \
  --tokenizer /Volumes/2TB/neatobmo-models/gemma-3-270m-q8_0/tokenizer.cspm
```

It refuses a non-FAT volume, checks free space, and re-hashes every byte after
copying. It never formats or partitions.

## Building a model

Only **already-quantized downloads** are used. This repo does not quantize, and
must not: re-quantizing a published Q8_0 down to Q4 destroyed model quality
outright when it was tried.

```sh
python3 esp32-body/tools/export_gemma_gguf_bmoq.py \
  gemma-3-270m-Q8_0.gguf model.bmoq
```

The export is a lossless repack: int8 codes are copied verbatim and f16 block
scales widen exactly to f32. It is byte-exact against the source GGUF, so the
device runs precisely the published quantization.

Gemma is currently the only architecture with an exporter. `coli_runtime_generate`
also dispatches OLMoE and GLM-5.2, but neither has a build path here, and
GLM-5.2 has only ever run against synthetic fixtures — which is exactly the
state Gemma was in while it silently produced garbage.

## Checking it on the host first

`coli-run` drives `coli_runtime_generate`, the same entry point the firmware
calls, against host files:

```sh
make -C esp32-body/tools build/coli-run
esp32-body/tools/build/coli-run model.bmoq tokenizer.cspm "BMO is" 16
```

Coherent text here means the model file and the runtime agree. Do this before
staging a drive; it is far faster than debugging over a serial log.

## On device

```sh
pio run -e esp32s3 -t upload --upload-port /dev/cu.usbmodem<id>
```

Watch the boot log at 115200. A mounted drive logs through `coli_msc`; without
one you see the MSC client install and then nothing further about storage.

```sh
curl -N -X POST http://<device-ip>/v1/completions \
  -d '{"prompt":"BMO is","max_tokens":16}'
```

The response is server-sent `data:` chunks of decoded text. It carries **no
stage cues** — no `[happy]`, no `[sound:beep]`. Sound, face and voice are
orchestrated host-side by `neatobmo/cues.py` against separate endpoints; the
on-device model emits plain text only.

## Known gaps

* The body still joins wifi on boot (`wifi_mgr`). Inference is offline, but the
  device associates. Disable that separately if the goal is a dark radio.
* `CONFIG_COLI_MCU_CONTEXT_TOKENS` defaults to 4096 for OLMoE; the Gemma demo
  path uses 16, which is a smoke test, not a usable context.
* Read bandwidth off USB MSC is unmeasured, and it decides tokens/sec far more
  than the kernels do. Measure it before optimizing anything else.
