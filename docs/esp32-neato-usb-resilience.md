# ESP32-S3 to Neato XV USB resilience

Status: implemented software recovery, hardware validation pending (2026-08-15).

## Symptom and RCA status

Observed symptom: the ESP32-S3 can retain power and network service while its
CDC-ACM connection to the Neato stops carrying useful traffic after an idle
period. Physically replugging USB restores service.

The repository did not contain a hardware capture of the failing interval, so
the physical root cause is not yet proven. The code-level failure mode is
proven: `neato_usb.c` considered a CDC handle healthy until the USB stack
reported a physical disconnect. A silent RX stall or repeated transfer failure
left `s_dev` non-null forever, so the connection task never reopened it.

Ranked causes:

1. **Stale CDC session after an idle/power-state transition.** High confidence
   for the software lockout: it exactly explains why replugging recovers while
   the old handle remains marked connected.
2. **Neato sleep/wake behavior consumes or delays the first command.** Medium
   confidence. Existing Neato controller software sends a harmless wake-up
   message before the requested command.
3. **USB suspend/resume interaction.** Medium-low confidence for this build.
   ESP-IDF supports global suspend and transfer-triggered resume, but this
   firmware does not enable an automatic suspend timer or ESP light-sleep power
   management. Suspend/resume events still need to be tolerated and logged.
4. **VBUS or signal-integrity fault.** Hardware-dependent. A marginal 5 V rail,
   cable, connector, or Neato USB PHY can require a real VBUS cycle; reopening a
   class-driver handle cannot repair that condition.

## Learnings imported into BMO Body firmware

- Treat received bytes—not a non-null device handle—as proof that the link is
  alive.
- Exercise the link periodically with a harmless status command. The existing
  10-second `GetCharger` heartbeat provides this probe.
- Recycle the CDC session after bounded consecutive TX failures or a bounded
  interval without RX, then let the single connection task reopen and
  reinitialize it.
- Never recycle during a binary sound/sound-bank transaction; preserve the
  transaction mutex and fail the operation cleanly first.
- Reapply line coding and CDC control-line state after every open. Host serial
  implementations conventionally assert DTR; the Neato setup must not rely on
  state retained by a previous enumeration.
- Keep recovery bounded and observable. Log the recovery reason instead of
  rebooting the ESP32 on the first error.

## External evidence

- Espressif's current ESP32-S3 USB Host guide documents global suspend/resume,
  client suspend/resume events, automatic resume when a transfer is submitted,
  and the special ordering required if ESP light sleep is enabled:
  <https://docs.espressif.com/projects/esp-idf/en/latest/esp32s3/api-reference/peripherals/usb_host.html>
- Espressif's CDC-ACM host component is the upstream class driver used here. Its
  v2.4.0 changelog includes suspend/resume and remote-wakeup support; this repo
  currently locks v2.4.0 with ESP-IDF 5.5.3:
  <https://github.com/espressif/esp-usb/tree/master/host/class/cdc/usb_host_cdc_acm>
- `jeroenterheerdt/neato-serial`, tested on Neato XV hardware, tries configured
  serial devices, sends a wake-up probe before commands, reconnects after I/O
  errors, and provides USB power cycling through a hub or relay when a logical
  reconnect is not enough:
  <https://github.com/jeroenterheerdt/neato-serial>
- Vacuula/Fang confirms the same command-interface family spans the XV
  generation, but its current recommended ESP integration uses the internal
  UART/debug interface rather than the XV USB device port. Its protocol and
  firmware-version findings are useful; its transport reliability is not
  direct evidence for this USB-host link:
  <https://github.com/vacuula/fang>

## Hardware fallback

If logs show repeated successful CDC reopen attempts without subsequent RX,
add a high-side USB VBUS load switch (or relay) controlled by a dedicated ESP32
GPIO. Switch only the Neato USB VBUS sense/power path; do not cycle the ESP32's
own 5 V rail. Recovery order should then be:

1. close and reopen the CDC handle;
2. if RX still does not return after a bounded number of reopen attempts, turn
   Neato USB VBUS off for about one second and restore it;
3. reopen, assert DTR, and run the normal connect probe;
4. reserve a full ESP32 reboot as the last resort.

The current direct-wiring design ties both loads to the same regulated 5 V
supply and exposes no controllable VBUS switch, so firmware must not pretend it
can perform this final recovery step yet.

## Hardware verification loop

1. Flash the hardened build and capture `neato_usb` logs over Wi-Fi (`:2323`) so
   observation does not depend on the USB path under test.
2. Leave the Neato idle past the previously failing interval.
3. Confirm each 10-second `GetCharger` produces RX. A missing-RX interval should
   log a CDC recycle, followed by `Neato connected!` and fresh version/charger
   output without replugging.
4. Repeat at least five idle/wake cycles and one physical unplug/replug cycle.
5. If a recycle occurs but enumeration or RX does not recover, measure VBUS at
   the Neato connector and use the controllable-VBUS fallback above.
