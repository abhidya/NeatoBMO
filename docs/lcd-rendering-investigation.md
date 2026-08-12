# LCD rendering investigation

## What is proved

The stock USB API exposes drawing primitives only. `Help SetLCD` lists
`BGWhite`, `BGBlack`, `FGWhite`, `FGBlack`, `HLine`, `VLine`, `HBars`,
`VBars`, and `Contrast`. It exposes no `GetLCD` or framebuffer readback.

Live probing (2026-08-09/10, fw 2.4.15667, photos verified) established:

- `HLine <row>` / `VLine <col>` take exactly one number and draw a 1px
  BLACK line spanning the full screen. There is no segment/rect grammar.
- Any extra trailing number on a `SetLCD` command is parsed as a
  **Contrast** value and written to NAND (`SetLCD VLine 64 10 50`
  silently set `LCDContrast=50`).
- `FGWhite` is a complete no-op: it ACKs but never draws or erases, on
  both white and black backgrounds. There is no selective erase; the
  only way to remove ink is a `BGWhite`/`BGBlack` full-screen fill.
- The drawable language is therefore unions of full-height black columns
  and full-width black rows on white. Arbitrary bitmaps, carved/nested
  bands, hollow shapes, text, and diagonals are impossible.
- The firmware processes `SetLCD` on a 10 Hz tick: exactly 10 commands/s
  at any send rate, zero drops, commands queue. A full grid-face redraw
  (~20-40 commands) takes ~2-4 s.
- Contrast range is 0-63, persisted to NAND; this robot's default is 45.

Post-application-upgrade observation (2026-08-11): after BACK selected the
surviving factory 2.4.15667 application, the operator reported that the LCD had
remained white for an extended period. The USB console then accepted the
project's 29-operation `happy` face (`BGWhite`, `FGBlack`, full-span eye columns
and mouth rows), followed by `TestMode Off`. No `Contrast` or upload command was
sent, so this was a transient display test with no intended NAND write. The USB
acceptance record is `../captures/20260811_D01_factory_lcd_happy_result.json`.
The sequence was repeated a second time; every USB command was again accepted,
but the operator observed no visible change and the LCD remained white. The
cause is unresolved: the LCD/connection may be damaged, or the stripped bench
configuration with most robot components disconnected may prevent the expected
rendering path. Do not treat command echo as proof that pixels changed.

The project renderer likewise sends primitives through `SetLCD`; it does not
hold a host-side copy of an LCD framebuffer. This does not prove whether the
robot application owns a RAM framebuffer or writes directly to the LCD
controller's internal memory.

## Safe probes

Use `TestMode On`, then only these valid forms:

1. `BGWhite`, `BGBlack` — full-screen fills.
2. `HLine <row>`, `VLine <column>` — one numeric coordinate only.
3. `HBars`, `VBars` — deterministic display patterns.
4. `FGWhite` / `FGBlack` — foreground behavior (`FGWhite` is a verified
   no-op; it is safe but does nothing).
5. End with `TestMode Off`.

Photograph the display and, if hardware access is available, passively capture
the LCD bus while issuing each pattern. A display-sized repeated transfer
would support a software-framebuffer model; short command/data transactions
would support direct controller drawing.

## Do not use in a zero-persistence probe

- `SetLCD Contrast <n>`: firmware help says it writes contrast into NAND.
- Extra numeric arguments after `HLine` or `VLine`: verified — the parser
  treats the trailing number as a contrast value and writes it to NAND.
- Any Upload, erase, system-mode, or motor command as part of LCD probing.

## What is needed for native text

Text does not require a readable framebuffer. Once the LCD bus/controller or
the application draw function is identified, a small bitmap font can be sent
as pixels/lines through the existing rendering path. Identifying the exact
function/address needs plaintext firmware, passive LCD-bus capture, or a
non-erasing debug-memory route.

The reported GVLCM128128G/ST7541-I2C identification is adjacent XV-11
community evidence only; confirm the actual XV-12 display module before using
it as a hardware assumption.
