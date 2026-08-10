"""The face vocabulary: grid geometry, emoji map, parser, preview rects.

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

This module is the single source of truth for the tables. The firmware's
copy (esp32-body/src/faces_table.h) is GENERATED from here by
tools/gen_faces_table.py — regenerate after editing FACES or EMOJI_FACES;
tests/test_emote.py asserts the generated header is in sync. The cascade
player that draws these over USB lives in neatobmo/emote.py.
"""
import time

LCD = 128
MAX_CASCADE = 8

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

NEUTRAL, HAPPY, LAUGH, LOVE, SAD, SURPRISED, WINK, SLEEPY, ANGRY, PARTY, BLINK = (
    "neutral", "happy", "laugh", "love", "sad", "surprised",
    "wink", "sleepy", "angry", "party", "blink")

EMOJI_FACES = {
    "\U0001F600": HAPPY,      # 😀
    "\U0001F604": HAPPY,      # 😄
    "\U0001F60A": HAPPY,      # 😊
    "\U0001F642": HAPPY,      # 🙂
    "\U0001F602": LAUGH,      # 😂
    "\U0001F923": LAUGH,      # 🤣
    "\U0001F60D": LOVE,       # 😍
    "❤":     LOVE,       # ❤ (with or without VS16)
    "\U0001F496": LOVE,       # 💖
    "\U0001F49A": LOVE,       # 💚
    "\U0001F622": SAD,        # 😢
    "\U0001F62D": SAD,        # 😭
    "\U0001F61E": SAD,        # 😞
    "☹":     SAD,        # ☹
    "\U0001F62E": SURPRISED,  # 😮
    "\U0001F632": SURPRISED,  # 😲
    "\U0001F631": SURPRISED,  # 😱
    "\U0001F609": WINK,       # 😉
    "\U0001F634": SLEEPY,     # 😴
    "\U0001F4A4": SLEEPY,     # 💤
    "\U0001F620": ANGRY,      # 😠
    "\U0001F624": ANGRY,      # 😤
    "\U0001F389": PARTY,      # 🎉
    "\U0001F3AE": PARTY,      # 🎮
    "✨":     PARTY,      # ✨
    "\U0001F916": NEUTRAL,    # 🤖
}


def parse_emojis(text, cap=MAX_CASCADE):
    """The face sequence a reply plays, in order of appearance."""
    out = []
    i = 0
    while i < len(text) and len(out) < cap:
        for key, face in EMOJI_FACES.items():
            if text.startswith(key, i):
                out.append(face)
                i += len(key)
                break
        else:
            i += 1
    return out


def face_ops(name):
    """The SetLCD op sequence for a full grid-face redraw."""
    cols, rows = FACES[name]
    ops = ["BGWhite", "FGBlack"]
    for lo, hi in cols:
        ops += [f"VLine {c}" for c in range(lo, hi + 1)]
    for lo, hi in rows:
        ops += [f"HLine {y}" for y in range(lo, hi + 1)]
    return ops


# ---- preview rects for the web console LCD, derived from the real grid
#      geometry so the preview shows exactly what the robot draws ----------

_LCD_H = 64     # bmo_web preview canvas dimensions (width is LCD)


def _grid_rects(cols, rows):
    return ([(lo, 0, hi, _LCD_H - 1) for lo, hi in cols] +
            [(0, lo, LCD - 1, hi) for lo, hi in rows])


PREVIEW_RECTS = {name: _grid_rects(cols, rows)
                 for name, (cols, rows) in FACES.items()}


# ---- screen moods used by the orchestrator ------------------------------

def blink(r, times=2, speed=0.15):
    """Quick dark blinks over a light screen."""
    for _ in range(times):
        r.lcd("BGBlack")
        time.sleep(speed)
        r.lcd("BGWhite")
        time.sleep(speed * 2)


def scanline(r, rows=range(10, 120, 12), dwell=0.12):
    """A line sweeping down the screen — thinking/scanning."""
    for row in rows:
        r.lcd("BGWhite", "FGBlack", f"HLine {row}")
        time.sleep(dwell)
    r.lcd("BGWhite")


def cascade(r, text):
    """Emoji/cue text -> LCD face cascade (delegates to the player)."""
    from . import emote
    return emote.cascade(r, text)
