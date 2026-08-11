# Neato `.enc` firmware envelope — crypto analysis & attack surface

Right-to-repair reverse engineering of the user's own Neato XV-12 / Vorwerk
Cruz Rev113 firmware. Consolidates the 2026-08-10 static analysis (local
`.enc` images) and OSINT. **No public break exists; the payload cipher is
implemented correctly; the only route is on-chip key extraction.**

## Image format (measured)

```
off 0-3     payload length, LE u32          ] plaintext
off 4       0x02  format version            ] header
off 5-9     "neato" magic                   ]
off 10-15   zero                            ]
off 16-31   16-byte per-image field         ] nonce/MAC, role unresolved
off 32-511  zero pad                        ]
off 512 → EOF   AES-CBC ciphertext stream, FIXED key + FIXED IV
```

Builds analysed: `XV11App.{15893(2.5),16621(2.7),17844(3.1),18755(3.2)}.P.bin.enc`.

## What the ciphertext proves

- **AES-class 128-bit, CBC mode.** Body entropy 7.9998; flat histogram; no
  repeated 16 B blocks (not ECB) and no 8 B repeats (not a 64-bit cipher).
  Every cross-build first-divergence is exactly 16-byte-aligned and
  avalanches to EOF — the textbook CBC signature.
- **Fixed key + fixed IV across the whole product line.** The leading CBC
  blocks are byte-identical across all four builds (the all-4 prefix is
  `512–624`, 112 B = 7 blocks); identical firmware-head plaintext →
  identical ciphertext only if key *and* IV are constant. Divergence
  offsets track where each build's firmware first differs: 2.5-vs-3.1=624,
  3.1-vs-3.2=2032, 2.5-vs-2.7=2080.
- **The header `off 16-31` field is NOT the CBC IV** (block 0 @512 is
  identical across images that have different 16-31 fields). It's a
  per-image nonce or MAC; its purpose is unresolved by static analysis.

## Attacks ruled out (the "shorten the search space" sweep)

| Attack | Result |
| --- | --- |
| ECB / repeated blocks | ❌ 0 repeats (16 B and 8 B) |
| 64-bit block cipher (DES/3DES/Blowfish/TEA) | ❌ no 8 B repeats |
| Stream keystream reuse | ❌ XOR(2.5,2.7 payload) = 7.999 bits/byte |
| Weak KDF (key = hash of magic/model/const) | ❌ 94 candidates, all full-entropy |
| Fixed-block = RSA signature | ❌ no `0x010001` in any file, no DER, asn1parse fails |
| Fixed-block = AES-KW key-wrap | ❌ no `A6A6A6A6A6A6A6A6` |
| Fixed-block = cert / embedded key | ❌ no PEM/OID/strings |

Fixed-IV CBC is a genuine implementation misstep, but it only leaks shared
plaintext prefixes (which is all we observed). It does **not** reduce the
2^128 key search. Brute force is not viable.

## Hardware / key location

- **Cruz Rev113 MCU = Atmel AT91SAM9XE128-QU** (ARM926). Binky Rev64 = NXP
  LPC3143 + STM32F100. The AES key is **fused into the MCU** and decryption
  is on-chip; `NeatoUpgrader.exe` and NeatoControl only relay the `.enc` —
  the key is in **no host binary**. JTAG software-disabled; SAM-BA gated by
  the GPNVM security bit; no public flash dump of this chip.
- Firmware is **RSA-SHA256 signed** (`Signing.crt`); on the sibling Botvac
  line the robot does not validate the cert chain (self-signed accepted).
  Signing key never leaked. Signature location on the XV `.enc` is not the
  512–2080 region (see above) and remains unidentified.

## The one documented Neato memory extraction (blueprint, wrong chip)

**CVE-2018-20785** — Jiska Classen, *Botvac Connected*. USB-serial commands
at boot drop the **AM335x** into a boot menu accepting an unsigned XMODEM
QNX image → dumps all memory incl. decrypted firmware. Patched D7 4.4.0-72.
Targets AM335x, **not** the Atmel Cruz — the method (talk to the ROM/serial
bootloader before the app locks down) is the pattern to probe on the Atmel's
SAM-BA ROM, but the exploit itself does not transfer.

## Attack surface, ranked by effort/risk

1. **Passively capture P6 (115200 8N1) — cheap, non-destructive, do first —
   BUT it is not itself a key readout.** *Correction (2026-08-11 review, see
   `../captures/analysis/at91-baud-research.md`):* a healthy board's RomBOOT
   jumps straight to a valid image, so P6 at cold boot shows the **app boot log,
   not a SAM-BA `RomBOOT>` prompt.** There is **no pre-lock window** to catch at
   reset; SAM-BA read access only exists after the destructive GPNVM glitch
   (#3). P6 capture confirms the tap and the board — treat it as reconnaissance,
   not extraction.
2. **Analysis-only** — diff more `.enc` builds from the Vault corpus
   (largely done here).
3. **Voltage-glitch the GPNVM security bit** to re-enable JTAG and dump
   flash. Hard: Atmel debounced the ERASE line to resist glitching;
   ATSAM4C32 is the nearest public precedent.
- **Do NOT touch the J3/ERASE jumper — documented unrecoverable brick.**
- Once flash is dumped, offset-512 plaintext (word 0 = SP `0x2000xxxx`,
  word 1 = odd Thumb reset addr) confirms an ARM vector table.

## For the BMO project specifically

None of this blocks BMO. The sound library (`DfltSoundLib.Rev1.0.bin`)
ships **unencrypted** (`KT` magic + LE offset table), which is why the whole
BMO speech pipeline works without ever touching the envelope. The encrypted
app image stays a black box; we don't need it.

## Sources (OSINT)

- RECESSIM wiki — Neato XV-11 (teardown, CPU IDs, headers, bootloader cmds):
  `https://wiki.recessim.com/view/Neato_XV-11`
- USENIX WOOT '19 "Vacuums in the Cloud" (Classen/Wegemer):
  `https://www.usenix.org/sites/default/files/conference/protected-files/woot19_slides_classen.pdf`
- CVE-2018-20785: `https://nvd.nist.gov/vuln/detail/CVE-2018-20785`
- RobertSundling/neato-botvac (RSA-SHA256 signing, self-signed accepted):
  `https://github.com/RobertSundling/neato-botvac`
- NoahJaehnert Cruz Rev113 offline updater (no decryption; zip pw `VORVR100!%`):
  `https://github.com/NoahJaehnert/Neato-XV-Series-Cruz-Rev-113-Update`
- NeatoControl (heXor fork; flasher, no key):
  `https://codeberg.org/rimas/neatocontrol`
- RECESSIM — ATSAM4C32 GPNVM glitch precedent:
  `https://wiki.recessim.com/view/ATSAM4C32`
