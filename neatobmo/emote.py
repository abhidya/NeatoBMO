"""The cascade player: emoji -> LCD face cascades over USB.

A true port of esp32-body/src/faces.c's player. All face DATA — grid
geometry, the emoji map, parse_emojis, preview rects — lives in
neatobmo/faces.py (the single source of truth; the firmware's tables are
generated from it). This module owns only drawing and the lingering
animations.

The firmware's SetLCD draws FULL-SPAN lines only: `HLine <row>` and
`VLine <col>` take a single number. Probed live (2026-08-09, fw 2.4.15667):
any extra trailing number is parsed as a *Contrast* value and written to
NAND — `SetLCD HLine 31 40 90` replied "Invalid Contrast Specified" and
`VLine 64 10 50` silently set LCDContrast to 50. FGWhite is a complete
no-op, so there is no selective erase: every face is a full redraw.

Usage:
    from neatobmo import emote
    emote.cascade(robot, "I love you! \U0001f60d\U0001f389")
"""
import time

from .faces import (FACES as GEO, PREVIEW_RECTS, EMOJI_FACES, MAX_CASCADE,
                    face_ops, parse_emojis,
                    NEUTRAL, HAPPY, LAUGH, LOVE, SAD, SURPRISED, WINK,
                    SLEEPY, ANGRY, PARTY, BLINK)

# back-compat: emote.FACES has always been the preview-rect view
FACES = PREVIEW_RECTS

CMD_GAP = 0              # fw 2.4 processes SetLCD on a 10 Hz tick (measured
                         # 2026-08-09: 10.0 cmd/s at gaps 0-25 ms, zero drops)
                         # so the reply wait in _cmd already paces us; any
                         # extra sleep only slows cascades down
CONTRAST_DEF = 45        # faces.c CONTRAST_DEF


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
