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
- ✅ Web dashboard (lidar radar, command console, drive pad) served from the Mac —
  [neato_dashboard.py](neato_dashboard.py); ESP32-hosted version in progress
  ([esp32-body/src/web.c](esp32-body/src/web.c) + OTA).
- 🔜 Behavior library (sounds/dance/LCD faces), Colibri LAN serving, voice loop.

## Repo layout

| Path | What |
|---|---|
| `DESIGN.md` | Architecture design doc (body/head/brain split, OSS stack, milestones) |
| `esp32-body/` | ESP32-S3 firmware (PlatformIO + ESP-IDF): USB CDC-ACM host ↔ Neato, WiFi log mirror (`:2323`), raw command bridge (`:3333`), embedded web UI + OTA (WIP) |
| `neato_dashboard.py` | LAN dashboard: lidar radar, console with full command set, drive controls |
| `lidar_viewer.py` | Original USB lidar visualizer (M0-era debug tool) |
| `neato_protocol_dump.txt` | Ground-truth protocol harvested from the actual XV-12 (`Help` for every command, sample sensor output) |
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

## Firmware quick start

```bash
cd esp32-body
cp src/wifi_secrets.example.h src/wifi_secrets.h   # fill in your WiFi
pio run -t upload   # first flash over USB; later flashes via OTA (POST /ota)
```

Then: `http://<board-ip>/` (dashboard), `nc <board-ip> 2323` (logs),
`nc <board-ip> 3333` (raw Neato commands).
