# NeatoOS ARM926 execution probe

Experiment date: 2026-08-12  
Target: XV-12 `WTD41611DD-0037829-P`, Cruz Rev113/P, mainboard 7.1  
Installed application before experiment: `2.5.15893`  
Independent factory application: `2.4.15667`

## Objective

Determine whether a minimal custom ARM926EJ-S payload can execute, or identify
the first layer that rejects it. The execution sentinel is:

```text
NEATOOS RAW V0
```

The canary intentionally has no motors, LDS, filesystem, sound, USB, or
application-service dependencies.

## Verified facts

- P6 is `115200 8N1`; passive wiring is P6.3/robot TX to ESP32 GPIO18 and
  P6.4 to GND. P6.2 is disconnected.
- Installed 2.5 and factory 2.4 are independent boot targets.
- Exact stock Cruz-P 2.5 recovery image:
  `e1a31ef56e2b617a4056d70c308aab26b7e2cd95e679cb982d697ff4f089c697`.
- Application uploads program NAND logical region `0x10000`; sound uploads use
  `0x400000`.
- AT91SAM9XE DBGU base/register offsets used by the canary match the
  [Microchip SAM9XE datasheet](https://ww1.microchip.com/downloads/en/DeviceDoc/Atmel-6254-32-bit-ARM926EJ-S-Embedded-Microprocessor-SAM9XE_Datasheet.pdf).
- The stock bootstrap prints through DBGU before starting the application, so
  the canary preserves inherited baud/mode state and only enables transmitter
  output.

## Build artifacts

The freestanding build targets `-mcpu=arm926ej-s -marm`. The linker address
`0x20000000` remains a hypothesis about the vendor application load mapping;
the experiment must not treat it as proven.

| Artifact | Bytes | SHA-256 |
|---|---:|---|
| `neatoos-raw.bin` | 129 | `bf7f415437ccac7e3fa5251d83765a33c365023722833ea1bf5f703f1f9b6c12` |
| structural probe | 1,024 | `566845909e34f46fece26ea77395a48e752760fc2b8a1534d025e3b8e8f3017a` |
| reference-header probe | 1,024 | `fd6d34e8fc1e165f9bde3385ba4e9745b8c8c863b077e7d9cf9e16a485a1cf88` |
| full-length reference-header probe | 805,888 | `cb9d7cc2de782f626ad8e8c8002ff52fefaa93976b8780c6784bcdfad3734e7f` |

The build was repeated from clean state; ELF and raw binary were byte-identical.
`readelf` reports ELF32 little-endian ARM EABI5, ARM state, entry
`0x20000028`. Objdump confirms a reset stub, stack initialization, DBGU TX
enable, TX-ready polling, and repeated writes of the exact sentinel.

## Image formats

### Raw ARM

Pure 129-byte executable payload. It is not a Neato container.

### Structural envelope

- declared length: 129
- byte 4: `0x02`
- bytes 5–9: `neato`
- experimental field: `39202700bde6319aeb23920d278d7c42`
- header/body boundary: `0x200`
- raw payload followed by 383 zero padding bytes
- **NOT ENCRYPTED; NOT AUTHENTICATED**

### Reference-header envelope

- exact first 512 bytes from SHA-verified Cruz-P 2.5 reference
- vendor declared length remains 805,156 and deliberately does not match the
  129-byte replacement body
- vendor unknown field: `e7413c42631da5ead7af3b3853d40439`
- same raw payload followed by 383 zero padding bytes
- **NOT ENCRYPTED; NOT AUTHENTICATED**

### Full-length reference-header envelope

- exact first 1,024 bytes of the short reference-header probe
- exact total size of the stock Cruz-P 2.5 representation: 805,888 bytes
- every byte after offset `0x400` is deterministic SHA-256 counter-mode filler
- vendor declared length remains 805,156
- **NOT ENCRYPTED; NOT AUTHENTICATED**

## Experiment matrix

| ID | Representation | Command | USB | P6/NAND | Classification |
|---|---|---|---|---|---|
| E03a | raw ARM | `Upload code noburn` | terminator, identity healthy | 133 bytes received; `NoWrite`; NAND failure | 3. Full receive but write rejection |
| E03b | structural | `Upload code noburn` | terminator, identity healthy | 1,028 bytes received; `NoWrite`; NAND failure | 3. Full receive but write rejection |
| E03c | reference header | `Upload code noburn` | terminator, identity healthy | 1,028 bytes received; `NoWrite`; NAND failure | 3. Full receive but write rejection |
| E04 | raw ARM | `Upload code reboot` | ENQ then ACK | 133 bytes written OK at `0x10000`; boot printed `Illegal size for application.`; automatic factory fallback | 4. NAND write succeeds but boot rejects image |
| E05 | structural | `Upload code reboot` | ENQ then ACK; factory 2.4 returned | 1,028 bytes written OK; boot printed `Illegal size for application.`; automatic factory fallback | 4. NAND write succeeds but boot rejects image |
| E06 | reference header | `Upload code reboot` | ENQ then ACK; USB did not return | 1,028 bytes written OK; boot printed `Starting app`; no sentinel or later P6 output | 5. Boot hands off, then image hangs/crashes before sentinel |
| E07 | exact stock Cruz-P 2.5 | `Upload code reboot` | ENQ then ACK; USB identity `2.5.15893` | 805,892 bytes written OK; installed NEROS 15893 started | Recovery verified |
| E09 | full-length reference header | `Upload code noburn` | terminator; identity healthy before and after | 805,892 bytes received; `NoWrite`; NAND failure | Transport complete; bootability unproven |
| E10 | full-length reference header | `Upload code reboot` | ENQ then ACK; factory 2.4 returned | 805,892 bytes written OK; `Checksum error in application binary`; factory fallback | NAND write succeeds; boot checksum gate rejects image |
| E11 | exact stock Cruz-P 2.5 | `Upload code reboot` | ENQ then ACK; USB identity `2.5.15893` | 805,892 bytes written OK; installed NEROS 15893 started | Recovery verified |

The three `noburn` results are intentionally not called image acceptance. They
only show that the receiver entered and consumed the declared transport bytes.

## E04 first rejection result

P6 recorded:

```text
Upload complete - 133 bytes received
nandflashWrite() - region=0x10000 offset=0 bytes=133
Upload - nandFlashWrite() OK
Reboot in 1 sec ...
Loading installed application
Illegal size for application.
Loading factory application
Starting app
NEROS Build 15667 Oct 28 2011 11:25:50
```

No `NEATOOS RAW V0` sentinel appeared.

This proves:

1. `Upload code reboot` does not require a Neato envelope before programming
   the raw 129-byte image.
2. The application-region NAND write completed successfully.
3. A later boot-selection/application-validation layer rejected the stored
   representation specifically on size before custom execution.
4. The independent factory application automatically remained bootable.

It does **not** yet identify the minimum accepted size or prove whether a
size-correct image next fails cryptographic validation, transform/decrypt,
entry/load mapping, or CPU execution.

## E05 structural-envelope result

The 1,024-byte structural probe was written successfully, but the boot result
was identical to raw ARM:

```text
Upload complete - 1028 bytes received
nandflashWrite() - region=0x10000 offset=0 bytes=1028
Upload - nandFlashWrite() OK
Loading installed application
Illegal size for application.
Loading factory application
Starting app
```

The structural header declared a 129-byte payload. This shows that matching the
outer `0x02`/`neato` structure is insufficient; the boot layer rejects the
small declared application size before any observable ARM execution.

## E06 reference-header result

Replacing only the first 512 bytes with the exact stock Cruz-P 2.5 header
changed the boot path:

```text
Upload complete - 1028 bytes received
nandflashWrite() - region=0x10000 offset=0 bytes=1028
Upload - nandFlashWrite() OK
Loading installed application
Starting app
```

No `Illegal size for application.` message appeared. No `NEATOOS RAW V0`,
NEROS banner, watchdog reset, factory fallback, or other P6 output followed in
the next 20 seconds, and USB did not enumerate.

The strongest supported conclusions are:

1. The copied reference header supplies enough information to pass the explicit
   boot size check. The first four bytes declare 805,156, whereas the
   structural probe declared 129.
2. `Starting app` proves the bootstrap reached its application handoff path;
   it does not prove that our ARM instructions executed.
3. The first remaining rejection/failure layer is after explicit header/size
   acceptance and before the UART sentinel. Candidate causes remain decrypt or
   transform behavior, authentication without a printed error, wrong load or
   entry mapping, invalid stack/memory assumptions, or an immediate crash/hang.
4. The exact vendor header does not make a 129-byte plaintext body a working
   application.

There is an important NAND confound: E06 uploaded only 1,028 framed bytes while
its copied header declared a much larger application. The updater reports only
the written byte count; it does not reveal whether the unwritten remainder of
the application region was erased, preserved from a prior image, or otherwise
transformed. Therefore the E06 result cannot establish header/body coupling or
the exact bytes consumed after the first page.

## E09-E10 full-length checksum-gate result

The controlled full-length image was 805,888 bytes, matched its manifest and
SHA-256 `cb9d7cc2de782f626ad8e8c8002ff52fefaa93976b8780c6784bcdfad3734e7f`,
and had the same first 1,024 bytes as E06. Its remaining 804,864 bytes were
deterministic filler, eliminating E06's unwritten-tail confound.

E09 sent the image through `Upload code noburn`. USB completed with the normal
terminator, the installed 2.5 identity remained healthy before and after, and
P6 recorded all 805,892 framed bytes with `NoWrite` followed by the expected
NAND-write failure. This established transport only.

E10 then issued exactly one manifest-locked `Upload code reboot`. USB returned
ACK and P6 recorded:

```text
Upload complete - 805892 bytes received
nandflashWrite() - region=0x10000 offset=0 bytes=805892
Upload - nandFlashWrite() OK
Loading installed application
Checksum error in application binary
Loading factory application
Starting app
NEROS Build 15667 Oct 28 2011 11:25:50
```

No `NEATOOS RAW V0` sentinel appeared. The full-length result proves that a
copied stock header and stock-sized stored representation are insufficient: a
later application checksum gate covers information not reproduced by the
controlled body. It also resolves E06's apparent `Starting app` result as
confounded by its short write; E06 cannot be used as evidence that the same
controlled prefix passes validation when the complete application region is
known. The message alone does not identify the checksum algorithm, its field,
whether it covers encrypted or transformed bytes, or whether a separate
authentication layer also exists.

## Recovery result

After E06, holding BACK at cold power selected factory NEROS 15667 and its USB
updater. The exact stock Cruz-P 2.5 image was re-hashed immediately before use:

```text
bytes   805888
sha256  e1a31ef56e2b617a4056d70c308aab26b7e2cd95e679cb982d697ff4f089c697
```

E07 wrote 805,892 framed bytes to NAND `0x10000`, returned USB ACK, and P6
showed installed NEROS build 15893 starting. A final read-only USB query
confirmed serial `WTD41611DD-0037829-P`, software `2.5.15893`, and mainboard
7.1. The user declined an additional cold-boot repetition because the restored
application was already running. Factory recovery had been directly verified
immediately before E07.

After E10 fell back automatically to factory 2.4.15667, E11 re-hashed and wrote
the same exact stock Cruz-P 2.5 image. USB returned ACK, P6 recorded the full
NAND write followed by `Starting app` and NEROS build 15893, and a fresh
`GetVersion` confirmed software `2.5.15893` on the approved robot.

## Sound-bank side experiment

To silence periodic bench beeps during preparation, all live PCM fields were
temporarily zeroed while preserving directory, record headers, lengths, and
all non-PCM bytes. SHA-256:
`ebce7f200a8a3f5f0676c475b7abb3aba5926ce559fb81bd3c3e0f37c042449a`.
USB returned ACK+TERM and P6 recorded a successful write at `0x400000`.
Before application experiments, the exact original bank
`d3969779a6a195812d72b6859454de004ea45beefdee6f1b5c50a2632564b64a`
was restored and verified.

## Evidence

- `captures/20260812_E00_neatoos_preflight_boot_p6.log`
- `captures/20260812_E01_silent_sound_burn_result.json`
- `captures/20260812_E02_original_sound_restore_result.json`
- `captures/20260812_E03_neatoos_receiver_matrix_p6.log`
- `captures/20260812_E03a_neatoos_raw_noburn_usb.json`
- `captures/20260812_E03b_neatoos_structural_noburn_usb.json`
- `captures/20260812_E03c_neatoos_reference_header_noburn_usb.json`
- `captures/20260812_E04_neatoos_raw_burn_p6.log`
- `captures/20260812_E04_neatoos_raw_burn_usb.json`
- `captures/20260812_E05_neatoos_structural_burn_p6.log`
- `captures/20260812_E05_neatoos_structural_burn_usb.json`
- `captures/20260812_E06_neatoos_reference_header_burn_p6.log`
- `captures/20260812_E06_neatoos_reference_header_burn_usb.json`
- `captures/20260812_E07_stock_25_restore_p6.log`
- `captures/20260812_E07_stock_25_restore_usb.json`
- `captures/20260812_E08_final_recovery_verification.json`
- `captures/20260812_E09_full_length_noburn_p6.log`
- `captures/20260812_E09_full_length_noburn_usb.json`
- `captures/20260812_E10_full_length_burn_p6.log`
- `captures/20260812_E10_full_length_burn_usb.json`
- `captures/20260812_E11_stock_25_restore_p6.log`
- `captures/20260812_E11_stock_25_restore_usb.json`

## Next experiment

Continue one variable at a time while the factory fallback and exact stock
recovery image remain intact:

1. Identify the application checksum algorithm and stored checksum field
   offline by comparing multiple exact, independently verified Cruz-P images.
   Do not infer that the unknown header field is a checksum without evidence.
2. Once the checksum is understood, vary one body byte and update only the
   proven checksum field. Keep total stored size, header, canary, and all other
   bytes fixed to distinguish checksum acceptance from later transform,
   authentication, load, or execution failures.
3. Prefer read-only NAND acquisition after each controlled write, if a safe path
   becomes available, to determine whether the stored representation equals the
   upload and confirm the complete application-region contents.
4. If a checksum-valid controlled probe reaches `Starting app` but stays silent,
   test load/entry hypotheses one at a time. In particular, the current
   `0x20000000` link address remains unverified; build alternate canaries only
   after establishing the vendor plaintext load mapping or acquiring runtime
   plaintext.
5. Restore exact stock Cruz-P 2.5 after every application experiment and retain
   the BACK-selected factory path as the recovery invariant.

Do not touch J3/ERASE, factory/bootloader regions, or firmware 3.2+.
