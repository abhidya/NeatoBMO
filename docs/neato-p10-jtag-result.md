# Cruz Rev113 P10 JTAG result

Session: `captures/jtag/jtag-p10-20260813T061756Z/`

## Result

The ESP32-S3 running CherryDAP worked as a Mac-visible CMSIS-DAP JTAG adapter,
but Cruz P10 did not produce a valid JTAG TAP in this session.

Observed classification:

- adapter: working (`CherryUSB CMSIS-DAP`, OpenOCD 0.12.0);
- P10 electrical state: not dead, because TDO state changed between target-off,
  powered, pull-up, boot-triggered, and software-window observations;
- TAP/IDCODE/IR length: not detected;
- ARM926 target/halt/registers/SRAM/internal flash: not reached;
- reset-assisted attach: not tested.

The strongest supported statement is **“P10 did not expose a usable read-only
ARM926 debug interface under the tested conditions.”**  Do not simplify this to
“security bit proven set”; all-one/all-zero OpenOCD scans also fit wiring,
signal integrity, reset/TRST gating, or runtime ICE-disable explanations.

## Pin map used

Manny reported the square pad at bottom-left:

```text
top:       2 GND   4 GND   6 SRST   8 TDO   10 RTCK
bottom:    1 VDDIO 3 TRST  5 TDI    7 TMS    9 TCK
```

ESP32-S3 wiring used:

```text
ESP GND    -> P10.2 GND
ESP GPIO7  -> P10.8 TDO
ESP GPIO15 -> P10.5 TDI
ESP GPIO16 -> P10.7 TMS
ESP GPIO17 -> P10.9 TCK
```

Not connected: P10 VDDIO, TRST, SRST, RTCK.  VDDIO was not measured in this
session and series resistors were not installed; both are recorded as operator
waivers, not as validated best practice.

## Collections preserved

- `SHA256SUMS` contains hashes for every tracked file in the session folder.
- `openocd-*` logs preserve all raw OpenOCD output.
- `p6-trigger-*.raw` files preserve raw P6 boot-trigger bytes.
- `sound-vendor-write-result.json` records the later exact vendor sound-bank
  restore/health check; proprietary sound bytes remain outside Git.
- `firmware-27-write-result.json` records the later exact stock Cruz-P 2.7
  application transition from installed 2.5.15893 to 2.7.16621; proprietary
  application bytes remain outside Git.
- `firmware-31-write-result.json` records the initial automatic USB
  rediscovery timeout after an ACKed exact stock Cruz-P 3.1 write. Physical USB
  reconnect then exposed healthy 3.1.17844; `usb-snapshot-31/` preserves the
  complete read-only identity and command surface.
- `firmware-27b-write-result.json` and `firmware-25b-write-result.json` preserve
  the final comparison transition and exact stock 2.5 restore.
- `openocd-sound-vendor-*` and `openocd-firmware-27-*` preserve scan-chain
  observations during those later software windows. `openocd-firmware-31-*`
  preserves the 3.1 window. They remained all-ones/no TAP and do not change the
  no-TAP classification.
- `manifest.json` records adapter, target, software metadata, private ESP
  backup hash, counts, and explicit non-actions.
- `usb-surface-comparison.json` records exact help-reply hashes and the observed
  controller recovery requirement: rediscover VID:PID `2108:780B` after updater
  reboot and provide a manual or hub-controlled USB VBUS-cycle fallback.

## Compatible software policy

Compatible Neato application and sound images are cataloged by path, size/status,
and SHA-256 only.  Proprietary `.enc` images, sound-bank bytes, ESP full-flash
backups, keys, and raw dumps must remain outside the public repository.

The side-jack Cruz Rev113 target remains hard-capped at the 3.1 P-family line;
3.2 P-family metadata is recorded only as a blocked image for this board.

Exact stock 2.5, 2.7, and 3.1 all booted and answered `GetVersion` on this
specific robot. P10 remained all-ones/no-TAP before, during, and after every
application transition. The firmware-transition P6 recorder files are
header-only; concurrent CherryDAP CDC capture did not preserve those UART
bursts, so application identity is grounded in the USB result files and
read-only snapshots.

## Next safest experiment

Use a scope or logic analyzer on TCK/TMS/TDI/TDO during the same `scan_chain`
sequence to distinguish “adapter is clocking but target never drives TDO” from
“signal never reaches/leaves P10.”  Do not add SRST/TRST or attempt halt until
that electrical distinction is measured.
