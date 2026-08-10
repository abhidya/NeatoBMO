"""Grid faces and screen moods for the XV-12 LCD.

Hardware-verified SetLCD reality (probed 2026-08-09 on the live robot):
  - HLine <row> / VLine <col> draw a 1px BLACK line spanning the full screen.
  - FGWhite is a complete no-op: it ACKs but draws/erases nothing, on both
    white and black backgrounds. There is no selective erase.
  - BGWhite / BGBlack fill the whole screen; the only way to remove ink.
  - Commands ACK in ~100 ms over the ESP32 bridge.

So the drawable language is: a union of full-height black columns and
full-width black rows on white. Faces are grids — eye "pillars" (column
groups) crossed by mouth "bands" (row groups); expression comes from
widths, thicknesses, and asymmetry. Any change needs a full redraw
(BGWhite + ~20-40 commands, a couple of seconds).

Mirrors esp32-body/src/faces.c so faces can be tested over :3333 without
reflashing.
"""
import time

LCD = 128

# name -> (column spans, row spans); each span is (lo, hi) inclusive
FACES = {
    "neutral":   ([(34, 42), (86, 94)], [(44, 46)]),
    "happy":     ([(34, 42), (86, 94)], [(42, 50)]),
    "laugh":     ([(32, 44), (84, 96)], [(40, 52)]),
    "love":      ([(32, 44), (84, 96)], [(42, 50)]),
    "sad":       ([(34, 42), (86, 94)], [(52, 54)]),
    "surprised": ([(30, 46), (82, 98)], [(36, 55)]),
    "wink":      ([(32, 44), (89, 91)], [(42, 50)]),
    "sleepy":    ([(37, 39), (89, 91)], [(48, 50)]),
    "angry":     ([(32, 44), (84, 96)], [(48, 50), (54, 56)]),
    "party":     ([(30, 46), (82, 98)], [(40, 42), (46, 52)]),
    "blink":     ([(37, 39), (89, 91)], [(44, 46)]),
}


def face_ops(name):
    """The SetLCD op sequence for a full grid-face redraw."""
    cols, rows = FACES[name]
    ops = ["BGWhite", "FGBlack"]
    for lo, hi in cols:
        ops += [f"VLine {c}" for c in range(lo, hi + 1)]
    for lo, hi in rows:
        ops += [f"HLine {y}" for y in range(lo, hi + 1)]
    return ops


def face(r, name, contrast=45):
    """Redraw the screen as a grid face (~20-40 SetLCD commands)."""
    r.lcd(f"Contrast {contrast}", *face_ops(name))


def clear(r, white=True):
    r.lcd("BGWhite" if white else "BGBlack")


def blink(r, times=2, speed=0.15):
    """Quick dark blinks over a light screen."""
    for _ in range(times):
        r.lcd("BGBlack")
        time.sleep(speed)
        r.lcd("BGWhite")
        time.sleep(speed * 2)


def breathe(r, cycles=2, lo=15, hi=50, step=5, dwell=0.08):
    """Slow contrast fade in/out — sleepy/calm breathing."""
    for _ in range(cycles):
        for c in range(hi, lo - 1, -step):
            r.lcd(f"Contrast {c}")
            time.sleep(dwell)
        for c in range(lo, hi + 1, step):
            r.lcd(f"Contrast {c}")
            time.sleep(dwell)


def excited(r, seconds=2.0):
    """Fast alternating bar patterns — party screen."""
    t0 = time.time()
    flip = False
    while time.time() - t0 < seconds:
        r.lcd("BGWhite", "FGBlack", "HBars" if flip else "VBars")
        flip = not flip
        time.sleep(0.25)
    r.lcd("BGWhite")


def scanline(r, rows=range(10, 120, 12), dwell=0.12):
    """A line sweeping down the screen — thinking/scanning."""
    for row in rows:
        r.lcd("BGWhite", "FGBlack", f"HLine {row}")
        time.sleep(dwell)
    r.lcd("BGWhite")


def sleepy(r):
    r.lcd("BGWhite", "FGBlack", "HBars")
    breathe(r, cycles=2)
    r.lcd("BGBlack")


def wake(r):
    r.lcd("BGBlack")
    time.sleep(0.3)
    blink(r, times=3, speed=0.1)
    r.lcd("Contrast 45", "BGWhite")
