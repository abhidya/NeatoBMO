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
- [Vorwerk VR100 cross-flash](neato-vorwerk-vr100-crossflash.md): archived
  community path to flash VR100 3.2 on our Rev113 board via `SetConfig
  ModelID` masking — no decryption needed, but it breaks the BMO sound-bank
  identity gates. Unverified; do not run on the working BMO body.

The immediate safe order is USB observation, LCD visual/bus capture, passive
P6 capture, then only board-confirmed read-only recovery. Do not treat a
sound-module upload as an OS memory-access primitive.
