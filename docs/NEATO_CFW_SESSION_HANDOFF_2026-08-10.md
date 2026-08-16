# Neato CFW session handoff — 2026-08-10

> **Superseded live-state update (2026-08-11):** P6 capture was completed, exact
> Cruz-P 2.5 build 15893 was installed from the BACK-selected factory
> application, and the application write was mapped to NAND region `0x10000`.
> Installed 2.5 passed software reboot and cold boot; BACK still boots factory
> 2.4.15667. Firmware 2.5 retained the same help surface and failed to expose
> application bytes through dump/readflash/XMODEM. The raw external-memory
> acquisition gate below therefore remains open; the statements in this dated
> handoff that no application had been written and P6 was not captured are
> preserved only as historical context. See `../captures/README.md` D01.

## Result at 2026-08-10 (historical — no application had been written yet)

No Neato application firmware has been dumped, patched, or written. The stock
USB command interface was exhaustively tested for readback without sending an
erase, unlock, reboot, or programming operation; it returned only command
echoes and terminators. Evidence is preserved under
`logs/firmware-readback-20260810/`.

The offline portion of the version-only CFW proof is implemented:

- `tools/neato_cfw.py` inspects a future raw image, creates a deterministic
  same-size version replacement pinned to the base SHA-256, emits a manifest,
  and independently verifies that only the approved bytes changed.
- `tests/test_neato_cfw.py` covers base-hash mismatches, ambiguous locations,
  encodings, size invariants, atomic publication, and tamper detection.
- `.omx/plans/neato-cfw-version-path.md` is the full gated execution plan.
- `.omx/plans/neato-25-upgrade-e2e.md` records the lower-upgrade experiment and
  why it cannot substitute for raw recovery.

The repository's clean `HEAD` ESP32-S3 firmware was built and flashed to the
board connected as `/dev/cu.usbmodem5C381965721`. The P6 UART bridge is verified
listening at `10.0.0.106:3334`. The isolated build used the existing P6 mapping:

- Neato P6.2 (robot RX) <- ESP32 GPIO17 (TX)
- Neato P6.3 (robot TX) -> ESP32 GPIO18 (RX)
- Neato P6.4 (ground) -- ESP32 ground

No P6 wires were attached and no boot trace was captured. The user deferred
that physical step.

## Private build artifacts

The successfully flashed ESP32 files are retained in the private firmware
archive, not Git, because `firmware.bin` embeds the local Wi-Fi configuration.

| File | SHA-256 |
|---|---|
| `firmware.bin` | `77ca3091083ca23106a2317ba9c89bbb6bf3dd1494ace20abfbb6242f3932b73` |
| `bootloader.bin` | `f6f8df05cd247a79dec4afb084d7621bd19163c1ca7a0b0104b18c66d6bc7f47` |
| `partitions.bin` | `257b872c0b49cb3af18bd9f76b60e086c39779fcae6e12c8542e1e1905a7b906` |

The clean build initially failed because ESP-IDF expected a generated
`x509_crt_bundle.S` that PlatformIO did not create. The isolated build disabled
`CONFIG_MBEDTLS_CERTIFICATE_BUNDLE`; no application source references the
certificate-bundle API. The resulting image used 39,640 bytes of RAM and
940,113 bytes of its application flash allowance.

## Resume point (superseded — P6 done, SAM-BA ruled out; see FIRMWARE_ARCHIVE.md next gate)

1. Turn the Neato completely off.
2. Confirm P6 orientation and 3.3 V levels; connect only P6.2, P6.3, and P6.4 as
   mapped above. Do not use J3, P6.1, or any 5 V connection.
3. Open a receive-only capture to `10.0.0.106:3334`.
4. Power-cycle the Neato and save the complete 115200 8N1 cold-boot trace.
5. Determine whether the trace exposes an AT91 ROM/SAM-BA monitor. If it does
   not, identify the flash device and use a read-capable hardware programmer
   that preserves NAND OOB/ECC data.
6. Obtain two independent byte-identical stable-region captures before using
   the offline patch tooling.

Encryption remains unresolved at the vendor `.enc` container layer. It is not
an obstacle if a restorable raw hardware image can be acquired, but transport
acceptance alone does not prove that modified container bytes are executable.
