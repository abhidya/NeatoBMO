# P6 UART Capture Campaign — 2026-08-11

## Fixed wiring

- Neato P6.4 GND -> ESP32 GND
- Neato P6.3 AT91_TXD -> ESP32 GPIO18 (numeric pin 18)
- Leave P6.1, P6.2, ESP32 GPIO17, board pins labelled TX/RX, and all voltage pins disconnected.
- ESP32 UART/COM USB connector -> Mac.
- Never touch J3 ERASE.

The tap is receive-only. Keep Neato power off while changing wires.

## Capture rules

- 115200 8N1 only.
- Start the capture before powering, resetting, pressing buttons, or uploading.
- Use one append-only capture file per experiment; never overwrite a prior capture.
- Say the experiment ID aloud/in notes and record exact button timing and visible LCD result.
- Do not run two serial readers on the ESP32 port.
- Stop before any firmware command that actually erases or programs application flash unless that destructive step is explicitly authorized.

## Sequence

| ID | Experiment | Risk | Status |
|---|---|---:|---|
| A00 | Ordinary cold boot, no buttons | Passive | Complete: `p6_1786482063.log` |
| A01 | Boot while holding Start | Passive | Complete: `20260811_A01_hold_start_cold_boot.log`; 1 PowerUp boot followed by 3 Software-reset boots while START was held |
| A02 | Boot while holding Back | Passive | Complete: `20260811_A02_hold_back_cold_boot.log`; bootloader selected `Loading factory application` |
| A03 | Boot while holding Start + Back; observe only, do not confirm reset/menu actions | Passive until a menu action is selected | Complete: initial `20260811_A03_hold_start_back_cold_boot.log`; controlled repeat `20260811_A03R1_hold_start_back_repeat.log`. Repeat showed factory selected while buttons were held, then installed selected on a software reset after release. |
| A04 | Normal menu navigation and each harmless button | Passive | Complete for available START/BACK panel: initial `20260811_A04_normal_menu_buttons.log`; controlled `20260811_A04R1_normal_back_start.log`. BACK emitted nothing; START woke LCD and played sound but emitted nothing on P6. |
| A05 | Start cleaning, pause, resume, return to base/stop | Robot motion | Pending |
| A06 | Sleep/standby, wake, ordinary shutdown/restart | Passive | Partial: idle LCD-off and START wake/sound captured within A04R1; neither emitted P6 text. Explicit shutdown/restart remains pending. |
| U00 | Attach Neato USB while P6 records | Passive | Complete: `20260811_B00_neato_usb_attach.log`; P6 printed `USB Connected`, with no reset/sound/LCD change attributable to attachment. |
| U01 | Read-only USB identity/configuration/help snapshot while P6 records | Read-only | Complete: `20260811_B01_usb_readonly_snapshot_p6.log` plus `usb-snapshots/WTD41611DD-0037829-P_sw-2-4-15667_20260811T221515Z/`; LCD woke, no sound, no P6 command trace. |
| U02 | BACK-selected factory application USB command-surface comparison | Read-only | Complete: `20260811_U02_factory_app_usb_readonly_p6.log` plus result JSON. P6 confirmed factory boot and USB attach; GetVersion/Help/Help Upload/Help SetSystemMode were byte-identical to installed-app responses. No write performed. |
| U03 | Exact vendor sound package through factory-application no-burn path | Low; uploader-dependent, non-writing | Complete: `20260811_U03_factory_sound_noburn_p6.log` plus result JSON. Factory mode accepted all 770052 framed bytes with SOUND/BLAST/NoWrite, then reported the expected NAND failure; USB returned `0x1A`, health remained good, and no write occurred. |
| B01 | Play known sound with no firmware change | Non-writing | Complete: `20260811_B01_playsound_accept_reject_p6.log`; USB accepted `PlaySound 1`, rejected ID 4 as out of range, P6 emitted nothing, health unchanged. |
| B02 | Exact known-good sound package, no-burn/validation path | Low; uploader-dependent | Complete: `20260811_B02_sound_noburn_exact_p6.log`; P6 showed SOUND/BLAST/770052/NoWrite, receive complete, then `nandflashWrite() fail - -1`; USB returned only `0x1A`; health check unchanged. |
| B03 | One-bit-corrupt copy of B02 through no-burn/validation path | Low; uploader-dependent | Complete: `20260811_B03_sound_noburn_onebit_p6.log`; bit flipped at offset 4108, payload SHA `befe3a38…de1`; USB and P6 outcomes identical to exact bank, health unchanged. |
| B04 | Known Cruz-P 2.5 application through no-burn/validation path | Residual NAND-path risk explicitly authorized | Complete: `20260811_B04R1_code_2.5_15893P_noburn_p6.log`; CODE/BLAST/805892/NoWrite, full receive, NAND failure, USB `0x1A`, identity unchanged. Pre-authorization recorder preserved as `20260811_B04_code_2.5_15893P_noburn_p6.log`; no image bytes were sent in it. |
| B05 | Known Cruz-P 2.7 application through no-burn/validation path | Residual NAND-path risk explicitly authorized | Complete: `20260811_B05_code_2.7_16621P_noburn_p6.log`; guarded CODE/BLAST/805892/NoWrite, full receive, NAND failure, USB `0x1A`, identity unchanged. |
| B06 | Known Cruz-P 3.1 application through no-burn/validation path | Residual NAND-path risk explicitly authorized; maximum allowed version | Complete: `20260811_B06_code_3.1_17844P_noburn_p6.log`; guarded CODE/BLAST/847876/NoWrite, full receive, NAND failure, USB `0x1A`, identity unchanged. 3.2+ remains forbidden and untested. |
| C01 | Program a known sound bank | Destructive write explicitly requested; exact vendor recovery bank | Complete: `20260811_C01_original_sound_burn_p6.log` and result JSON. ACK+TERM, health/slot verification passed. P6: SOUND/BLAST/770052, NAND region `0x400000`, offset 0, `nandFlashWrite() OK`. |
| D01 | Program exact Cruz-P 2.5 application firmware | Destructive write explicitly authorized; no exact installed-2.4 rollback image | Complete: `20260811_D01_factory_code_25_burn_p6.log`, burn/preflight/verification JSON, raw readback probes, and post-upgrade snapshot. Factory-mode `Upload code reboot` wrote 805892 bytes to NAND region `0x10000`, ACKed, rebooted into installed build 15893, passed a true cold boot, and preserved the separate BACK-selected factory build 15667. |

Do not test 3.2+, L1000, Vorwerk, or unidentified images on this Cruz Rev113 board. “No-burn” must be confirmed from the exact uploader command before submitting any image.

## Expected evidence

For every sequence, preserve the raw UART log and record:

- Neato starting state and power source
- buttons held and when released
- USB/upload command, if any
- LCD/menu behavior
- UART messages, reset reason, addresses, sizes, validation/decryption/checksum results
- whether the robot remained functional afterward
