# ESP32-S3 power and direct Neato motherboard USB wiring

Status: proposed installation design; bench and in-robot electrical validation
remain pending (2026-08-15).

This is the installation plan for powering the ESP32-S3 inside the Neato
XV-12 and wiring the ESP32 USB host directly to the Cruz Rev113 motherboard.
The Neato LCD remains installed and continues to use its original motherboard
power supply. The vacuum fan and standalone `Clean` behavior are not part of
this installation.

## Proposed electrical design

The Neato battery rail powers a salvaged automotive 5 V USB supply. That
supply powers both the ESP32-S3 and the Neato USB `VBUS` input. The ESP32-S3
and Neato exchange USB data over `D+` and `D-` and share ground.

```text
Neato switched battery rail (roughly 12-17 V)
                 |
              0.5-1 A fuse
                 |
                 v
       12/24 V automotive USB supply
              regulated 5 V
                 |
                 +--------------------> ESP32-S3 5V/VIN
                 |
                 +--------------------> Neato USB VBUS

ESP32-S3 GPIO20 / USB D+ -------------> Neato USB D+
ESP32-S3 GPIO19 / USB D- -------------> Neato USB D-
ESP32-S3 GND --------------------------> Neato motherboard GND
```

An automotive USB charger is itself a buck converter, but it is a convenient
part to salvage instead of buying a bare converter module. Suitable donors
include a 12/24 V cigarette-lighter USB charger, dashcam hardwire adapter, or
automotive GPS USB charger rated for a regulated 5 V output at 1 A or more.
A 2 A output gives useful margin for ESP32 Wi-Fi current peaks.

Do not use a household AC USB charger, a 3.7 V power-bank boost board, a bare
7805 linear regulator, or the Neato LCD 5 V rail. Removing the vacuum fan does
not add useful capacity to the logic/LCD 5 V regulator because the fan runs
from the battery rail.

## Parts

- ESP32-S3 development board with native USB host support (the current
  YD-style N16R8 board is suitable)
- salvaged 12/24 V automotive USB supply, 5 V at 1 A minimum
- inline 0.5-1 A fuse on the automotive supply input
- sacrificial USB cable or USB breakout for identifying and carrying the four
  USB conductors
- small-gauge stranded wire, heat-shrink tubing, and strain relief
- multimeter with DC voltage and continuity modes
- optional removable 4-pin connector between the ESP32 assembly and Neato
  motherboard

## Important connector distinctions

- The Neato's normal USB interface is a USB **device**. The ESP32-S3 is the
  USB **host** and runs the ESP-IDF CDC-ACM host driver.
- `P6` is a separate 3.3 V debug UART. Do not use `P6.2` or `P6.3` for the
  normal Neato command interface and never apply 5 V to them.
- USB requires four connections: `VBUS`, `D-`, `D+`, and `GND`. The Mini-USB
  `ID` contact is not used.

## Identify the exact Cruz motherboard USB points

Neato used multiple XV motherboard layouts. Published modifications identify
USB access around components such as `D38` on some boards and `D13`/`R90` on
others. Those labels are useful search landmarks, not a universal pinout.
Verify this specific Cruz Rev113 board by continuity before soldering.

1. Power off, undock the robot, and disconnect both battery packs.
2. Insert a sacrificial Mini-USB plug or breakout into the Neato USB socket.
3. Identify its conductors using the breakout labels or a known-good cable:
   red is normally `VBUS`, white `D-`, green `D+`, and black `GND`. Do not
   trust color alone; verify continuity.
4. In continuity mode, trace each plug conductor to an accessible round test
   pad or component pad beside the USB connector.
5. Mark the four verified board points `VBUS`, `D-`, `D+`, and `GND` before
   applying power.
6. Confirm that `GND` also has continuity to battery negative. Confirm that
   `D+` and `D-` are not shorted to each other or to ground.

Do not infer pad order from a photograph of another revision. If an accessible
pad is not available, soldering to wires from a plugged-in sacrificial cable is
safer and more repairable than soldering directly to the tiny USB socket pins.

## Build the power path

1. Keep the automotive charger intact for the first bench test. Its input
   marked `+`, `BAT`, or cigarette-lighter center contact goes to Neato battery
   positive through the fuse. Its input ground or shell goes to battery
   negative.
2. With the ESP32 and Neato USB disconnected, power the robot and measure the
   charger output. It must remain between 4.8 V and 5.2 V.
3. Power off and disconnect the batteries again.
4. Connect regulated 5 V to ESP32 `5V/VIN` and connect charger ground to ESP32
   `GND`. Do not feed 5 V into the ESP32 `3V3` pin.
5. Connect the same regulated 5 V output to the verified Neato USB `VBUS`
   point and the common ground to the verified Neato USB `GND` point.
6. Insulate the charger board if its original enclosure was removed. Mount it
   so neither side can contact the Neato motherboard or chassis hardware.

The battery connection is in parallel with the Neato motherboard. It does not
replace or interrupt the LCD supply. Prefer a switched battery point so the
ESP32 turns off with the Neato's main power; otherwise the ESP32 can eventually
drain the packs while the robot appears off.

## Build the USB data path

Preferred serviceable route:

1. Plug a short cable into the ESP32-S3 native USB connector.
2. At the Neato end, expose the cable conductors or terminate them in a small
   removable connector.
3. Connect cable `D-` to the verified Neato `D-` point.
4. Connect cable `D+` to the verified Neato `D+` point.
5. Connect cable ground to common ground.
6. Connect the cable's VBUS conductor to the regulated 5 V output, not to the
   battery rail.

For a connection made directly at the ESP32 headers, use:

```text
ESP32-S3 GPIO19 -> USB D-
ESP32-S3 GPIO20 -> USB D+
ESP32-S3 GND    -> USB GND
regulated 5 V   -> USB VBUS
```

Keep `D+` and `D-` short, together, and away from the motor leads. Add strain
relief after the connection passes continuity and enumeration tests.

## YD ESP32-S3 jumpers

For the direct wiring above, leave both `USB-OTG` and `IN-OUT` open because the
automotive supply feeds Neato `VBUS` directly.

Bridge `USB-OTG` only when using the YD board's native USB connector to deliver
VBUS and powering the board through a USB connector that supplies that VBUS.
Do not bridge `IN-OUT`; it can couple supplies and create an unwanted backfeed
path. When in doubt, leave both open and use the explicit 5 V VBUS wire shown
in the final design.

## Bring-up and verification

Perform these checks in order:

1. With batteries disconnected, check that 5 V is not shorted to ground and
   that `D+` is not shorted to `D-`.
2. Disconnect the Neato USB wiring and power only the automotive supply plus
   ESP32. Confirm stable ESP32 boot and Wi-Fi operation.
3. Measure ESP32 `5V/VIN`; require 4.8-5.2 V during Wi-Fi startup.
4. Power down, attach Neato `GND` and `VBUS`, power up, and verify that Neato
   VBUS is approximately 5 V.
5. Power down, attach `D+` and `D-`, then power up and verify USB enumeration
   in the ESP32 logs.
6. Send a non-moving command first, such as `GetVersion`, and verify the ASCII
   response terminates with `0x1A`.
7. Test `GetAnalogSensors` and `GetLDSScan` before enabling any motor command.
8. Only after communication is stable, enable `TestMode On` and test movement
   with the wheels lifted clear of the bench and an immediate stop available.

Because standalone vacuum cleaning is no longer required, Neato USB VBUS may
remain asserted continuously. Reserve a footprint for an optional high-side
VBUS switch: software reconnect is the default recovery path, while switched
VBUS remains available if validation shows that a wedged USB device cannot
recover by close and reopen alone.

## Stop conditions

Disconnect power immediately if any of these occur:

- automotive supply output exceeds 5.2 V
- output falls below 4.8 V or the ESP32 repeatedly brownouts
- charger, wiring, or motherboard component becomes hot
- USB fails to enumerate after confirming the data pair is not reversed
- LCD resets, flickers, or behaves differently after adding the ESP32 supply

Do not proceed by moving the ESP32 onto the LCD rail. Diagnose the battery tap,
automotive supply, common ground, and USB data-pair wiring instead.

## Reference photographs and prior builds

- Direct Neato USB solder points on one later XV motherboard revision:
  <https://github.com/jeroenterheerdt/neato-serial/blob/master/neato-miniusb.jpg>
- Battery-rail power and embedded USB-host installation:
  <https://github.com/jeroenterheerdt/neato-serial>
- XV board research and USB behavior:
  <https://wiki.recessim.com/view/Neato_XV-11>
