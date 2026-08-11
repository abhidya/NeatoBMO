# P6 capture — session handoff (baud sweep in progress)

## Goal
Tap the Neato XV-12 **P6 debug UART** with an **ESP32-S3** and capture the
AT91SAM9XE cold-boot log over USB, to see whether we reach the SAM-BA ROM
monitor (key-extraction route). We are currently **finding the correct P6 baud
rate** via a sweep firmware, because 115200 (the assumed rate) produced garbage.

## Hardware
- **ESP32-S3-DevKitC-1**, PlatformIO env `esp32s3` (`esp32-body/platformio.ini`).
  Connected to the Mac via its **native USB port** → shows as `/dev/cu.usbmodem*`
  (name can change on replug — always `ls /dev/cu.usbmodem*` to find it).
- **Neato P6 header** (3.3 V UART, from `docs/neato-hardware-access.md`):
  `P6.1`=square pad/unused, `P6.2`=AT91_RXD, `P6.3`=AT91_TXD, `P6.4`=GND.
- **J3 = ERASE line — NEVER short it (permanent brick).**

## Wiring (this exact wiring already worked — it delivered 8377 bytes)
```
P6.4 GND      -> ESP32 GND         (essential; lifted GND = no data)
P6.3 AT91_TXD -> ESP32 GPIO18 (RX) (THE critical wire — robot output in)
P6.2 AT91_RXD -> ESP32 GPIO17 (TX) (only needed to send TO the robot; optional)
P6.1          -> unused
```
**No jumper between GPIO17 and GPIO18** (that was a self-test loopback — remove it).

## Firmware on the chip right now: the BAUD-SWEEP diagnostic build
Source: `esp32-body/src/debug_uart.c`. Three compile-time variants:
- **(no flag)** normal bridge: mirrors P6 RX → USB console **and** TCP 3334.
- **`-DP6_SELFTEST`** transmits `P6-SELFTEST N` out GPIO17 every 1 s (loopback test).
- **`-DP6_BAUDSWEEP`** (CURRENTLY FLASHED) receive-only; cycles UART1 through
  `{115200,57600,38400,19200,9600,4800,230400,460800,921600,250000,74880}`,
  ~1.2 s each, printing `==== BAUD N ====` before each window, mirroring all RX
  to the USB console. Whichever banner is followed by **readable text** is the
  real P6 rate.

## Capture (USB only — NO Wi-Fi; the sweep build has no TCP)
`pio device monitor` FAILS when backgrounded (needs a TTY). Use the pyserial
reader instead (read-only, tees to file), with the framework python that has pyserial:
```
/Library/Frameworks/Python.framework/Versions/3.14/bin/python3 \
  ~/Documents/neato/tools/p6_capture.py <PORT> ~/Documents/neato/neato-p6-boot.txt
```
Only ONE reader may hold the port at a time (two readers split the byte stream —
this bit us earlier). `pkill -f p6_capture.py` before restarting.

## Flashing (only if firmware must change) — native-USB is flaky
esptool errors seen: `0xa unsupported`, `no sync reply`, `invalid format`.
Reliable recovery: **unplug ESP32 → hold BOOT → replug while holding BOOT →
keep ~2 s → release**, then flash immediately:
```
cd ~/Documents/neato/esp32-body
PLATFORMIO_BUILD_FLAGS=-DP6_BAUDSWEEP pio run -e esp32s3 -t upload --upload-port <PORT>
```

## Baud-analysis method
Split the capture on `==== BAUD N ====`; for each baud, score the payload for
printable-ASCII ratio / longest readable run. The segment with real words wins.
The Neato boot log is a **one-time burst at power-on**, so the robot must be
**power-cycled while the sweep runs** to catch it.

## Where we are (blocker)
- Proven: RX path works; `P6.3→GPIO18` correct (8377 bytes received).
- Garbage not silence ⇒ correct wiring, **wrong baud** ⇒ hence the sweep.
- Regression: during ESP32 replug/BOOT handling the **P6 connection came loose**
  (only banners, zero robot bytes), then the **ESP32 got unplugged** (port gone,
  capture died).
- **BLOCKED ON:** reconnect ESP32 USB + reseat P6 wires + power-cycle Neato.

## Next steps for the agent
1. `ls /dev/cu.usbmodem*` to get `<PORT>`. If absent, ESP32 still unplugged.
2. `pkill -f p6_capture.py`; start ONE capture (command above).
3. Tell the user to power-cycle the Neato (off ~5 s, on).
4. Watch `neato-p6-boot.txt`; ignore banner-only growth. When real (non-banner)
   bytes appear, run the baud-analysis split and report the clean baud.
5. Once the baud is known: reflash the **normal** build (no flag), set
   `DEBUG_UART_BAUD` to that rate in `debug_uart.c`, and do a clean cold-boot
   capture of the full AT91 boot log. Then assess ROM-monitor vs app console.

## Safety
Battery-disconnect while probing; ESD-safe; **never touch J3**; do this on the
scavenged Rev113 robot, not the working BMO body.
