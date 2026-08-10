"""Emoji -> LCD face cascades, ported 1:1 from esp32-body/src/faces.c.

One canonical face vocabulary for both the firmware and the USB path: the
rect tables and emoji map below mirror faces.c exactly (same coordinates,
same cascade timing), so a reply tested locally over USB looks identical
when the ESP32 draws it. If a face changes, change it here *and* in faces.c.

Usage:
    from neatobmo import emote
    emote.cascade(robot, "I love you! \U0001f60d\U0001f389")
"""
import time

CMD_GAP = 0.025          # pacing between SetLCD commands (CMD_GAP_MS)
MAX_CASCADE = 8

NEUTRAL, HAPPY, LAUGH, LOVE, SAD, SURPRISED, WINK, SLEEPY, ANGRY, PARTY, BLINK = (
    "neutral", "happy", "laugh", "love", "sad", "surprised",
    "wink", "sleepy", "angry", "party", "blink")


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


# ---- drawing (same segment strategy as faces.c: fewest commands wins) ----

def _cmd(r, c):
    r.cmd(c, timeout=0.6)
    time.sleep(CMD_GAP)


def _rect(r, box):
    x0, y0, x1, y1 = box
    if y1 - y0 <= x1 - x0:
        for row in range(y0, y1 + 1):
            _cmd(r, f"SetLCD HLine {row} {x0} {x1}")
    else:
        for col in range(x0, x1 + 1):
            _cmd(r, f"SetLCD VLine {col} {y0} {y1}")


def _rects(r, boxes):
    for b in boxes:
        _rect(r, b)


def _erase(r, boxes):
    _cmd(r, "SetLCD FGWhite")
    _rects(r, boxes)
    _cmd(r, "SetLCD FGBlack")


def draw_face(r, face):
    _cmd(r, "SetLCD BGWhite")
    _cmd(r, "SetLCD FGBlack")
    _rects(r, FACES.get(face, FACES[NEUTRAL]))


# ---- sprite animations (linger on the last emotion) ----------------------

def _anim_hearts(r, live):
    for step in range(14):
        if not live():
            return
        y = 40 - step * 3
        h = _heart(52, y)
        _rects(r, h)
        time.sleep(0.06)
        if y > 4:
            _erase(r, h)


def _anim_tear(r, live):
    for step in range(8):
        if not live():
            return
        t = [_rc(88, 30 + step * 3, 2, 4)]
        _rects(r, t)
        time.sleep(0.08)
        _erase(r, t)


def _anim_zzz(r, live):
    for step in range(10):
        if not live():
            return
        y = 34 - step * 3
        if y < 4:
            break
        z = [_rc(104, y, 8, 1), _rc(108, y + 2, 3, 1), _rc(104, y + 4, 8, 1)]
        _rects(r, z)
        time.sleep(0.12)
        _erase(r, z)


def _anim_confetti(r, live):
    for step in range(10):
        if not live():
            return
        c = [_rc(10 + (step * 37) % 100, 2 + (step * 13) % 12, 2, 2),
             _rc(20 + (step * 53) % 90, 2 + (step * 29) % 12, 2, 2)]
        _rects(r, c)
        time.sleep(0.09)
        _erase(r, c)


ANIMS = {LOVE: _anim_hearts, SAD: _anim_tear, SLEEPY: _anim_zzz,
         PARTY: _anim_confetti, LAUGH: _anim_confetti}

_generation = 0


def cascade(r, text):
    """Play the reply's emojis as a face cascade; newest cascade wins.

    Blocking (a few seconds) — run it on a worker thread; a later call
    makes any in-flight cascade stop at its next step, like the firmware's
    depth-1 overwrite queue.
    """
    global _generation
    _generation += 1
    gen = _generation
    live = lambda: _generation == gen

    seq = parse_emojis(text) or [HAPPY]   # plain text: just smile
    r.cmd("TestMode On")                  # SetLCD is TestMode-only
    time.sleep(0.1)
    for i, face in enumerate(seq):
        if not live():
            return len(seq)
        draw_face(r, face)
        time.sleep(0.65)
        if i < len(seq) - 1:
            draw_face(r, BLINK)
            time.sleep(0.12)
    anim = ANIMS.get(seq[-1])
    if anim and live():
        anim(r, live)
    return len(seq)
