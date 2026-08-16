# NAND readback & key-extraction runbook

Status: **plan — read-only acquisition and donor-board research only.**
Target: XV-12 `WTD41611DD-0037829-P`, Cruz Rev113/P, mainboard 7.1,
AT91SAM9XE128-QU.

## TL;DR

Custom firmware is blocked by two layers over the application region
(`0x10000`): (1) an application integrity/checksum field in the clear header
(proven by E13, `docs/neatoos-execution-probe.md`), and (2) AES-CBC encryption
with a fixed on-chip key (proven by the divergence analysis in
`docs/neato-envelope-crypto.md`). Neither is defeatable from the `.enc` files
alone. This runbook sequences the two remaining acquisition routes and — just
as importantly — states exactly what each one yields, so effort is not spent
expecting plaintext where plaintext is not stored.

## The one fact that re-scopes everything

The updater writes the **encrypted** `.enc` bytes verbatim to `0x10000`
(`nandflashWrite() … bytes=805892` = 805,888-byte `.enc` + 4-byte checksum;
staged in RAM at `pData=0x208790EE`, `captures/20260812_E07_stock_25_restore_p6.log`).
The MCU decrypts internally at boot (`docs/neato-envelope-crypto.md`).

Consequence: **a raw NAND read of region `0x10000` is almost certainly
ciphertext, not the running plaintext.** The plaintext application exists only
in RAM after the bootloader decrypts it, which is exactly what we cannot read
(no stable JTAG TAP; P6 is a boot log). This is the load-bearing distinction:

| Want | Where it actually lives | Route that reaches it |
|---|---|---|
| Decrypted application plaintext | RAM, post-boot | Key extraction (decrypt the `.enc`) **or** a transient JTAG/RAM readback |
| Bootloader / decrypt + checksum logic | Lower NAND, **plaintext** | External NAND readback |
| Byte-restorable full backup + geometry | External NAND (raw + OOB/ECC) | External NAND readback |
| Known-plaintext anchor for the map | Sound region `0x400000` (we hold the exact bytes) | External NAND readback |

So NAND readback is **not** "read the plaintext app" — it is "read the
plaintext **bootloader**, where the key derivation and the `0x10..0x1f`
checksum algorithm almost certainly live." That is still the highest-value,
lowest-risk unlock step, because reversing the plaintext bootstrap is the most
plausible way to learn the key/checksum without fault injection.

## Decision map

```text
Is the app region stored as ciphertext or plaintext?
   (determined only by the Phase-4 classification below, not assumed)
├── plaintext  → NAND readback IS the unlock; skip to patching.
└── ciphertext → reverse the plaintext bootloader for:
                 ├── AES key derivation  → decrypt .enc → plaintext app
                 ├── 0x10..0x1f checksum algorithm → re-checksum patched images
                 └── load/entry mapping  → where the bootloader jumps
              if bootloader yields no key → donor fault-injection (Route 2)
```

## Route 1 — External NAND acquisition (preferred, read-only)

### Phase 1 — Identify the part and geometry

1. Photograph the mainboard; record silkscreen and the flash package markings
   (`docs/neato-hardware-access.md` gives the board/CPU orientation). Do not
   trust online Cruz teardowns for the NAND part — this board is Rev113, and
   most public teardowns are Rev64/Binky.
2. From the part number, obtain the datasheet and record:
   - page size and spare/OOB size;
   - ECC scheme (1-bit Hamming / 4-bit BCH / on-die) and whether ECC lives in
     OOB;
   - bad-block marker location;
   - block/plane geometry and the logical→physical (LBA) mapping.
3. Map the test points / bus for a passive or in-circuit read. Prefer a socketed
   or donor read; do **not** lift the flash from the working BMO board first.

### Phase 2 — Duplicate raw reads with OOB/ECC

1. Use a programmer/reader that preserves **raw pages + OOB + bad-block markers +
   ECC convention** (e.g. Xgecu/TL866-class with a NAND adapter, or a raw bus
   capture with a logic analyzer if the part is TSOP48 and in-circuit).
2. Take **two independent full reads** and diff. Only byte-identical stable
   regions count; re-read anything that differs.
3. Save both captures plus a SHA-256 manifest under
   `/Volumes/2TB/neato-firmware-archive/work/inputs/` (do not commit firmware
   bytes to the repo — the repo already treats images as external).

### Phase 3 — Anchor logical↔physical using the sound bank

The sound region `0x400000` is the one region whose plaintext we hold
byte-for-byte (vendor default `d3969779…b64a`, 770,048 bytes, `KT` magic).
Search the raw dump for that image (and for the `KT` directory) to fix the
logical-address→physical-offset mapping. This converts the observed logical
regions (`0x10000` app, `0x400000` sound) into physical page ranges and
validates the OOB/ECC interpretation.

### Phase 4 — Classify regions

Label every region as plaintext / ciphertext / config / calibration / erase:

- **Lower region (bootstrap)** — expected plaintext; this is the target for
  reversing the decrypt + checksum + load logic. Extract it and start ARM9
  analysis (vector table, strings, `0x10..0x1f` handling, AES key schedule or
  KDF constants).
- **`0x10000` app region** — decide ciphertext vs plaintext from entropy/strings
  (`tools/neato_firmware.py validate-unlock` requires size coverage, command
  markers, substantial strings, low entropy, and optionally an exact repack
  hash).
- **`0x400000` sound region** — must match the known bank; this is the map
  anchor and the acquisition-validity check.

### Phase 5 — Reverse the plaintext bootstrap (the actual unlock)

If the app region is ciphertext, the bootstrap is where the key lives. Look for:

- the AES key schedule / a KDF over the `neato` magic, model string, or serial;
- the `0x10..0x1f` field computation (checksum vs MAC) and what range it covers;
- the decrypt routine and where it writes (RAM load address → fixes the
  `0x20000000` hypothesis in `neatoos/linker/linker.ld`);
- whether a decrypted copy is ever written back to a fixed NAND scratch region
  (which would make a later readback yield plaintext).

Produce the key/checksum/load-map as validated artifacts only when they
reproduce the known `.enc` images (re-encrypt a `.enc` body and match the stored
SHA; recompute the checksum field and match stock).

### Phase 6 — Patch gate (only after plaintext is proven)

Do **not** patch until a decrypted, validated application exists. Then follow
`docs/neatoos-execution-probe.md` "Next experiment" and `FIRMWARE_SOUND_PATCH.md`
"Distilled CFW execution path": version-only patch first, health regression,
then the `PlaySound File` handler. Keep the exact stock 2.5 restore image and
the BACK-selected factory path as the recovery invariant.

## Route 2 — Key extraction (fault injection / GPNVM glitch; donor only)

Use only if Phase 5 does not recover the key from the plaintext bootstrap, and
only on a donor board.

- The AT91SAM9XE security bit blocks JTAG; **ordinary clearing is the ERASE
  operation, which also destroys internal flash** (`docs/neato-hardware-access.md`).
- The target is a **non-erasing transient bypass**: glitch the security-bit read
  long enough to attach JTAG and read RAM/internal flash without invoking ERASE.
  The RECESSIM ATSAM4C32 GPNVM-glitch result is the closest published precedent,
  but it does **not** transfer to the AT91SAM9XE.
- Requires: a controllable power/glitch source, the JTAG chain on P10 (currently
  no stable TAP/IDCODE/IR length — `docs/neato-p10-jtag-result.md`), and a
  clocking/trigger plan. Treat every step as destructive to the donor.

## Safety boundaries (non-negotiable)

- **J3/ERASE is a permanent brick. Never short it, never erase.**
- All fault injection and any in-circuit write: **donor board only**.
- The live board is only ever read passively (P6) or driven by the existing
  guarded, typed-confirmation tools.
- Never commit firmware bytes to the repo; keep them on the 2 TB archive volume.
- Do not widen `tools/neato_code_noburn.py` / `neato_sound_burn_exact.py`
  allowlists to accept unproven images.

## Decision gates

- Proceed to Route 2 only after Phase 4 classifies `0x10000` as ciphertext and
  Phase 5 fails to yield the key from the bootstrap.
- Proceed to patching only after a validated plaintext application exists and
  the checksum algorithm is understood (not assumed — `docs/neatoos-execution-probe.md`
  warns that common unkeyed checksums already failed).
- Prefer RAM/debug boot for the first execution proof; use a spare/duplicate
  flash device before any persistent application write.

## Tools & evidence links

- `tools/neato_firmware.py validate-unlock` — plaintext-candidate structural gate.
- `tools/neato_cfw.py` — offline, deterministic version patch (file-only).
- `tools/backup_neato.py` — read-only snapshot (not a firmware backup).
- `tools/p6_capture.py` — append-only passive P6 capture.
- `docs/hardware-readback-options.md` — the readback sequence this runbook makes concrete.
- `docs/neato-hardware-access.md` — board, CPU, P6/P10/J3, ERASE warning.
- `docs/neato-envelope-crypto.md` — envelope layout and the fixed-key/fixed-IV proof.
- `docs/neatoos-execution-probe.md` — E04–E14 gate findings and recovery evidence.
- `captures/20260812_E13_header_field_bitflip_burn_p6.log` — the checksum-field proof.
