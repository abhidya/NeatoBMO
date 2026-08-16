# Stock 2.5 upload-save-area probe analysis

Target: XV-12 `WTD41611DD,0037829,P`, mainboard 7.1, stock
`Software,2,5,15893`.

## Observations

- The robot emitted ENQ for `Upload code noburn Size 260` and accepted the
  complete 256-byte project-owned sentinel plus its four-byte additive
  checksum. Completion was a terminator rather than ACK; version and `GetErr`
  were unchanged afterward.
- Fourteen `dump`/`readflash` grammar variants, including code, sound, LDS, and
  reversed option order, returned only the command echo plus `0x1a` terminator.
  None returned the sentinel or any non-text bytes.
- Six XMODEM-start variants terminated before the host handshake. The following
  receiver `C` and CAN/CAN bytes were parsed as a separate unknown command.
  No SOH/STX block marker appeared.
- `GetSysLog` returned only echo plus terminator.
- `GetLifeStatLog` exposed 498,744 bytes of textual lifetime statistics. The
  first harness revision used an eight-second hard limit; this record ends
  mid-row and is therefore a large partial capture, not a complete log. Later
  runs use a 30-second bound and record whether `0x1a` was observed.
- The CherryDAP CDC/P6 path produced zero bytes during this application-state
  transaction, so it provides no independent NAND/parser trace for this row.

## Interpretation

The sized sentinel proves that the stock upload receiver accepted host-to-robot
framing and payload bytes. It does not prove a persistent NAND write because
the command included `noburn`, and it does not prove that `dump` accesses the
same volatile buffer. The tested unqualified grammar does not expose upload
save-area, application, sound, LDS, NAND, flash, or filesystem bytes.

The XMODEM behavior is more consistent with an upload transport modifier than
a robot-to-host readback mode, but a missing size/region prerequisite remains
an alternative. The next pass therefore adds exact `Size 260`-qualified
dump/readflash queries; an ENQ response is classified as selection of the
host-to-robot receiver and is cancelled without a second payload.

## Product impact

- Clean-room rewrite: the parser accepts a sized binary receive transaction;
  advertised readback flags are not sufficient to infer output behavior.
- Debug/read access: `GetLifeStatLog` is a substantial serial diagnostics
  source, while firmware/filesystem readback remains unavailable.
- Patching: the updater transport is reachable, but arbitrary patched-image
  acceptance and envelope integrity remain separate gates. Persistent testing
  proceeds only through exact-hash stock and vendor-sound write tools.

Confidence is high for the observed byte-level responses and low for why the
readback flags are inert.
