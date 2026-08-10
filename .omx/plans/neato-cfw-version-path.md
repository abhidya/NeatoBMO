# Neato XV-12 version-only CFW path

## Outcome

Produce a reversible, evidence-backed custom application image for XV-12
`WTD41611DD-0037829-P` that changes only the version reported by `GetVersion`.
Use that image to prove the complete CFW acquisition, patch, execution,
verification, and recovery pipeline before implementing `PlaySound File`.

This is a plan, not authorization to write the robot.

## Requirements summary

- Target the exact P-family/mainboard 7.1 device currently running
  `2.4.15667` ([FIRMWARE_ARCHIVE.md](../../FIRMWARE_ARCHIVE.md#L28)).
- Do not infer application validity from `Upload code noburn`: the receiver
  accepts a transport payload but provides no readback or execution proof
  ([FIRMWARE_ARCHIVE.md](../../FIRMWARE_ARCHIVE.md#L39)).
- Keep acquisition non-destructive until a full restorable image and recovery
  mechanism are proven.
- Treat public P-family 2.5/2.7/3.1/3.2 images as references, not as the first
  CFW base ([FIRMWARE_ARCHIVE.md](../../FIRMWARE_ARCHIVE.md#L94)).
- Preserve image size and layout in the first patch. Change only the smallest
  fixed-width version representation and any proven integrity fields.
- Do not add `PlaySound File` until the version-only image passes all gates.

## Core design decision

Use a **raw hardware acquisition and reversible execution path** as the primary
route. The updater's encrypted container is a delivery format, not necessarily
the representation stored or executed by the CPU. Bypassing that container
means a decryptor/repacker is not required if raw application read, RAM load,
and eventual raw restore/write are available.

The user's transport observation is retained as evidence: the updater forwards
the input bytes and the vacuum's receive path does not authenticate them. It
does not establish whether a later stage transforms the bytes before execution.
Consequently, blind mutation of `.enc` payload bytes is excluded from the
primary path.

## Quick risky path

This is the shortest credible route when accepting a substantially higher
brick/recovery risk:

1. Connect a hardware reader/programmer to the confirmed application flash and
   make one complete raw dump, retaining NAND OOB/ECC bytes if applicable.
2. Find the `GetVersion` software build constant or fixed-width string in that
   dump and confirm exactly one code/data reference controls the response.
3. Change only that same-size value—for example `15667` to a reserved five-digit
   CFW sentinel—and update only an integrity field that is positively
   identified. Do not resize or relocate anything.
4. Write back only the application region, read it back immediately, and verify
   that it exactly matches the patched file.
5. Reboot and run `GetVersion`. If it does not boot or the sentinel is absent,
   restore the original raw dump using the same hardware programmer.

This route intentionally skips duplicate captures, unchanged-image execution,
RAM boot, a spare-flash restore rehearsal, and `.enc` transform analysis. Its
one non-negotiable gate is that the programmer must be able to read and write
the flash while the Neato application is not running; otherwise a failed boot
would also remove the only recovery path.

Success criteria:

- original raw dump has a recorded size and SHA-256;
- patch changes only the version bytes and any proven integrity bytes;
- immediate post-write readback equals the patched image byte-for-byte;
- `GetVersion` reports the chosen CFW sentinel after cold boot;
- the original dump remains available for hardware restoration.

Principal failure modes are incorrect NAND geometry/OOB handling, an unknown
boot checksum, patching the wrong version representation, or electrical damage
from in-circuit programming. Any of these can leave the robot unable to boot;
the external programmer is the recovery mechanism.

## Deliverables

All generated binary evidence should live outside Git under:

```text
/Volumes/2TB/neato-firmware-archive/work/cfw/
├── board-profile/
├── captures/
├── extraction/
├── patches/version-proof-1/
├── run-logs/
└── recovery/
```

Git should contain only tooling, tests, manifests without private/raw firmware,
and the operating runbook.

## Execution plan

### 1. Freeze the baseline and target contract

1. Create a fresh read-only snapshot with `tools/backup_neato.py` and record:
   device identity, `GetVersion`, mainboard, calibration/configuration hashes,
   command help, date, and USB identity.
2. Re-catalog the public archive with `tools/neato_firmware.py catalog` and pin
   the exact hashes for every P image.
3. Define the first visible CFW sentinel. Tentative value: preserve the
   `Software,2,4,<build>` grammar and change only the five-digit build field to
   a reserved value such as `95667`. Finalize only after locating how the value
   is represented in code/data.

Acceptance:

- Fresh baseline snapshot has its own SHA-256 manifest.
- Target identity is exactly `WTD41611DD-0037829-P`, mainboard `7.1`, software
  `2.4.15667`.
- CFW sentinel preserves the existing CSV response grammar and field widths.

### 2. Prove board topology before attaching active tools

1. Photograph both PCB sides and record CPU, external flash, SDRAM, boot strap,
   P6, P10, J3, and unlabelled test-pad markings.
2. Confirm voltage levels with a meter before connecting a UART/debug adapter.
3. Capture P6 DBGU passively at 115200 8N1 across cold boot and reset.
4. Determine the least invasive raw-read candidate in this order:
   - debugger halt/read without changing nonvolatile state;
   - naturally reachable ROM monitor over DBGU/USB;
   - external flash programmer with a verified pinout and voltage isolation;
   - chip removal only as a final recovery-lab path.
5. Do not use J3. Do not force ROM-monitor entry if doing so requires erasing or
   invalidating boot media.

Microchip's SAM9XE documentation confirms that the ROM SAM-BA assistant can
communicate through DBGU or USB. Monitor availability on this assembled Neato
board must still be demonstrated; the generic capability is not evidence that
the board exposes a safe entry sequence.

Acceptance:

- Board profile identifies exact part numbers and signal voltages.
- Passive boot log is stored with timestamp, wiring diagram, and adapter model.
- Selected acquisition method has an explicit statement of every write-capable
  operation and how those operations are disabled or avoided.

### 3. Acquire a byte-restorable raw image

1. Read the complete nonvolatile device twice after independent power cycles.
2. For NAND, preserve raw page data, OOB/spare bytes, bad-block markers, and ECC
   convention. Do not reduce the capture to logical data until raw copies exist.
3. Compare captures page-by-page. Separate stable program regions from expected
   mutable configuration/log regions.
4. Store capture manifests with device identity, geometry, tool version,
   adapter, command transcript, file sizes, and SHA-256 hashes.

Acceptance:

- Two independent captures match exactly across every stable region.
- Any differing region is explained and excluded from application extraction.
- A recovery procedure can address the physical device even if the application
  no longer boots.

Stop condition: no patch work begins if flash geometry, OOB/ECC handling, or
stable application bytes remain ambiguous.

### 4. Extract and classify the executable application

1. Identify boot stages, partition/region boundaries, load addresses, entry
   points, vectors, and CPU mode.
2. Search for the known strings already used by
   `tools/neato_firmware.py`: `Neato Robotics`, `PlaySound`, `GetVersion`,
   `SetMotor`, `Copyright`, and `NEROS`
   ([tools/neato_firmware.py](../../tools/neato_firmware.py#L21)).
3. Extend the validator with a raw-image mode that records region offsets,
   hashes, entropy, vector plausibility, and strings without assuming the
   `.enc` envelope.
4. Cross-check code/data references in a disassembler and document the memory
   map. Do not rely on string proximity alone.

Acceptance:

- Extracted application has a stable SHA-256 and deterministic extraction map.
- At least two command strings have valid code/data cross-references.
- Reset/vector and entry-point analysis are coherent for the confirmed ARM9
  target.
- The source of the `GetVersion` software tuple is identified precisely.

### 5. Prove unchanged execution before patching

1. Run an unchanged extracted application through the intended reversible
   execution mechanism, preferably a RAM/debug launch after the board's normal
   initialization state is understood.
2. Capture serial boot output, `GetVersion`, command help, motor-disabled health
   probes, LCD, sensors, charger, and reboot behavior.
3. Compare against the baseline snapshot.

Acceptance:

- Unchanged extracted image reaches the console and reports `2.4.15667`.
- Baseline health probes match without persistent storage changes.
- Power-cycle returns the robot to its original application.

Stop condition: if the unchanged image cannot execute reproducibly, the patch
builder is not started.

### 6. Build a deterministic version-only patcher

Status: the generic offline slice is implemented in `tools/neato_cfw.py` with
tests in `tests/test_neato_cfw.py`. It inspects raw files, builds same-size
ASCII/u16le/u32le replacements, atomically publishes the output and manifest,
and independently verifies that only the approved replacement changed. The
raw-image-specific extraction map and any real boot-integrity update remain
blocked on acquiring the hardware dump.

Add `tools/neato_cfw.py` with narrowly scoped commands:

- `inspect-raw`: apply the extraction/structure checks and emit JSON.
- `patch-version`: require an approved base hash and an expected old byte
  sequence/value; produce a same-size output.
- `verify-patch`: reject unexpected byte differences, layout movement, invalid
  branch targets, or missing integrity updates.
- `manifest`: emit base/output hashes, offsets, old/new bytes, tool revision,
  integrity fields, and the expected `GetVersion` response.

The patcher must fail closed if the base hash, old bytes, count of matches, or
output size differs from the approved manifest. No generic arbitrary-offset
write command belongs in the deployment tool.

Tests in `tests/test_neato_cfw.py` should cover:

- wrong base hash;
- zero or multiple version matches;
- wrong expected old bytes;
- size/layout change;
- unexpected changed ranges;
- deterministic output and manifest;
- integrity-field recalculation when a real field is identified.

Acceptance:

- Repeated builds are byte-identical.
- Diff contains only the approved version field and proven integrity bytes.
- `verify-patch` independently validates the result from disk.

### 7. Execute the version proof reversibly

1. Run the CFW image through the already-proven reversible execution path.
2. Require the exact sentinel `GetVersion` response.
3. Run the same health suite used for unchanged execution.
4. Power-cycle back to stock and confirm `2.4.15667` plus matching health.

Acceptance:

- Patched run reports only the intended version change.
- Command help, LCD, sensors, charger, USB reconnect, and controlled reboot pass.
- Stock recovery after power-cycle is demonstrated and logged.

### 8. Establish persistent-write recovery

Persistent application writing is a separate destructive phase and requires
explicit approval at execution time.

Before approval:

1. Clone the captured image to a spare compatible flash device or equivalent
   recovery fixture and prove it boots/restores correctly.
2. Document exact write boundaries and protect calibration/configuration areas.
3. Verify stable power, physical recovery access, original capture hashes, and
   a second operator-readable rollback checklist.
4. Prefer raw region programming through the proven hardware path. Use the
   updater `.enc` route only after its transform and post-write representation
   are independently understood.

Acceptance:

- Recovery does not depend on the Neato application booting.
- Original image can be restored and verified byte-for-byte.
- Write tooling refuses any device identity, base hash, or region map mismatch.

### 9. Deploy version-proof CFW, then begin `PlaySound File`

After explicit destructive-write approval, write the smallest proven
application region, read it back, verify its hash, reboot, and run the complete
version/health suite. Retain the raw original, patched image, manifests,
transcripts, and recovery log.

Only after this milestone passes should the functional patch described in
`FIRMWARE_SOUND_PATCH.md` begin. That patch must use the version-proof CFW image
as its base and add one behavior at a time: command dispatch, bounded receiver,
WAV validation, existing PCM/DAC call, then buffer lifecycle.

## Alternative paths

### A. Known developer/service image and packer

Continue a bounded search for a plaintext developer image, factory tool, or
service package. Accept it only if the resulting plaintext passes structural
checks and an unchanged repack exactly reproduces a known vendor `.enc` hash.
This can supersede the raw path if independently proven.

### B. Recover the container transform

Analyze bootloader code or a hardware capture to locate the `.enc` transform
and authenticator semantics. Require decrypt -> encrypt identity against at
least two known P-family images before using it. This is useful for normal USB
delivery but unnecessary if the raw hardware route is reliable.

### C. Blind plaintext/custom payload through `Upload code`

Rejected for the only robot. Receiver acceptance is not bootability evidence.
This experiment is allowed only on a sacrificial/spare board with independent
hardware recovery already demonstrated.

## Risks and mitigations

- **Brick from incomplete recovery:** no persistent write until off-application
  recovery and byte-exact restoration are proven.
- **NAND capture loses ECC/OOB semantics:** retain raw pages and OOB; document
  programmer geometry before logical extraction.
- **Wrong application base:** use installed 2.4 capture for the first proof;
  keep public 3.x images as references.
- **Version field has multiple representations:** require code/data references
  and a unique expected patch site; fail closed on multiple matches.
- **RAM launch hides cold-boot dependencies:** treat RAM execution as the first
  gate, not proof of persistent boot; repeat full cold-boot tests after recovery
  is established.
- **Transport non-validation is mistaken for no transform:** keep transport,
  stored representation, and executed representation as separate evidence.

## Verification matrix

| Gate | Evidence | Pass condition |
|---|---|---|
| Baseline | snapshot + hashes | exact device/version identity |
| Acquisition | two raw captures | stable regions match byte-for-byte |
| Extraction | map + validator JSON | coherent ARM9 image and xrefs |
| Unchanged run | transcript + health JSON | stock behavior reproduced |
| Patch build | manifest + independent diff | only approved ranges differ |
| CFW RAM/debug run | transcript + health JSON | sentinel version, no regressions |
| Recovery | spare/fixture restore log | original boots and hashes match |
| Persistent deploy | readback + health JSON | patched hash and sentinel survive cold boot |

## Immediate next action

Produce the board-profile evidence package: board photographs, exact CPU/flash
part numbers, P6/P10/J3 mapping, voltage measurements, and a passive cold-boot
P6 trace. That result selects the concrete acquisition adapter and read command
set; no firmware write is needed for this step.
