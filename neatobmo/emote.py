"""Emoji -> LCD face cascades over USB, a true port of esp32-body/src/faces.c.

The firmware's SetLCD draws FULL-SPAN lines only: `HLine <row>` and
`VLine <col>` take a single number. Probed live (2026-08-09, fw 2.4.15667):
any extra trailing number is parsed as a *Contrast* value and written to
NAND — `SetLCD HLine 31 40 90` replied "Invalid Contrast Specified" and
`VLine 64 10 50` silently set LCDContrast to 50. So there is no segment
grammar, and faces are carved exactly like faces.c: black eye pillars
(VLine) masked down to row bands by white rows (HLine), mouth = full-width
band. Band geometry lives in neatobmo/faces.py, mirroring faces.c; if a
face changes, change faces.py *and* faces.c.

FACES and ANIMS below also feed the web console's animated LCD preview
(bmo_web /faces): FACES holds the stylized preview rects, and the ANIMS
function names (hearts/tear/zzz/confetti) select the preview sprite. On
the robot those same entries run full-span-native lingering effects —
contrast throb / fade / breathe / bars strobe — matching faces.c, whose
anims already accept that Contrast persists to NAND.

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


# ---- stylized preview rects for the web console LCD (not what the robot
#      draws — the robot carves the GEO bands; these are the same faces as
#      8x8-ish sprite art for bmo_web's canvas preview) --------------------

def _rc(x, y, w, h):
    return (x, y, x + w - 1, y + h - 1)


EYES_DOT = [_rc(34, 20, 8, 8), _rc(86, 20, 8, 8)]
EYES_LINE = [_rc(32, 23, 12, 3), _rc(84, 23, 12, 3)]
EYES_WIDE = [_rc(33, 18, 10, 10), _rc(85, 18, 10, 10)]
EYES_HAT = [_rc(32, 24, 12, 3), _rc(34, 21, 8, 3),
            _rc(84, 24, 12, 3), _rc(86, 21, 8, 3)]
EYES_LID = [_rc(32, 18, 12, 3), _rc(33, 22, 10, 6),
            _rc(84, 18, 12, 3), _rc(85, 22, 10, 6)]
EYES_BROW = [_rc(32, 16, 12, 3), _rc(34, 21, 8, 8),
             _rc(84, 16, 12, 3), _rc(86, 21, 8, 8)]


def _heart(x, y):
    return [_rc(x + 1, y, 3, 2), _rc(x + 6, y, 3, 2), _rc(x, y + 2, 10, 3),
            _rc(x + 2, y + 5, 6, 2), _rc(x + 4, y + 7, 2, 1)]


EYES_HEART = _heart(31, 17) + _heart(83, 17)

M_FLAT = [_rc(52, 44, 25, 3)]
M_SMILE = [_rc(44, 44, 41, 6), _rc(48, 50, 33, 3)]
M_GRIN = [_rc(44, 42, 41, 10), _rc(50, 52, 29, 3)]
M_SAD = [_rc(52, 50, 25, 3), _rc(46, 46, 6, 2), _rc(77, 46, 6, 2)]
M_O = [_rc(56, 42, 17, 13)]
M_SLEEP = [_rc(56, 48, 17, 3)]

FACES = {
    HAPPY:     EYES_HAT + M_SMILE,
    LAUGH:     EYES_HAT + M_GRIN,
    LOVE:      EYES_HEART + M_SMILE,
    SAD:       EYES_DOT + M_SAD,
    SURPRISED: EYES_WIDE + M_O,
    WINK:      [EYES_DOT[0], EYES_LINE[1]] + M_SMILE,
    SLEEPY:    EYES_LID + M_SLEEP,
    ANGRY:     EYES_BROW + M_SAD,
    PARTY:     EYES_WIDE + M_GRIN,
    BLINK:     EYES_LINE + M_FLAT,
    NEUTRAL:   EYES_DOT + M_FLAT,
}

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


# ---- drawing (carve + nested-band delta, same strategy as faces.c) -------

def _cmd(r, c):
    r.cmd(c, timeout=0.6)
    time.sleep(CMD_GAP)


def _lcd(r, op):
    _cmd(r, "SetLCD " + op)


_shown = None   # face name currently carved on the LCD, or None


def _delta_ok(name):
    """Delta redraw is legal only when the new eye bands nest inside the
    current ones (rows can be whited out per-row, but a row can never be
    turned into "black at eye columns only" without redrawing pillars)."""
    if _shown is None:
        return False
    (cel, cer, _), (el, er, _) = GEO[_shown], GEO[name]
    return (el[0] >= cel[0] and el[-1] <= cel[-1] and
            er[0] >= cer[0] and er[-1] <= cer[-1])


def _delta_ops(name):
    (cel, cer, cm), (el, er, m) = GEO[_shown], GEO[name]
    ops = ["FGWhite"]
    ops += [f"HLine {y}" for y in cel if y not in el and y not in m]
    ops += [f"HLine {y}" for y in cer
            if y not in er and y not in m and y not in cel]
    ops += [f"HLine {y}" for y in cm if y not in m]
    ops += ["FGBlack"]
    ops += [f"HLine {y}" for y in m if y not in cm]
    return ops


def draw_face(r, face):
    global _shown
    if face not in GEO:
        face = NEUTRAL
    ops = _delta_ops(face) if _delta_ok(face) else face_ops(face)
    for op in ops:
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
    last, _shown = _shown, None     # bars trashed the carve
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
    _shown = None    # anything may have touched the LCD since: full carve

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
        if i < len(seq) - 1:
            draw_face(r, BLINK)           # nested rows: cheap delta
            time.sleep(0.12)
    anim = ANIMS.get(seq[-1])
    if anim and live():
        anim(r, live)
    return len(seq)
