"""Screen moods and carved faces for the XV-12 LCD.

The firmware only draws full-span lines, bars, and fills. Faces are
therefore *carved*: black eye pillars (VLine) masked down to row bands by
white rows (HLine), mouth = full-width black band. Later commands overwrite
earlier ones, which is what makes a nested wink possible. A mouth narrower
than the eye span is impossible with full-span primitives.

Mirrors esp32-body/src/faces.c — same geometry, same ordering — so faces
can be tested over the :3333 bridge without reflashing.
"""
import time

LCD = 128
EYE_L = range(32, 45)
EYE_R = range(84, 97)

# name -> (left-eye rows, right-eye rows, mouth rows); eyes may differ
# (wink) only when one band nests inside the other
FACES = {
    "neutral":   (range(20, 28), range(20, 28), range(44, 47)),
    "happy":     (range(20, 28), range(20, 28), range(42, 51)),
    "laugh":     (range(18, 29), range(18, 29), range(40, 53)),
    "love":      (range(18, 29), range(18, 29), range(42, 51)),
    "sad":       (range(20, 28), range(20, 28), range(50, 53)),
    "surprised": (range(16, 29), range(16, 29), range(38, 56)),
    "wink":      (range(20, 28), range(23, 26), range(42, 51)),
    "sleepy":    (range(24, 27), range(24, 27), range(48, 51)),
    "angry":     (range(22, 27), range(22, 27), range(50, 53)),
    "party":     (range(16, 29), range(16, 29), range(40, 53)),
    "blink":     (range(23, 26), range(23, 26), range(44, 47)),
}


def face_ops(name):
    """The SetLCD op sequence that carves a full face (~150 commands)."""
    el, er, mouth = FACES[name]
    ops = ["BGWhite", "FGBlack"]
    ops += [f"VLine {c}" for c in EYE_R]
    if el != er:
        ops += ["FGWhite"]
        ops += [f"HLine {y}" for y in el if y not in er]
        ops += ["FGBlack"]
    ops += [f"VLine {c}" for c in EYE_L]
    ops += ["FGWhite"]
    ops += [f"HLine {y}" for y in range(LCD) if y not in el and y not in mouth]
    ops += ["FGBlack"]
    ops += [f"HLine {y}" for y in mouth]
    return ops


def face(r, name):
    """Carve a full face (~150 SetLCD commands)."""
    r.lcd(*face_ops(name))


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
    r.lcd(f"Contrast 45", "BGWhite")
