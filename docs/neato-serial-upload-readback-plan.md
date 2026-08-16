# Cruz serial upload/readback experiment plan

## Goal

Determine whether the stock `Upload` parser can expose its volatile upload-save
area or any installed region, while producing evidence useful to:

1. a clean-room command-compatible rewrite;
2. debug and storage/readback access; and
3. guarded application patch and recovery workflows.

Help text is not treated as proof of capability. Stock 2.5 and 2.7 advertise
`dump`, `xmodem`, and `readflash`; earlier isolated probes returned only command
echoes/terminators and no XMODEM stream. Stock 3.1 omits `dump` and `xmodem` but
still advertises `readflash`.

## Executable sequence

1. Gate on USB VID:PID `2108:780B`, robot serial
   `WTD41611DD,0037829,P`, mainboard 7.1, and an explicitly supported stock
   version.
2. Start passive P6 capture when the CherryDAP CDC UART is present.
3. Capture `GetVersion`, `Help Upload`, and `GetErr` before the transaction.
4. Send one deterministic 256-byte project-owned sentinel with
   `Upload code noburn Size 260`. This deliberately exercises `Size`, payload,
   additive checksum, ENQ, and completion framing without requesting a
   persistent application write.
5. Immediately capture the fixed grammar matrix:
   `Upload dump`, region-qualified `dump`, `Upload readflash`, and
   region-qualified `readflash`, in both option orders.
6. Probe the equivalent XMODEM-start forms with receiver `C` handshakes. Stop
   and cancel with CAN/CAN on the first SOH/STX block marker; do not ACK or
   collect a full proprietary image into Git.
7. Capture `GetErr`, `GetSysLog`, `GetLifeStatLog`, and `GetVersion` after the
   matrix. Store raw acquisition privately first; publish only verified
   non-secret text, byte counts, hashes, and classifications.
8. Run the matrix around the exact-hash persistent transition sequence
   2.5 → 2.7 → 3.1 → 2.5. Each transition uses `Upload code reboot Size ...`,
   sends the complete allowlisted payload plus additive checksum exactly once,
   and verifies USB identity after re-enumeration. Capture before/during/after
   P6 where the current adapter permits it.
9. Perform one exact vendor-default sound-bank write on the final 2.5 state,
   verify its known slot map, then repeat the upload-save-area queries. This
   tests whether the sound updater leaves different volatile/readback state.
10. Commit and fast-forward push the plan/tooling first, then each coherent
    evidence and analysis batch. Never commit proprietary application, sound,
    ESP-backup, or newly exposed dump bytes.

## Persistent-write and erase gates

- Exact stock 2.5, 2.7, and 3.1 writes are supported only through the guarded
  exact-hash/identity/confirmation tool. Build 3.2 and unknown images remain
  blocked.
- A custom application write is not attempted until its envelope/integrity
  result is predicted and the factory fallback plus exact stock recovery path
  have been freshly verified.
- No documented stock serial `erase` command has been observed. Do not invent
  one. J3/AT91 ERASE and GPNVM changes remain outside this plan. Any future
  erase test requires an exact region, a matching donor target, duplicate
  recoverable backups, and a separately reviewed recovery procedure.

## Stop conditions

- USB identity changes or the expected robot/mainboard cannot be verified.
- The robot requests data for any command other than the single fixed
  `noburn` sentinel transaction.
- A dump/readflash response contains non-text bytes not attributable to the
  project-owned sentinel/checksum.
- XMODEM SOH/STX appears: cancel, preserve privately, and report before any
  full receive.
- During the volatile sentinel phase, P6 reports a successful persistent NAND
  write (`nandFlashWrite() OK`), erase, factory fallback, or new power/software
  reset. The expected `noburn` `NoWrite`/`nandflashWrite() fail - -1` diagnostic
  is recorded and does not itself abort the readback matrix.
