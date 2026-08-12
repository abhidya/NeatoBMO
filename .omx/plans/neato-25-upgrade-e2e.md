# Neato 2.5 stock-upgrade and CFW E2E experiment

## Execution result — 2026-08-11

Phases 1–3 were completed on `WTD41611DD-0037829-P`. The guarded exact-image
tool installed Cruz-P 2.5 build 15893 from the BACK-selected factory
application. P6 recorded a successful 805892-byte transfer and NAND write to
logical region `0x10000`; software reboot and a separate cold boot both reached
NEROS 15893. BACK still boots the independent factory 2.4 build 15667.

The upgrade did **not** unlock CFW acquisition: probed help remained unchanged,
`PowerCycleCDC` was absent, raw `dump`/`readflash` returned only command framing,
and XMODEM never began. Phase 4 repeat-write is unnecessary flash wear. The
next gate is duplicate external-NAND acquisition with OOB/ECC preservation,
preferably on a donor board. Evidence is indexed under
`captures/20260811_D01_*` and in `captures/README.md`.

## Current answer

The CFW tooling is not end-to-end. It currently implements only:

```text
raw executable file
  -> inspect markers/version representations
  -> same-size version replacement
  -> deterministic manifest
  -> independent exact-diff verification
```

It does not implement:

```text
robot application acquisition
  -> .enc decrypt/repack
  -> application upload/write
  -> reboot/re-enumeration
  -> runtime GetVersion verification
  -> rollback after a failed application boot
```

The lowest public P-family application above installed `2.4.15667` is:

- file: `/Volumes/2TB/neato-firmware-archive/sources/Neato-XV-Series-Cruz-Rev-113-Update/OriginalVorwerkFirmwareFiles/Firmware25/XV11App.15893.P.bin.enc`
- size: `805888` bytes
- SHA-256: `e1a31ef56e2b617a4056d70c308aab26b7e2cd95e679cb982d697ff4f089c697`
- envelope: format 2, marker `neato`, declared plaintext `805156` bytes
- archive metadata discrepancy: filename/build token is `15893`, while
  `Config.ini` declares `Version=2,5,15894`

There is no archived application image for the currently installed
`2.4.15667`. Installing 2.5 therefore has no proven downgrade path.

## Experiment objective

Use the exact stock 2.5 P image to establish the real application update path,
then determine whether firmware 2.5 exposes an acquisition or bootloader route
that makes a controlled custom patch possible.

Do not describe the experiment as successful CFW E2E until a visible version
change survives a cold boot and the original application can be restored.

## Phase 1: build the exact-image stock upgrader

Add `tools/neato_code_upgrade.py` with these commands:

- `preflight`: locate the robot, require model/P/mainboard 7.1 and exact current
  version, report power/charger state, verify the candidate size/SHA/envelope,
  and write a JSON preflight record.
- `noburn`: send only `Upload code noburn Size <file+4>`, record ENQ/terminal
  behavior, then require a healthy unchanged `GetVersion`.
- `install-stock-25`: accept only the exact 2.5 P hash above, require a typed
  confirmation phrase plus `--execute-destructive-write`, send
  `Upload code reboot Size <file+4>`, wait for USB re-enumeration, and capture
  the first successful `GetVersion`.
- `verify-post-upgrade`: capture identity, command help, calibration/config,
  sensors, charger, LCD, sound-slot map, USB reconnect, and cold-boot behavior.

The tool must not accept arbitrary application files. The initial allowlist has
one entry only: exact stock 2.5 P.

Acceptance before any write:

- unit tests cover wrong robot, wrong base version, wrong image hash, wrong
  hardware suffix, missing confirmation, timeout, NAK, failure to re-enumerate,
  and unexpected post-upgrade version;
- a 2.5 `noburn` run completes and leaves `GetVersion` at `2.4.15667`;
- preflight records the `15893` versus `15894` expectation explicitly;
- no code path can upload application bytes without the destructive flag and
  exact confirmation phrase.

## Phase 2: optional stock 2.5 destructive install

This phase requires explicit approval immediately before execution because the
current 2.4 application cannot be restored from existing artifacts.

Procedure:

1. Create one final 2.4 state snapshot.
2. Keep the robot on stable external power and prevent sleep/USB interruption.
3. Send only the exact whitelisted 2.5 P application image. Do not change sound
   or LDS modules during this experiment.
4. Wait for re-enumeration and accept the upgrade only if `GetVersion` reports
   the expected 2.5 family/build.
5. Run the full post-upgrade verification and cold boot once.

What this proves:

- the direct `Upload code reboot` write path works on this 2.4 application;
- the public P image boots on this mainboard;
- the actual post-install version resolves the `15893/15894` discrepancy;
- our host can recover the USB connection and verify application health.

What this does not prove:

- ability to edit the executable;
- ability to repack `.enc`;
- acceptance of modified payloads as executable code;
- rollback to 2.4.

## Phase 3: probe 2.5 for a CFW-enabling path

Run these non-writing checks immediately after a healthy 2.5 install:

1. Capture `Help SetSystemMode` and check whether `PowerCycleCDC` now exists.
2. Capture `Help Upload` and retest application `readflash`, XMODEM readflash,
   upload-save-area `dump`, and a stock-image `noburn` transaction.
3. Classify any returned bytes:
   - exact public `.enc` container;
   - plaintext executable with `GetVersion`/command markers;
   - raw flash pages/OOB;
   - empty/transport-only response.
4. Feed any nonempty candidate to `tools/neato_cfw.py inspect-raw` and
   `tools/neato_firmware.py validate-unlock` as appropriate.

Go condition for custom patching:

- a reproducible editable executable is recovered; or
- a decrypt/repack pipeline reproduces the stock 2.5 `.enc` byte-for-byte; or
- a hardware raw-write/restore path is available.

Stop condition:

- if 2.5 still produces no application bytes and no recoverable bootloader
  access, the stock upgrade has not unlocked CFW. Do not mutate ciphertext.

## Phase 4: repeat exact stock 2.5 update

Re-uploading the byte-identical 2.5 image is optional and destructive. It can
prove same-version/repeated-update acceptance, but it adds flash wear and does
not prove custom packaging.

Only run it if Phase 3 shows that same-version acceptance is relevant to the
selected custom delivery path. Require the same exact-image hash, confirmation,
re-enumeration, `GetVersion`, and health gates as Phase 2.

## Phase 5: smallest meaningful custom patch

The first meaningful patch is a runtime-visible version change, not an envelope
or padding mutation.

1. Locate the exact `GetVersion` build constant/string in recovered plaintext.
2. Change it without changing application size or layout.
3. Update only integrity fields whose algorithms have been proven.
4. Build and independently verify with `tools/neato_cfw.py`.
5. If USB delivery requires `.enc`, require decrypt/repack identity against the
   untouched stock 2.5 image before packaging the modified executable.
6. Install through the proven Phase 2 path, cold boot, and require the chosen
   CFW sentinel in `GetVersion`.

Passing CFW E2E means:

- stock 2.5 base hash is pinned;
- patch manifest proves only intended bytes/integrity fields changed;
- upload and reboot complete;
- the sentinel version is visible after cold boot;
- health regression passes;
- a non-application recovery route can restore a boot failure.

## Recommended next action

Do not repeat the stock write. Acquire the external NAND twice, preserving raw
pages, OOB/spare data, bad-block markers, and ECC convention. Prefer a donor
Cruz board for active probing, then classify the logical `0x10000` application
region from the matching captures.
