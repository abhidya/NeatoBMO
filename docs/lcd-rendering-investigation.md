# LCD rendering investigation

## What is proved

The stock USB API exposes drawing primitives only. `Help SetLCD` lists
`BGWhite`, `BGBlack`, `FGWhite`, `FGBlack`, `HLine`, `VLine`, `HBars`,
`VBars`, and `Contrast`. It exposes no `GetLCD` or framebuffer readback.

The project renderer likewise sends primitives through `SetLCD`; it does not
hold a host-side copy of an LCD framebuffer. This does not prove whether the
robot application owns a RAM framebuffer or writes directly to the LCD
controller's internal memory.

## Safe probes

Use `TestMode On`, then only these valid forms:

1. `BGWhite`, `BGBlack` — full-screen fills.
2. `HLine <row>`, `VLine <column>` — one numeric coordinate only.
3. `HBars`, `VBars` — deterministic display patterns.
4. `FGWhite` / `FGBlack` — foreground behavior.
5. End with `TestMode Off`.

Photograph the display and, if hardware access is available, passively capture
the LCD bus while issuing each pattern. A display-sized repeated transfer
would support a software-framebuffer model; short command/data transactions
would support direct controller drawing.

## Do not use in a zero-persistence probe

- `SetLCD Contrast <n>`: firmware help says it writes contrast into NAND.
- Extra numeric arguments after `HLine` or `VLine`: parser behavior can be
  ambiguous and may be treated as a contrast value.
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
