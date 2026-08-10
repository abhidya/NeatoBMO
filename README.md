# NeatoBMO 🤖

An always-on, BMO-style companion robot built from a **Neato XV-12** robot vacuum,
an **ESP32-S3**, and a **local LLM** — everything open source, everything on the LAN,
nothing in the cloud.

See [DESIGN.md](DESIGN.md) for the full architecture and milestones.

## Product goals

- **Embodied buddy:** wheels, 360° lidar, beeps, LED moods, a drawable LCD, and
  (via a projector head) a face — reacting to touch, pickup, and voice.
- **Local brain:** wake word → STT → local LLM (Colibri / Ollama on the LAN) → TTS,
  with tool-calling so the LLM can drive, look around, and emote.
- **Always on:** lives on its charging dock, wakes on "Hey BMO", auto-docks when low.

## Status

- ✅ **M0 complete** — ESP32-S3 USB-hosts the Neato and bridges it to WiFi.
  Proof: the robot beeps when the link opens; full command round-trip over the LAN.
- ✅ Web console consolidated into [bmo_web.py](bmo_web.py) (`:8485`): chat with the
  local brain, raw commands, lidar/battery, ESP32 OTA. The ESP32 serves its own
  embedded dashboard + WebSocket bridge ([esp32-body/src/web.c](esp32-body/src/web.c)).
- ✅ Emoji faces work over **both** paths: the ESP32's `/emote`
  ([esp32-body/src/faces.c](esp32-body/src/faces.c)) and a 1:1 Python port
  ([neatobmo/emote.py](neatobmo/emote.py)) used automatically over USB when the
  ESP32 is unreachable — same coordinates, same cascade timing.
- 🚧 Native voice transport complete — Colibri produces the Neato PCM format,
  and the ESP32 validates and relays WAV files over USB. The remaining gate is
  the XV `PlaySound File` firmware handler documented in
  [FIRMWARE_SOUND_PATCH.md](FIRMWARE_SOUND_PATCH.md); any sound-flash write is
  governed by [SOUND_BANK_WRITE_GATES.md](SOUND_BANK_WRITE_GATES.md) (several
  gates still failing — no writes until they pass).

## Repo layout

| Path | What |
|---|---|
| `DESIGN.md` | Architecture design doc (body/head/brain split, OSS stack, milestones) + as-built notes |
| `esp32-body/` | ESP32-S3 firmware (PlatformIO + ESP-IDF): USB CDC-ACM host ↔ Neato, embedded dashboard + WS bridge + `/emote` + `/speak` + OTA (`:80`), WiFi log mirror (`:2323`), raw command bridge (`:3333`), P6 debug-UART bridge (`:3334`) |
| `bmo_web.py` | The one web console (`:8485`): chat, BMO sound metadata/playback, guarded bank install/restore, console, sensors, emote, OTA proxy |
| `bmo_brain_server.py` | OpenAI-compatible wrapper around Colibri's OLMoE + espeak-ng TTS |
| `bmo_agent.py` | CLI tool-calling agent (drive/sounds/LED via any OpenAI-compatible LLM) |
| `neatobmo/` | Robot library: transports, typed commands, sounds, behaviors, emoji faces (`emote.py`) |
| `tools/` | Probe & archive utilities: `lidar_viewer.py`, `backup_neato.py`, `firmware_probe.py`, `neato_firmware.py`, offline `neato_cfw.py` version patch/verification, `neato_sound_bank.py`, `neato_sound_noburn_matrix.py` |
| `tests/` | Unit tests (`PYTHONPATH=".:tools" python3 -m unittest discover -s tests` from the repo root) |
| `assets/` | Captured sound-bank evidence, validated BMO bank/previews, and original recovery image |
| `docs/SOUND_BANK_UPDATE.md` | Exact hashes and guarded web/CLI procedures for installing BMO sounds or restoring the original bank |
| `docs/SOUND_BANK_TTS_PROMPT.md` | Implementation prompt for routing fixed BMO phrases through the bank and arbitrary speech through streamed WAV TTS |
| `docs/TTS_BANK.md` | BMO speaks: automatic TTS through the sound flash — sentence-boundary chunking for long text, validated persistent banks, silent verification, ESP32 `/soundbank` relay (`neatobmo/tts_bank.py` + web “TTS” tab) |
| `neato_protocol_dump.txt` | Ground-truth protocol harvested from the actual XV-12 (`Help` for every command, sample sensor output) |
| `FIRMWARE_ARCHIVE.md` | Checksummed 2 TB archive, current-robot recovery snapshot, compatible version inventory, and reproduction commands |
| `neato-driver-python/` | Vendored clone of [brannonvann/neato-driver-python](https://github.com/brannonvann/neato-driver-python) (MIT) for protocol reference |

## Hardware notes (hard-won)

- The Neato's USB port is a **device** port → the controller must be a USB **host**:
  ESP32-**S3** required (OTG), classic ESP32 won't work.
- On YD-style ESP32-S3 devkits the native USB port ships with **no 5 V output**;
  bridge the **`USB-OTG`** solder jumper (NOT `IN-OUT`) so the port can power the
  robot's transceiver, or put a powered hub in between.
- ESP-IDF's USB host stack ships with **hub support disabled**
  (`CONFIG_USB_HOST_HUBS_SUPPORTED=y` in `sdkconfig.defaults` enables it).
- The Neato replies in ASCII, each response terminated by `0x1A` (Ctrl-Z).
  Full 360° lidar scan ≈ 14 KB — size read timeouts accordingly.
- Native runtime speech needs a patch to the XV application's `PlaySound`
  handler. For plaintext firmware capture, connect mainboard `P6.2` (robot RX)
  to ESP32 GPIO17, `P6.3` (robot TX) to GPIO18, and `P6.4` to GND. The raw
  115200-baud debug stream is then available at `nc <board-ip> 3334`.

## Firmware quick start

```bash
cd esp32-body
cp src/wifi_secrets.example.h src/wifi_secrets.h   # fill in your WiFi
pio run -t upload   # first flash over USB; later flashes via OTA (POST /ota)
```

Then: `http://<board-ip>/` (dashboard), `nc <board-ip> 2323` (logs),
`nc <board-ip> 3333` (raw Neato commands).

Native WAV relay is `POST http://<board-ip>/speak` with `Content-Type:
audio/wav`. It accepts at most 512 KiB of mono signed 16-bit PCM at 22,050 Hz.
Until the XV application patch is installed, it intentionally returns HTTP 409
instead of reporting false success.
