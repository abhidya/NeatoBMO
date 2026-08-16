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

## Exact stock 3.1 transition and parser fork

- Exact Cruz-P stock 3.1 build 17844, image SHA-256
  `03396329a1a47a7358d09bd414d01eddaa5806a50a18f4d9ce2f96edc2d5fab7`,
  was sent once by the guarded updater. The robot ACKed the 847,872-byte image
  with zero retries. The updater's bounded rediscovery window did not verify
  the new application, but a later fresh `GetVersion` and both 3.1 matrix
  preflights independently reported 3.1.17844 on the expected mainboard.
- The first 3.1 pass reproduced inert echo/terminator responses for every plain
  dump/readflash form. `Upload code dump Size 260` instead echoed the command
  and emitted ENQ, selecting the host-to-robot receiver. CAN/CAN did not yield
  a terminator, so the harness stopped without sending payload bytes. A normal
  Neato power cycle cleared the partial receive state; `GetVersion` afterward
  still reported 3.1.17844.
- A reordered completion pass ran all plain and XMODEM rows before the
  size-qualified forms. No XMODEM SOH/STX or robot-to-host data appeared.
  `Upload sound dump Size 260` likewise emitted ENQ and did not confirm CAN/CAN
  cancellation. The harness again stopped without sending a payload.
- The private echo-plus-ENQ transcripts are 28 bytes/SHA-256
  `8a1d15b71a4cbea8b50fc55c84d2983981c153948e340fce3f003f23d41c8179`
  and 29 bytes/SHA-256
  `cb97c49452cbcb79254aea036a75871eb254e43e5ab7884ba92bf0f13bd977e5`.
  P6 remained silent in both runs.

This is a version-specific parser/state-machine difference, not readback. On
3.1, a region token plus `Size` can win over the extra `dump` token and enter
the binary upload receiver even though `dump` is absent from 3.1 help. ENQ
means the robot expects host payload bytes; it does not establish dump
semantics, storage acceptance, firmware extraction, or filesystem access.

## Final stock restore, sound write, and post-sound control

- Exact stock Cruz-P 2.5 build 15893, image SHA-256
  `e1a31ef56e2b617a4056d70c308aab26b7e2cd95e679cb982d697ff4f089c697`,
  was restored from 3.1 in one zero-retry `Upload code reboot` transaction.
  USB returned ACK and the guarded tool verified 2.5.15893 afterward.
- The complete final 2.5 matrix again accepted the volatile sentinel and found
  no dump/readflash data, size-qualified ENQ, XMODEM start, or P6 bytes. Its
  lifetime-stat record reached 2,867,448 bytes without a terminator, so the
  fail-closed harness exited nonzero. A private 65,253-byte tail was drained
  through `0x1a`; fresh identity afterward was 2.5.15893.
- The exact 770,048-byte vendor default sound bank, SHA-256
  `d3969779a6a195812d72b6859454de004ea45beefdee6f1b5c50a2632564b64a`,
  was written once with `Upload sound`. The robot emitted ENQ, accepted the
  payload plus checksum, returned ACK plus terminator, and kept the 2.5
  application identity. The command-level sweep accepted the known populated
  IDs `0–3`, `6–10`, and `19`; audible playback was not independently asserted.
- A complete post-sound 2.5 matrix reproduced the pre-sound capability result:
  no dump/readflash payload, no XMODEM start, and no P6 bytes. Its 2,868,280-byte
  partial lifetime log was followed by a private 65,221-byte tail through the
  terminator and a fresh 2.5.15893 identity check.
- The final tracked health snapshot verified the expected robot serial,
  mainboard 7.1, installed 2.5.15893, `Help Upload`, and command acceptance for
  sound IDs 0 and 19. `GetErr` still reports battery-critical error 238 because
  the bench robot has no battery; this evidence does not claim full
  electromechanical health or a final factory-fallback boot.
- After that verified USB state, the operator reassigned the robot and directed
  that remaining work be offline. ESP firmware restoration and physical removal
  of the existing temporary P6/P10 wiring were therefore not performed or
  verified in this session; the ESP remained on CherryDAP.

## Final classification

The stock serial updater provides a proven host-to-robot binary receive and
exact-image write path. Across repeated 2.5, 2.7, and 3.1 rows, it did not
provide firmware, NAND, filesystem, sound-region, or volatile upload-buffer
readback. Stock 3.1 changes token precedence enough that unadvertised legacy
tokens can still lead to receiver entry, so live versioned transcripts—not
help text alone—are the clean-room contract.

Confidence is high for byte-level transport and write observations, high that
the tested grammar produced no readback, and low for the internal reason those
flags are inert. The next safest acquisition experiment is duplicate raw
external-NAND reads from a donor Cruz board, preserving data, OOB, ECC, and
bad-block markers; it should not be started automatically.
