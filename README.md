# NeatoBMO 🤖

An always-on, BMO-style companion robot built from a **Neato XV-12** robot vacuum,
an **ESP32-S3**, and a **local LLM** — everything open source, everything on the LAN,
nothing in the cloud.

The robot chats, performs (soundboard + dance moves + LCD faces), speaks with a
neural BMO voice clone through its own speaker, and answers instantly to
everyday phrases without waking the model. See [DESIGN.md](DESIGN.md) for the
full architecture and milestones.

## Features

- **Web console** ([bmo_web.py](bmo_web.py), `:8485`) with an Adventure Time
  skin: chat, soundboard, TTS, raw command console, lidar/battery sensors, and
  ESP32 OTA — the header is a mini BMO screen playing animated sprite faces
  that react to replies, think, blink, glance around, and doze off.
- **Voice in**: browser mic (Chrome/Edge) with a **"hey BMO" wake word**.
- **Voice out**: reviewed authentic BMO catalog clips stream natively through
  the robot's speaker with **no flash write**; synthetic speech uses the neural
  BMO clone (Piper prosody → RVC timbre,
  [tools/bmo_voice_server.py](tools/bmo_voice_server.py)) packed into
  validated sound-flash banks; espeak-ng fallback.
- **Instant routines** ([neatobmo/routines.py](neatobmo/routines.py)): a
  Siri-style pattern layer answers everyday utterances ("hello!", "dance!",
  "wanna play a game?") from precached scripts with zero model latency, with
  a small state machine for multi-turn follow-ups.
- **Stage cues** ([neatobmo/cues.py](neatobmo/cues.py)): best-effort "tool
  calling" for the small local model — replies carry bracketed cues
  (`[happy] [wiggle] [sound:videogames]`) that a forgiving parser turns into
  faces, sounds, and dance moves performed on the body. Default **soundboard**
  mode answers with a reviewed authentic clip and lets the performance carry
  the reply; `soundbyte` caps spoken words to a short burst.
- **Emoji faces** on the Neato's LCD over both paths: ESP32
  ([esp32-body/src/faces.c](esp32-body/src/faces.c)) and a 1:1 Python port
  ([neatobmo/emote.py](neatobmo/emote.py)) used automatically over USB.

## Hardware

| Part | Role |
|---|---|
| Neato XV-12 | Body: wheels, 360° lidar, buttons, LCD, speaker, dock |
| ESP32-S3 devkit (USB-OTG capable, e.g. YD N16R8) | USB-hosts the Neato, bridges it to WiFi, serves its own dashboard |
| Mac/PC on the LAN | Runs the web console, the OLMoE brain, and the neural voice server |

### Hard-won hardware notes

- Permanent installation plan: [power the ESP32-S3 and wire its USB host
  directly to the Cruz motherboard](docs/esp32-neato-direct-wiring.md). It
  preserves the Neato LCD, uses a fused battery-to-5 V automotive supply, and
  includes board-revision-safe pad identification and bring-up checks.

- The Neato's USB port is a **device** port → the controller must be a USB
  **host**: ESP32-**S3** required (OTG), classic ESP32 won't work.
- On YD-style ESP32-S3 devkits the native USB port ships with **no 5 V
  output**; bridge the **`USB-OTG`** solder jumper (NOT `IN-OUT`) so the port
  can power the robot's transceiver, or put a powered hub in between.
- ESP-IDF's USB host stack ships with **hub support disabled**
  (`CONFIG_USB_HOST_HUBS_SUPPORTED=y` in `sdkconfig.defaults` enables it).
- The ESP32 controller binds the Neato CDC device by USB VID:PID
  **`2108:780B`**, not by a persistent `/dev` path. Stock-updater reboots do
  not always cause macOS to recreate the serial device; rediscover it by
  VID:PID and verify `GetVersion`, with a physical or controllable-hub VBUS
  cycle as the fallback when the device is absent. Reopening a stale pathname
  cannot recover a device that is no longer enumerated.
- The Neato replies in ASCII, each response terminated by `0x1A` (Ctrl-Z).
  A full 360° lidar scan is ≈ 14 KB — size read timeouts accordingly.
- The LCD draws **full-span lines only** (`SetLCD HLine <row>` /
  `VLine <col>`); a trailing number is parsed as a *Contrast* value and
  written to NAND. Faces are therefore carved from full-span bands
  (see [docs/lcd-rendering-investigation.md](docs/lcd-rendering-investigation.md)).
- For passive boot-log capture, connect mainboard `P6.3` (robot TX) to ESP32
  GPIO18 and `P6.4` to GND. Leave `P6.2` disconnected unless transmit access is
  explicitly needed; it may optionally connect to GPIO17. The raw 115200 8N1
  stream is available over the diagnostic bridge. P6 exposes Neato boot/update
  logs on a healthy board, **not plaintext firmware or a SAM-BA unlock**.
- Hardware-research state (2026-08-15): exact Cruz-P builds 2.5.15893,
  2.7.16621, and 3.1.17844 were installed and returned to 2.5.15893; BACK
  still boots factory 2.4.15667. P10 produced no stable TAP during repeated
  pre-, during-, or post-update scans, and no tested firmware exposed
  application readback. See
  [captures/README.md](captures/README.md) and
  [FIRMWARE_ARCHIVE.md](FIRMWARE_ARCHIVE.md).

## Getting started

### Prerequisites

- **Python 3.10+** with `pyserial` (`pip install pyserial`) — everything else
  in the core stack is standard library.
- **espeak-ng** for fallback TTS: `brew install espeak-ng` (macOS) or your
  distro package.
- **PlatformIO** (`pip install platformio`) to build/flash the ESP32 firmware.
- Optional, for the local LLM brain: a Colibri OLMoE engine build + model
  snapshot (paths configurable, see below).
- Optional, for the neural BMO voice: a dedicated Python 3.12 venv at
  `~/.neatobmo/voice-venv` with `piper-tts`, `torch`, and `rvc-python`, plus
  the models under `~/.neatobmo/voices/` (Piper `en_US-amy-medium.onnx` and a
  BMO RVC checkpoint). Without it, speech falls back to espeak-ng.

### 1. Flash the ESP32 body

```bash
cd esp32-body
pio run -t upload   # first flash over USB; later flashes via OTA (POST /ota)
```

The project pins PlatformIO's application upload offset to `0x20000`, matching
the first slot in `partitions.csv`. Do not override it with the ESP-IDF default
`0x10000`; that address is outside this project's bootable OTA slots.

A fresh flash boots a **`NeatoBMO-Setup`** WiFi AP: join it and browse to
`http://192.168.4.1/wifi` to save your WiFi credentials (persisted in NVS —
no secrets are compiled into the firmware).

Then: `http://<board-ip>/` (embedded dashboard), `nc <board-ip> 2323` (logs),
`nc <board-ip> 3333` (raw Neato commands).

### 2. Run the web console

```bash
python3 bmo_web.py
```

Open `http://localhost:8485`. The console connects to the robot over USB
serial if attached, otherwise through the ESP32 bridge. On startup it also
auto-starts, when installed on this machine:

- the **Colibri OLMoE brain** (`bmo_brain_server.py`, `:8000`) — chat degrades
  gracefully while the model loads;
- the **neural BMO voice server** (`tools/bmo_voice_server.py`, `:8486`) —
  espeak-ng answers until it is up.

Logs for both land in `logs/`.

### Configuration

All knobs are environment variables with sensible defaults:

| Variable | Default | Meaning |
|---|---|---|
| `PORT` | `8485` | Web console port |
| `NEATOBMO_ESP32` | `http://10.0.0.106` | ESP32 body board base URL |
| `NEATOBMO_BRAIN` | `http://127.0.0.1:8000/v1` | OpenAI-compatible brain endpoint |
| `NEATOBMO_BRAIN_ENGINE` | `/Volumes/2TB/colibri.../olmoe` | Colibri engine binary (auto-start) |
| `NEATOBMO_BRAIN_SNAP` | `/Volumes/2TB/models/olmoe-snap` | OLMoE model snapshot (auto-start) |
| `NEATOBMO_VOICE` | `http://127.0.0.1:8486` | Neural BMO voice server |
| `NEATOBMO_SPEECH` | `soundboard` | `soundboard` answers with a reviewed clip first (synth fallback); `soundbyte` caps spoken words to a short burst; `full` speaks whole replies |
| `NEATOBMO_SPEECH_BURST` | `1.5` | Soundbyte burst length in seconds |
| `NEATOBMO_SOUNDBOARD_CATALOG` | `docs/bmo-soundboard/catalog.json` | Soundboard clip catalog path |

## Local development

```bash
# run the test suite (pure-Python, no robot needed)
PYTHONPATH=".:tools" python3 -m unittest discover -s tests
```

- The web console honors `PORT`, so a second dev instance can run next to a
  production one — but only **one process may own the robot's USB serial
  port** at a time.
- `.claude/launch.json` carries dev-server launch configs (`bmo-web`,
  `bmo-brain`, `lidar-viewer`) for IDE/agent tooling.
- The stage-cue pipeline has an offline demo (`python3 tools/cue_demo.py`,
  `--live` to perform on the robot) and a latency profile
  (`tools/cue_profile.py`).
- The ESP32 web UI can be exercised without hardware via
  `tools/esp32_web_simulator.py`.
- Firmware iterates fastest over OTA: `pio run`, then POST the built `.bin`
  to `http://<board-ip>/ota` (the console's ESP32 tab does this for you).

### Safety rails (read before touching the sound flash)

Writing the robot's sound flash is a **destructive, low-level operation**.
Every write path is gated: only byte-exact, SHA-256-verified 770,048-byte
bank images are ever sent, and installs verify the robot end-to-end
afterwards. Read [SOUND_BANK_WRITE_GATES.md](SOUND_BANK_WRITE_GATES.md) and
[docs/SOUND_BANK_UPDATE.md](docs/SOUND_BANK_UPDATE.md) first; the forensic
history lives in [docs/sound-burn-forensics.md](docs/sound-burn-forensics.md).

## Repo layout

| Path | What |
|---|---|
| `bmo_web.py` | The one web console (`:8485`): chat + routines + stage cues, TTS speech, soundboard, guarded bank install/restore, raw console, sensors, OTA proxy |
| `bmo_brain_server.py` | OpenAI-compatible wrapper around Colibri's OLMoE + espeak-ng TTS |
| `bmo_agent.py` | CLI tool-calling agent (drive/sounds/LED via any OpenAI-compatible LLM) |
| `neatobmo/` | Robot library: transports, typed commands, sounds, behaviors, emoji faces (`emote.py`), stage cues (`cues.py`), instant routines (`routines.py`), compound-turn planner (`turns.py`), soundboard clip voice (`soundboard_voice.py`), TTS banks (`tts_bank.py`) |
| `esp32-body/` | ESP32-S3 firmware (PlatformIO + ESP-IDF): USB CDC-ACM host ↔ Neato, dashboard + WS bridge + `/emote` + `/speak` + streaming `/soundbank` + OTA (`:80`), log mirror (`:2323`), raw bridge (`:3333`), debug-UART bridge (`:3334`) |
| `esp32-body/components/coli_mcu/` | On-MCU brain runtime: USB-MSC storage, BMOQ model format, streamed Q4 kernels, OLMoE prompt-to-text (MoE + KV cache + streamed attention) plus experimental Gemma/GLM-5.2 paths |
| `tools/` | Voice server, sound-bank builders/probes, firmware archive/patch tooling, lidar viewer, cue demo/profiler, ESP32 web simulator |
| `tests/` | Unit tests (see Local development) |
| `assets/` | Captured sound-bank evidence, validated BMO bank, original recovery image |
| `docs/` | Deep-dive docs + the GitHub Pages data host for the remote soundboard (`docs/bmo-soundboard/`) |
| `neato-driver-python/` | Vendored [brannonvann/neato-driver-python](https://github.com/brannonvann/neato-driver-python) (MIT) for protocol reference |
| `neato_protocol_dump.txt` | Ground-truth protocol harvested from the actual XV-12 |

## Documentation index

| Doc | Topic |
|---|---|
| [DESIGN.md](DESIGN.md) | Architecture (body/head/brain split), OSS stack, milestones, as-built notes |
| [docs/TTS_BANK.md](docs/TTS_BANK.md) | How BMO speaks through the sound flash: chunking, validation, persistent banks |
| [docs/SOUND_BANK_UPDATE.md](docs/SOUND_BANK_UPDATE.md) | Exact hashes + guarded procedures for installing/restoring sound banks |
| [SOUND_BANK_WRITE_GATES.md](SOUND_BANK_WRITE_GATES.md) | The gate checklist every sound-flash write must pass |
| [docs/BMO_REMOTE_SOUNDBOARD.md](docs/BMO_REMOTE_SOUNDBOARD.md) | The GitHub-Pages-hosted module catalog the soundboard streams from |
| [FIRMWARE_SOUND_PATCH.md](FIRMWARE_SOUND_PATCH.md) | The XV `PlaySound File` firmware patch investigation (native runtime speech) |
| [FIRMWARE_ARCHIVE.md](FIRMWARE_ARCHIVE.md) | Checksummed firmware archive, recovery snapshot, reproduction commands |
| [docs/lcd-rendering-investigation.md](docs/lcd-rendering-investigation.md) | Live-probed LCD ground truth (full-span lines, contrast pitfall) |
| [docs/sound-burn-forensics.md](docs/sound-burn-forensics.md) | Forensic record of sound-flash write behavior |
| [docs/usb-os-observability.md](docs/usb-os-observability.md) | What the XV OS exposes over USB |
| [docs/hardware-readback-options.md](docs/hardware-readback-options.md) | Options for reading firmware off the MCU |
| [docs/neato-os-research-index.md](docs/neato-os-research-index.md) | Index of the OS research notes |
| [docs/compound-turn-orchestration-design.md](docs/compound-turn-orchestration-design.md) | Compound-turn planner design + NDJSON event contract (implemented) |
| [docs/bmo-voice-research.md](docs/bmo-voice-research.md) | Soundboard clip catalog, trust/quarantine model, and voice ladder |
| [docs/neatoos-source-census.md](docs/neatoos-source-census.md) | Census of NeatoOS source slices and probe tools |
| [docs/neatoos-execution-probe.md](docs/neatoos-execution-probe.md) | NeatoOS execution-probe experiments and checksum-gate result |
| [docs/neato-p10-jtag-result.md](docs/neato-p10-jtag-result.md) | P10 JTAG no-TAP result (security-bit verdict) |
| [docs/neato-serial-upload-readback-plan.md](docs/neato-serial-upload-readback-plan.md) | Serial upload-save-area matrix plan and gated harness |
| [docs/neato-hardware-access.md](docs/neato-hardware-access.md) | Case/mainboard access, P6/P10 pinouts, J3 ERASE warning |
| [docs/esp32-neato-direct-wiring.md](docs/esp32-neato-direct-wiring.md) | Permanent ESP32-S3→Cruz direct-wiring installation plan |
| [docs/esp32-neato-usb-resilience.md](docs/esp32-neato-usb-resilience.md) | ESP32 USB CDC re-enumeration/resilience design |
| [docs/neato-envelope-crypto.md](docs/neato-envelope-crypto.md) | `.enc` envelope crypto analysis |

## Status

- ✅ **M0** — ESP32-S3 USB-hosts the Neato and bridges it to WiFi; full
  command round-trip over the LAN.
- ✅ Web console with Adventure Time skin, animated sprite-face avatar,
  wake-word voice input, and live connection status.
- ✅ Instant routine layer + compound-turn planner + stage-cue performances (clip-first).
- ✅ Neural BMO voice (Piper → RVC) speaking through the robot's own speaker
  via validated sound-flash banks.
- 🚧 Native streamed speech awaits the XV `PlaySound File` firmware patch
  ([FIRMWARE_SOUND_PATCH.md](FIRMWARE_SOUND_PATCH.md)); until then any
  sound-flash write is governed by the write gates.
- 🚧 On-MCU brain runtime (`coli_mcu`): OLMoE prompt-to-text works; physical
  device latency/parity still being proven.

## License & credits

Personal research project. The vendored
[neato-driver-python](https://github.com/brannonvann/neato-driver-python) is
MIT-licensed by its author. BMO and Adventure Time are trademarks of their
respective owners; this is a fan-made homage, not an affiliated product.
