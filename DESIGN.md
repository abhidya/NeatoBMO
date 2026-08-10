# BMO-Bot: Always-On LLM Robot — Design Doc

**Goal:** An always-on, BMO-style companion robot with a face, voice, eyes, and wheels.
It listens for a wake word, sees through a webcam, talks back, drives around on a Neato XV-12
base, and thinks with a **local LLM** — everything open source, nothing leaving the LAN.

**Status:** Draft v1 · 2026-08-09 — see §0 for where the build diverged from this plan.

---

## 0. As built (2026-08-09)

The plan below predates the implementation; these decisions supersede it:

- **No MQTT.** The body's transport is the ESP32's own HTTP server (`web.c`):
  embedded dashboard at `/`, WebSocket `/ws` raw serial bridge for browsers,
  raw TCP `:3333` for programmatic clients, plus `/emote` (emoji → LCD face
  cascades, `faces.c`) and `/speak` (WAV relay). No broker on the LAN.
- **No Wyoming stack yet.** Voice is Colibri: `bmo_brain_server.py` wraps the
  OLMoE chat engine and espeak-ng TTS behind an OpenAI-compatible API;
  `bmo_web.py` (`:8485`) is the orchestrator (browser mic for STT for now).
- **The face lives on the Neato's own LCD first.** `faces.c` on the ESP32 and
  its 1:1 Python port `neatobmo/emote.py` draw the same emoji faces over WiFi
  or USB; the projector head (§3) remains future work.
- **Native speech is gated.** The `PlaySound File` firmware patch path and the
  sound-flash write gates are tracked in FIRMWARE_SOUND_PATCH.md and
  SOUND_BANK_WRITE_GATES.md — no flash writes until every gate passes.

---

## 1. System overview

Two-node architecture. The **body** (ESP32 on the Neato) handles real-time robot control.
The **head** (Chromebook motherboard bolted into a projector) handles perception, speech,
the face, and talking to the LLM. Heavy LLM inference can run on the head if the model is
small, or on any other box on the LAN (e.g. your Mac) — the transport is the same either way.

```
                       Wi-Fi LAN
  ┌────────────────────────┼─────────────────────────────┐
  │                        │                             │
┌─┴──────────────┐   ┌─────┴──────────────────┐   ┌──────┴─────────┐
│ BODY           │   │ HEAD (brain + face)    │   │ LLM SERVER     │
│ ESP32-S3       │   │ Chromebook mobo in     │   │ (optional —    │
│  · USB host →  │   │ projector chassis      │   │ any LAN box,   │
│    Neato serial│   │  · Linux + 4GB/SSD     │   │ e.g. your Mac) │
│  · battery mon │   │  · mic + webcam        │   │  · Ollama /    │
│  · bump/estop  │   │  · wake word, STT, TTS │   │    llama.cpp   │
│  MQTT/WebSocket│   │  · face renderer →     │   │  OpenAI-compat │
│  over Wi-Fi    │   │    projector output    │   │  HTTP API      │
└────────────────┘   │  · orchestrator agent  │   └────────────────┘
                     └────────────────────────┘
```

Design principle: **the ESP32 never blocks on the LLM.** It owns safety (cliff/bump stop,
watchdog motor cutoff) with a dumb reflex loop; intelligence is advisory and asynchronous.

---

## 2. Body: ESP32 + Neato XV-12

### Why the Neato is a great base
Already proven working in this project: the XV-12 speaks a plain-text serial protocol over
its USB port (`TestMode On`, `SetMotor`/`SetMotorWheels`, `GetLDSScan`, `GetAnalogSensors`,
`PlaySound`, `SetLED`). It has a 360° lidar, drop/bump sensors, a big NiMH battery, and a
charging dock. That's a free SLAM-capable mobility platform.

### ESP32 choice: **ESP32-S3** (required, not optional)
The Neato's port is a **USB device port**, so the microcontroller must be a **USB host**.
Only the S3 (and S2) have OTG host support; classic ESP32 does not.

- Board: any ESP32-S3 dev board with the OTG port exposed (S3-DevKitC, ~$8).
- Driver: ESP-IDF's `cdc_acm_host` component — the Neato enumerates as standard CDC-ACM,
  same class of device the ESP-IDF example code targets.
- Fallback if USB host fights us: a Raspberry Pi Zero 2 W (~$15) as the body controller
  instead. Less "pure ESP32" but battle-tested (many Neato+Pi builds exist). Decide after
  a 1-day USB-host spike.

### Body firmware (custom, ESP-IDF or Arduino)
- Connects to Wi-Fi, maintains an **MQTT** session to the head (Mosquitto broker, OSS).
- Exposes topics: `body/drive` (v, ω or L/R mm+speed), `body/lidar` (compact scan @ ~2 Hz),
  `body/sensors` (battery, charge state, bumps, drops @ 5 Hz), `body/sound`, `body/led`.
- **Reflex layer runs on-chip:** if a drop sensor fires or comms go silent >500 ms →
  `SetMotor 0` immediately. The head can *request* motion; the body can always veto.
- Power: tap the Neato's battery (12–14.4 V NiMH) through a buck converter → 5 V for the
  ESP32. The robot's own dock keeps everything charged — free "always-on" power story.

---

## 3. Head: Chromebook motherboard in a projector

### Making the Chromebook board usable
- Flash **MrChromebox coreboot/UEFI firmware** (open source) so the board boots plain
  Linux from the SSD. (Assumption: "Colibri" on the SSD = your existing lightweight
  Linux setup — the doc works the same for Debian/Alpine/whatever is on it.)
- Wire the board's video output (or internal eDP→HDMI adapter board, ~$15) into the
  projector's input. Reuse the board's **built-in mic and webcam** — they're just USB
  devices on the ribbon; extend the ribbon or re-house them at the projector's front
  so BMO's "eyes and ears" point the right way.
- 4 GB RAM budget (see §5): the head runs the voice pipeline + face + orchestrator
  comfortably; it runs a small LLM only if we accept ~2 GB for the model.

### The face (the BMO part)
- **Renderer:** a fullscreen Chromium/`cage` kiosk showing an HTML5-canvas face —
  eyes, blinks, mouth visemes, emotion states. This is the same trick used by OSS robot
  faces; we write ~300 lines of canvas JS, driven over WebSocket by the orchestrator
  (states: idle / listening / thinking / speaking / happy / low-battery).
- Projected onto a wall or onto a diffusion screen on the robot — either works; the
  projector head can pan the face independently of the body.
- Lip sync: Piper TTS emits phoneme timing; map to 4–6 mouth shapes. Good enough for BMO.

---

## 4. Voice + vision pipeline (all open source)

The **Wyoming protocol** ecosystem (from Rhasspy/Home Assistant) gives us every stage as a
swappable service — this is the most active OSS voice stack right now:

| Stage | Component | Notes |
|---|---|---|
| Wake word | **openWakeWord** (Wyoming) | Always-on, runs on the head CPU; train a custom "Hey BMO" model with its OSS trainer |
| STT | **whisper.cpp / faster-whisper** (Wyoming) | `tiny.en`/`base.en` fit easily in RAM; runs fine on the Chromebook CPU |
| TTS | **Piper** | Fast, local, dozens of voices; pick a high-pitched one for BMO |
| VAD | **Silero VAD** | Bundled in the satellite pipeline; ends utterances cleanly |
| Orchestrator | **Custom Python agent** (~500 lines) | Glues Wyoming events ↔ LLM ↔ MQTT body ↔ face. Alternative: full Home Assistant Assist if you want its device ecosystem too |

**Vision:** grab webcam frames on demand (not continuously — RAM/CPU budget). Two options:
- Send the frame to the LLM server if it runs a vision model (**Qwen3-VL / LLaVA via
  Ollama**) — best quality, zero head-RAM cost.
- Or run **Moondream 2B quantized** on the head for simple "what do you see" (tight but
  possible in ~1.5 GB).
Lidar gives the spatial sense; the camera gives the semantic sense ("who's at the door").

---

## 5. The LLM

"Wi-Fi access to local LLM" — serve it with **Ollama** or **llama.cpp server** (both OSS,
both expose an OpenAI-compatible HTTP API, so the orchestrator doesn't care which):

| Where | Model | Fit |
|---|---|---|
| **LAN box (recommended)** — your Mac or any spare PC | Qwen3 8–14B / Llama 3.x 8B + a vision model | Best brains, head stays light; robot still fully local-network |
| **On the head (4 GB)** | Qwen3 1.7B / Llama 3.2 1B-3B, Q4 quant | Works for chat/persona; no vision headroom; ~1–2.5 GB resident |

Recommendation: **both.** Head runs a 1B "reflex brain" (fast small talk, offline
fallback) and prefers the LAN server when reachable. The orchestrator does the failover.

**Persona & memory:** system prompt gives the BMO persona; conversation memory in SQLite
on the SSD; tool-calling schema so the LLM can invoke `drive()`, `look()`, `scan()`,
`play_sound()`, `set_face(emotion)` — the orchestrator validates every call before it
touches MQTT (the LLM never gets raw motor access).

---

## 6. Milestones

1. **M0 — Spike (risk retirement):** ESP32-S3 USB-host ↔ Neato `GetVersion` echo. If this
   fails after a day, switch body controller to Pi Zero 2 W and move on.
2. **M1 — Body service:** MQTT drive/sensors/lidar topics + reflex stop; teleop from a
   laptop keyboard. (The lidar viewer from this repo becomes the debug dashboard.)
3. **M2 — Head boots:** MrChromebox flash, Linux on SSD, projector shows a blinking face.
4. **M3 — Voice loop:** wake word → whisper → LLM → Piper, ~2–4 s round trip. Talking BMO.
5. **M4 — Embodiment:** tool-calling wired to the body ("BMO, do a little spin"), camera
   Q&A, emotion faces, lip sync.
6. **M5 — Always-on polish:** auto-dock via Neato's `Clean` docking, battery-aware
   behavior, custom "Hey BMO" wake model, boot-to-BMO systemd units.

## 7. Risks / open questions

- **ESP32-S3 USB host vs. Neato quirks** — top technical risk; that's why it's M0.
  (Known-good escape hatch: Pi Zero 2 W.)
- **4 GB RAM ceiling** — voice stack + face + tiny LLM all fit, but not with a vision
  model; vision wants the LAN server.
- **Projector in the loop** — heat, noise, and power draw of a projector running 24/7;
  consider display-off idle with wake-on-wake-word.
- **Mic quality** — Chromebook mics are okay at 1–2 m; if wake word is flaky, add a $10
  USB conference mic.
- **XV-12 battery age** — NiMH packs from that era are usually tired; budget ~$40 for a
  replacement pack if runtime is poor.

## 8. Open-source shopping list (software)

MrChromebox firmware · Debian/Alpine · Mosquitto · ESP-IDF (`cdc_acm_host`) ·
wyoming-satellite · openWakeWord · faster-whisper / whisper.cpp · Piper · Silero VAD ·
Ollama / llama.cpp · Qwen3 / Llama 3.x / Moondream weights · Chromium kiosk face ·
this repo's `neato-driver-python` + `tools/lidar_viewer.py` for protocol reference and debugging.
