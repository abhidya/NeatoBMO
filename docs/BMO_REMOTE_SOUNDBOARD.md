# Remote persistent BMO soundboard

The soundboard uses the XV-12's proven sound-flash format end to end. GitHub
Pages is a data host only; it does not run the UI and it does not serve WAV or
MP3 files.

## Published data

`docs/bmo-soundboard/` contains only:

- `catalog.json` — labels, provenance, module hashes, slot sequences, timing,
  and PCM offsets used by browser preview;
- `modules/*.bin` — exact 770,048-byte Neato sound modules.

The generated catalog currently covers 230 entries / 224 unique clips in 36
modules. Every module preserves the validated BMO bank directory, record
headers, page starts, declared lengths, and non-PCM bytes. Only PCM fields are
changed. Unused live slots contain silence.

The catalog records provenance, but provenance is not a redistribution
license. The official-app and fan-board recordings are copyrighted material;
keep deployment private/personal unless you have permission to publish them.

Build or refresh the offline source library, then compile the final modules:

```sh
python3 tools/build_bmo_sound_library.py \
  --board-html /path/to/101-bmo-board.html \
  --board-html /path/to/101-bmo-secondary-board.html \
  --beemo-ipa /path/to/Beemo-v2.2.5.ipa

PYTHONPATH=. python3 tools/build_bmo_flash_library.py
```

The first command retains normalized files under `assets/` as private build
intermediates. They are never copied to the Pages directory. The second command
recreates `catalog.json` and all final `.bin` files deterministically.

## ESP32 production flow

The dashboard embedded in `esp32-body/src/index.html` loads the remote catalog.
Selecting a sound posts its module URL, SHA-256, slots, and pacing to:

```text
POST /soundboard/play
GET  /soundboard/status
```

`remote_soundboard.c` performs the operation asynchronously:

1. fetch the `.bin` over LAN HTTP or verified HTTPS;
2. stage exactly 770,048 bytes in PSRAM;
3. require the `KT` marker and catalog SHA-256;
4. skip the write when the same module is already installed in this boot;
5. otherwise send the untouched bytes through `Upload sound`;
6. issue each mapped `PlaySound` command with its full slot-duration wait.

The last installed module remains in the robot's sound flash. There is no
automatic BMO restore and no typed confirmation. Integrity checks are internal
protocol invariants, not user-facing gates.

Like the existing drive and console controls, the write endpoints trust the
device's local network. Put the ESP32 on a trusted LAN; do not expose its HTTP
server directly to the public internet.

Browser preview fetches the same `.bin` and decodes its little-endian PCM using
catalog offsets. It never requests a separately hosted audio file.

The default catalog URL is:

```text
https://abhidya.github.io/NeatoBMO/bmo-soundboard/catalog.json
```

It can be changed in the ESP32 dashboard for a LAN host or another Pages/CDN
deployment.

## No-burn development server

Run:

```sh
python3 tools/esp32_web_simulator.py
```

Open `http://127.0.0.1:8080`. The server reads the embedded ESP32 HTML source,
injects only simulator/catalog configuration,
serves local copies of the published catalog/modules, validates requested
modules byte-for-byte, and simulates install/play status. It never opens a
serial port and never contacts the robot.

Use `--speed 1` for real slot timing or the default accelerated timing for UI
work. Remote module URLs are disabled by default; `--allow-remote-modules`
opts into them for controlled integration testing.

## Hardware boundary

No command in either build tool writes hardware. A real sound-flash write only
occurs when production ESP32 firmware receives `POST /soundboard/play` while a
Neato is connected, or when an existing explicit burn tool is run. Firmware
compilation and the Python simulator are safe no-burn verification paths.
