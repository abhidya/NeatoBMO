# Neato OS research index

Current evidence for understanding the XV-12 without further flash writes:

- [USB observability](usb-os-observability.md): command surface, binary framing,
  live modes, and readback limits.
- [Sound-burn forensics](sound-burn-forensics.md): observed sound-bank outcomes
  and the PCM-only overlay rule for any future recovered image.
- [LCD rendering investigation](lcd-rendering-investigation.md): safe visual
  probes and the evidence required to locate the rendering path.
- [Hardware readback options](hardware-readback-options.md): P6, passive NAND,
  SAM-BA, and JTAG options with non-erasing boundaries.
- [CFW session handoff 2026-08-10](NEATO_CFW_SESSION_HANDOFF_2026-08-10.md):
  the latest readback/custom-firmware session record.
- [Hardware access](neato-hardware-access.md): opening the case and reaching
  the mainboard / AT91SAM9XE (U29); P6 serial + P10 JTAG pinouts, the J3
  ERASE brick warning, and which header serves which attack.
- [Envelope crypto analysis](neato-envelope-crypto.md): the `.enc` format is
  AES-CBC with a fixed key+IV starting at off 512; brute force and every
  weak-setup shortcut ruled out; CPU = Atmel AT91SAM9XE128; no public break;
  only route is on-chip key extraction (SAM-BA probe / GPNVM glitch).
- [Vorwerk VR100 cross-flash](neato-vorwerk-vr100-crossflash.md): archived
  community path to flash VR100/Neato images on Rev113 via the offline
  updater. Rev113 firmware = 3 parts (app/LDS/sound + Config.ini); oldest
  Cruz boards (side charge jack) cap at v3.1 — 3.2+ bricks. Our 18755 image
  == Vorwerk 3.2. Breaks the BMO sound-bank identity gates; do not run on
  the working BMO body.

The immediate safe order is USB observation, LCD visual/bus capture, passive
P6 capture, then only board-confirmed read-only recovery. Do not treat a
sound-module upload as an OS memory-access primitive.
