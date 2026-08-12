# Upload destination race: hypothesis, gates, and safe first probe

## Current answer

The new captures expose a promising **call boundary**, not yet a working exploit.
A normal SOUND transfer is fully received into RAM and only then reaches:

```text
nandflashWrite() - region=0x400000 offset=0 bytes=770052 pData=0x208790EE
```

The application transfer reaches the same routine with logical region
`0x10000`. This establishes a shared NAND-writing backend with module-specific
arguments. It does **not** establish that `region` is a mutable global, that a
second command can run while BLAST is receiving, or that execution jumps to
`pData`. `pData` is best treated as the receive-buffer pointer unless later
code analysis proves otherwise.

Therefore the proposed path is split into separate claims:

1. P6 RX accepts commands, rather than only emitting logs.
2. The P6 command parser remains runnable while USB is in BLAST receive mode.
3. Two command paths share mutable upload type/options/destination state.
4. A changed destination survives until the final `nandflashWrite()` call.
5. Bytes written to the application region pass the later boot/decrypt/integrity
   path and execute.
6. Executing code can observe decrypted material or the key-handling path.

Only the known region values and shared writer are proven today. Claims 1–6
must not be collapsed into one conclusion.

## Why the USB channel cannot carry the race command

After USB receives ENQ, every byte belongs to the declared binary frame until
`Size` bytes have arrived. Inserting a textual command into that stream changes
the payload and checksum; it does not create an independent command. The
separate P6 RX line is the only currently wired candidate for simultaneous
control.

The ESP32 bridge already exposes that line as TCP port 3334. Until now, the
validated field wiring left P6.2/robot RX disconnected or used it only for an
ESP32 loopback self-test. There is no checked capture showing that the Neato
accepts a command on P6 RX.

## Implemented phase: read-only concurrency probe

`tools/neato_upload_concurrency_probe.py` implements the first two gates and
nothing beyond them.

It first sends a fixed `GetVersion` over P6 and requires the response to identify
this exact XV-12 and either its installed 2.5 application or BACK-selected
factory 2.4 application. If that does not happen, the tool exits before starting
an upload.

After the P6 baseline passes, it independently pins the Neato USB endpoint,
checks live `Help Upload`, starts the exact recovery-tested vendor bank with
`Upload sound noburn`, and sends one more fixed P6 `GetVersion` halfway through
the byte-exact frame. It always completes the declared frame after ENQ. The
result records whether:

- P6 returned `GetVersion` after the injection point;
- the updater reported `Type: SOUND` and `Options: NoWrite`;
- all 770052 framed bytes were received;
- the NAND write was blocked rather than reported `OK`;
- either `0x400000` or the unexpected `0x10000` destination appeared.

The tool has no caller-controlled P6 command and no destructive USB command.
It refuses every sound image except the exact vendor rollback artifact and
requires explicit USB/P6 endpoints plus an exact typed confirmation.

Offline validation, which never contacts the robot:

```sh
python3 tools/neato_upload_concurrency_probe.py \
  /path/to/DfltSoundLib.Rev1.0.bin
```

Hardware probe, only with P6.2 connected to ESP32 GPIO17 and the existing P6
bridge reachable on TCP 3334:

```sh
python3 tools/neato_upload_concurrency_probe.py \
  /path/to/DfltSoundLib.Rev1.0.bin \
  --usb-port /dev/cu.usbmodem1431201 \
  --p6-host 192.168.4.1 \
  --execute-noburn-probe \
  --confirmation 'RUN NO-WRITE P6 CONCURRENCY PROBE ON WTD41611DD-0037829-P' \
  --result captures/<new-id>_concurrency_result.json \
  --p6-output captures/<new-id>_concurrency_p6.log
```

Use the actual ESP32 address when it is connected to another network. Do not run
`tools/p6_capture.py` against TCP 3334 simultaneously; the bridge supports one
TCP client, and the probe itself preserves the P6 stream.

## Result interpretation

| Idle P6 `GetVersion` | During-upload `GetVersion` | Meaning |
|---|---|---|
| No | Not attempted | P6 is not a proven command input; stop. |
| Yes | No | P6 is an input console, but parser execution appears blocked during BLAST. |
| Yes | Yes | Concurrent parser execution is proven; shared mutable upload state remains unproven. |
| Yes | Yes, and destination changes | A destination-state interaction is plausible, but any real write must remain donor-only until independently reproduced under `NoWrite`. |

An `ACK`, `nandFlashWrite() OK`, or loss of `Options: NoWrite` is an unexpected
unsafe result and must be treated as a stop condition, even if post-probe
`GetVersion` still works.

## What comes next after a positive concurrency result

Do **not** jump directly to a plaintext custom application on the working robot.
The next research task is static or donor-board proof of where upload type,
options, and region live. A dual-upload state canary is riskier than this probe:
even when both commands request `noburn`, a state-corruption bug could drop the
NoWrite flag. That experiment belongs on a donor or a setup with duplicate raw
NAND capture and demonstrated restoration.

Even a proven SOUND-to-CODE destination race would only show an arbitrary write
to the installed-application region. The current boot path still expects the
Neato application envelope and has unresolved decryption/integrity behavior.
Writing plaintext there may simply force the independent factory fallback; it
does not reveal the AES key. Key recovery requires a later primitive that can
observe the decrypted application or instrument the code that handles it.

## Hard boundaries

- Never use firmware 3.2+ on this Cruz Rev113 board.
- Never touch J3/ERASE.
- Never reinterpret USB terminator `0x1A` as successful validation or a write.
- Never infer execution from the logged `pData` address alone.
- Keep the factory 2.4 application and the exact Cruz-P 2.5 rollback artifacts
  outside every experiment target.
