"""Real BMO faces on the XV LCD, built from HLine segments.

Confirmed: `SetLCD HLine <row> <col0> <col1>` draws a horizontal *segment*
(not a full-width line), and VLine likewise. So we can draw filled shapes by
stacking segments -> eyes, mouths, expressions. Screen assumed 128x64
(cols 0..127, rows 0..63); adjust W/H if probing says otherwise.
"""
import time

W, H = 128, 64


def _seg(r, row, c0, c1):
    r.cmd(f"SetLCD HLine {int(row)} {int(c0)} {int(c1)}", timeout=0.6)


def _rect(r, x0, y0, x1, y1):
    """Filled box via stacked horizontal segments."""
    for row in range(int(y0), int(y1) + 1):
        _seg(r, row, x0, x1)


def clear(r):
    r.cmd("SetLCD BGWhite"); r.cmd("SetLCD FGBlack")


def _eyes(r, kind="open"):
    # left eye cols 24-44, right eye cols 84-104
    if kind == "open":
        _rect(r, 24, 14, 44, 34)
        _rect(r, 84, 14, 104, 34)
    elif kind == "blink":  # thin closed lines
        _rect(r, 24, 26, 44, 29)
        _rect(r, 84, 26, 104, 29)
    elif kind == "happy":  # squished (^ ^) — lower half only
        _rect(r, 24, 24, 44, 34)
        _rect(r, 84, 24, 104, 34)
    elif kind == "wide":   # surprised, taller
        _rect(r, 24, 10, 44, 38)
        _rect(r, 84, 10, 104, 38)


def neutral(r):
    clear(r); _eyes(r, "open"); _rect(r, 44, 48, 84, 51)          # flat mouth


def happy(r):
    clear(r); _eyes(r, "happy")
    # open smile: a rounded block
    _rect(r, 40, 46, 88, 54)
    _seg(r, 45, 36, 40); _seg(r, 45, 88, 92)                       # upturned corners


def surprised(r):
    clear(r); _eyes(r, "wide"); _rect(r, 54, 46, 74, 56)           # small O mouth


def sad(r):
    clear(r); _eyes(r, "open")
    _rect(r, 44, 52, 84, 55)                                       # low mouth
    _seg(r, 49, 40, 46); _seg(r, 49, 82, 88)                       # downturned corners


def blink(r, times=2, dt=0.12):
    for _ in range(times):
        clear(r); _eyes(r, "blink"); _rect(r, 44, 48, 84, 51)
        time.sleep(dt)
        neutral(r)
        time.sleep(dt * 1.5)


def look(r, direction="center"):
    """Pupils shifted by drawing eyes offset (simple version: whole eye moves)."""
    clear(r)
    dx = {"left": -12, "right": 12, "center": 0}[direction]
    _rect(r, 24 + dx, 14, 44 + dx, 34)
    _rect(r, 84 + dx, 14, 104 + dx, 34)
    _rect(r, 44, 48, 84, 51)
