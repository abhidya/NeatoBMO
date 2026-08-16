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

## Size-qualified 2.5 pass

- Ten `Size 260`-qualified `dump`/`readflash` forms also returned only their
  echoes and terminators. None emitted ENQ, so omission of `Size` does not
  explain the inert readback flags.
- The second 2.5 pass again accepted the volatile sentinel and produced no P6
  bytes or XMODEM block start.
- `GetLifeStatLog` streamed 1,908,856 bytes without reaching its terminator in
  the original 30-second window. A later identity preflight safely rejected
  the still-active stream before sending any firmware header or payload. The
  residual 327 bytes were drained privately through the terminator, after
  which `GetVersion` again proved stock 2.5.15893.

## Exact stock 2.7 transition and matrix

- Exact Cruz-P stock 2.7 build 16621, image SHA-256
  `2e6033b1ef5440bed949de20e89563d7cb3dda41e0eb5e371c9d86dceeb1633f`,
  was sent once by the guarded `Upload code reboot` path. The robot ACKed the
  805,888-byte image, the tool made zero retries, and post-reboot `GetVersion`
  reported 2.7.16621 on the expected robot and mainboard.
- This proves USB application identity after the stock transition, not full
  motor, LDS, sound, factory-fallback, or cold-boot health.
- The complete 2.7 matrix matched 2.5: sentinel receive worked; all unqualified
  and size-qualified dump/readflash forms returned no payload; all XMODEM
  starts failed to produce SOH/STX; and P6 produced zero device bytes.
- `GetLifeStatLog` emitted 2,867,960 captured bytes plus a private 65,242-byte
  tail before its terminator. A clean post-drain `GetVersion` confirmed
  2.7.16621 before any later persistent operation.
- The parallel P6 write capture contained only the capture tool's 74-byte
  session header, not target-emitted UART bytes. Its private SHA-256 is
  `5b326f69e01f47d9a7b151f8713191dbc912c6e60c05a9bb64e6eb14187b3247`.

The strongest supported conclusion remains narrow: stock 2.5 and 2.7 expose a
working host-to-robot sized upload receiver, but the tested advertised flags do
not return the volatile upload area or any firmware/NAND/filesystem bytes.
