"""Emoji -> LCD face cascades over USB, a true port of esp32-body/src/faces.c.

The firmware's SetLCD draws FULL-SPAN lines only: `HLine <row>` and
`VLine <col>` take a single number. Probed live (2026-08-09, fw 2.4.15667):
any extra trailing number is parsed as a *Contrast* value and written to
NAND — `SetLCD HLine 31 40 90` replied "Invalid Contrast Specified" and
`VLine 64 10 50` silently set LCDContrast to 50. FGWhite is a complete
no-op (ACKs, never draws or erases, on white or black backgrounds), so
there is no selective erase: faces are GRIDS — full-height black eye
pillars (VLine) crossed by full-width mouth bands (HLine), fully redrawn
each time. Grid geometry lives in neatobmo/faces.py, mirroring faces.c;
if a face changes, change faces.py *and* faces.c.

FACES and ANIMS below also feed the web console's animated LCD preview
(bmo_web /faces): FACES holds preview rects now derived from the same
grid geometry the robot draws, and the ANIMS function names
(hearts/tear/zzz/confetti) select the preview sprite — the one remaining
preview embellishment; on the robot those entries run full-span-native
lingering effects (contrast throb / fade / breathe / bars strobe),
matching faces.c, whose anims already accept that Contrast persists to
NAND.

Usage:
    from neatobmo import emote
    emote.cascade(robot, "I love you! \U0001f60d\U0001f389")
"""
import time

from .faces import FACES as GEO, face_ops

CMD_GAP = 0              # fw 2.4 processes SetLCD on a 10 Hz tick (measured
                         # 2026-08-09: 10.0 cmd/s at gaps 0-25 ms, zero drops)
                         # so the reply wait in _cmd already paces us; any
                         # extra sleep only slows cascades down
MAX_CASCADE = 8
CONTRAST_DEF = 45        # faces.c CONTRAST_DEF

NEUTRAL, HAPPY, LAUGH, LOVE, SAD, SURPRISED, WINK, SLEEPY, ANGRY, PARTY, BLINK = (
    "neutral", "happy", "laugh", "love", "sad", "surprised",
    "wink", "sleepy", "angry", "party", "blink")


# ---- preview rects for the web console LCD, derived from the real grid
#      geometry so the preview shows exactly what the robot draws. The old
#      hand-drawn sprite faces (dot eyes, heart eyes, smile arcs) are gone:
#      they were prettier than the hardware can render (deprecated
#      2026-08-10; see git history if the art is ever wanted again) --------

_LCD_W, _LCD_H = 128, 64    # bmo_web preview canvas dimensions


def _grid_rects(cols, rows):
    return ([(lo, 0, hi, _LCD_H - 1) for lo, hi in cols] +
            [(0, lo, _LCD_W - 1, hi) for lo, hi in rows])


FACES = {name: _grid_rects(cols, rows) for name, (cols, rows) in GEO.items()}

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


# ---- drawing (full grid redraw each time, same strategy as faces.c) ------

def _cmd(r, c):
    r.cmd(c, timeout=0.6)
    time.sleep(CMD_GAP)


def _lcd(r, op):
    _cmd(r, "SetLCD " + op)


_shown = None   # face name currently drawn on the LCD, or None


def draw_face(r, face):
    global _shown
    if face not in GEO:
        face = NEUTRAL
    for op in face_ops(face):
        _lcd(r, op)
    _shown = face


# ---- lingering animations (full-span native, mirroring faces.c; the
#      function names select the matching web-preview sprite) --------------

def _anim_hearts(r, live):      # love: heartbeat contrast throb
    for _ in range(4):
        if not live():
            return
        _lcd(r, "Contrast 60")
        time.sleep(0.18)
        _lcd(r, f"Contrast {CONTRAST_DEF}")
        time.sleep(0.42)


def _anim_tear(r, live):        # sad: world slowly fades, then recovers
    for c in range(CONTRAST_DEF, 19, -5):
        if not live():
            return
        _lcd(r, f"Contrast {c}")
        time.sleep(0.15)
    time.sleep(0.6)
    _lcd(r, f"Contrast {CONTRAST_DEF}")


def _anim_zzz(r, live):         # sleepy: breathing fade + lights out
    for _ in range(2):
        for c in range(CONTRAST_DEF, 14, -5):
            if not live():
                return
            _lcd(r, f"Contrast {c}")
            time.sleep(0.09)
        for c in range(15, CONTRAST_DEF + 1, 5):
            if not live():
                return
            _lcd(r, f"Contrast {c}")
            time.sleep(0.09)
    if live():
        _cmd(r, "SetLED BacklightOff")


def _anim_confetti(r, live):    # party/laugh: strobe bars, then the face
    global _shown
    for i in range(6):
        if not live():
            return
        _lcd(r, "BGWhite")
        _lcd(r, "FGBlack")
        _lcd(r, "HBars" if i & 1 else "VBars")
        time.sleep(0.22)
    last, _shown = _shown, None     # bars trashed the grid face
    if live() and last:
        draw_face(r, last)


ANIMS = {LOVE: _anim_hearts, SAD: _anim_tear, SLEEPY: _anim_zzz,
         PARTY: _anim_confetti, LAUGH: _anim_confetti}

_generation = 0


def cascade(r, text):
    """Play the reply's emojis as a face cascade; newest cascade wins.

    Blocking (a few seconds) — run it on a worker thread; a later call
    makes any in-flight cascade stop at its next step, like the firmware's
    depth-1 overwrite queue.
    """
    global _generation, _shown
    _generation += 1
    gen = _generation
    live = lambda: _generation == gen
    _shown = None    # anything may have touched the LCD since: full redraw

    seq = parse_emojis(text) or [HAPPY]   # plain text: just smile
    r.cmd("TestMode On")                  # SetLCD is TestMode-only
    time.sleep(0.1)
    _cmd(r, "SetLED BacklightOn")
    _lcd(r, f"Contrast {CONTRAST_DEF}")
    for i, face in enumerate(seq):
        if not live():
            return len(seq)
        draw_face(r, face)
        time.sleep(0.65)
        if i < len(seq) - 1:              # eyelid flash between faces
            _cmd(r, "SetLED BacklightOff")
            time.sleep(0.12)
            _cmd(r, "SetLED BacklightOn")
    anim = ANIMS.get(seq[-1])
    if anim and live():
        anim(r, live)
    return len(seq)
