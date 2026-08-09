"""Screen moods for the XV-12 LCD.

The firmware only draws full-span lines, bars, and fills — so these are
abstract "mood screens" and animations, not drawn faces: blinks, breathing
contrast fades, excitement flickers. Synced with sounds/LEDs by behaviors.
"""
import time


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
