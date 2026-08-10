"""Stage-cue protocol: best-effort "tool calling" for a small local brain.

OLMoE can't do real tool calls, so the persona prompt asks it to act with
inline stage cues — bracketed tokens like ``[happy] [wiggle] [sound:hello]``
sprinkled through the reply. This module parses them out best-effort:

  - any bracket style counts: [x] (x) {x} <x> *x* **x**
  - namespaced (``sound:hello``, ``face:sad``, ``move:wiggle``) or bare
  - bare names resolve fuzzily (``[laughs]`` -> laugh, ``[dancing]`` -> dance)
    so a sloppy model still lands on a real action
  - unmatched square-bracket / asterisk tokens are treated as stage chatter
    (``[thinks hard]``) and stripped from speech; unmatched parentheses are
    kept — people talk in parentheses
  - emojis remain a fallback face layer via emote.parse_emojis

parse() returns a Plan with three views of the reply:
  speech  — cue-free text for the TTS bank (emoji stripping happens later
            in tts_bank.sanitize_speech_text as before)
  display — face cues replaced by their emoji, other cues removed; feed this
            to the chat log, the web LCD preview, and emote_react so the
            existing ESP32/USB emoji cascade keeps working unchanged
  steps   — ordered (kind, name) actions; perform() runs the sound/move ones
"""
import difflib
import re
import time

from .faces import FACES as FACE_GEO
from .sounds import BMO_SOUND_SLOTS

# ---- vocabulary ----------------------------------------------------------

FACE_NAMES = [n for n in FACE_GEO if n != "blink"]

# face -> representative emoji, so display text drives the existing cascade
FACE_EMOJI = {
    "neutral": "\U0001F916", "happy": "\U0001F600", "laugh": "\U0001F602",
    "love": "\U0001F60D", "sad": "\U0001F622", "surprised": "\U0001F62E",
    "wink": "\U0001F609", "sleepy": "\U0001F634", "angry": "\U0001F620",
    "party": "\U0001F389",
}

# move cue -> behaviors function name (looked up lazily in perform)
MOVES = {
    "wiggle": "happy_wiggle",
    "dance": "dance",
    "spin": "spin_flourish",
    "look": "curious_look",
    "scared": "scared",
}

# sound cue -> Robot.play key, or "seq:" + BMO_SEQUENCES burst name
SOUND_CUES = {
    "hello": "hello",
    "yeah": "yeah_reaction",
    "bmotime": "bmo_time",
    "videogames": "seq:bmo_video_games_burst",
    "homeboys": "homeboys",
    "butt": "bmobutt",
    "json": "json_bmon",
    "beep": "short_reaction",
}

# sloppy synonyms a small model actually emits -> canonical bare cue
ALIASES = {
    "laughs": "laugh", "laughing": "laugh", "giggle": "laugh", "giggles": "laugh",
    "smile": "happy", "smiles": "happy", "grin": "happy", "yay": "happy",
    "heart": "love", "hearts": "love", "hugs": "love", "hug": "love",
    "cry": "sad", "cries": "sad", "crying": "sad", "tear": "sad",
    "wow": "surprised", "gasp": "surprised", "shocked": "surprised",
    "mad": "angry", "grr": "angry",
    "tired": "sleepy", "yawn": "sleepy", "zzz": "sleepy",
    "celebrate": "party", "confetti": "party",
    "wiggles": "wiggle", "wiggling": "wiggle", "shimmy": "wiggle",
    "dances": "dance", "dancing": "dance",
    "spins": "spin", "spinning": "spin", "twirl": "spin",
    "looks": "look", "looking": "look", "peek": "look",
    "bmobutt": "butt", "videogame": "videogames", "video": "videogames",
    "bmo time": "bmotime", "its bmotime": "bmotime",
}

_BARE = {}                          # normalized bare name -> (kind, canonical)
for _n in FACE_NAMES:
    _BARE[_n] = ("face", _n)
for _n in MOVES:
    _BARE[_n] = ("move", _n)
for _n in SOUND_CUES:
    _BARE.setdefault(_n, ("sound", _n))

MAX_FACES, MAX_MOVES, MAX_SOUNDS = 8, 2, 3

# [x] (x) {x} <x> *x* — short inner text only, cues never span lines
_TOKEN = re.compile(
    r"\[([^\[\]\n]{1,40})\]|\(([^()\n]{1,40})\)|\{([^{}\n]{1,40})\}"
    r"|<([^<>\n]{1,40})>|\*{1,2}([^*\n]{1,30})\*{1,2}")

_NAMESPACES = {
    "sound": "sound", "sfx": "sound", "play": "sound", "s": "sound",
    "face": "face", "draw": "face", "f": "face",
    "move": "move", "do": "move", "m": "move",
}


def _norm(s):
    s = re.sub(r"[_\-]+", " ", s.strip().lower())
    s = re.sub(r"[^a-z0-9 :]+", "", s)
    return re.sub(r"\s+", " ", s).strip()


def _resolve_bare(word, fuzzy=True):
    """(kind, name) for a bare cue word; None if it isn't one.

    fuzzy is for stage-direction brackets only — inside parentheses a near
    miss is far more likely a real word ("care" is not [scared]).
    """
    word = ALIASES.get(word, word)
    if word in _BARE:
        return _BARE[word]
    if not fuzzy:
        return None
    hit = difflib.get_close_matches(word, list(_BARE) + list(ALIASES), n=1,
                                    cutoff=0.8)
    if hit:
        w = ALIASES.get(hit[0], hit[0])
        return _BARE.get(w)
    return None


def _resolve(inner, stagey=False):
    """Resolve one token's inner text to a list of (kind, name) steps.

    stagey: the token came from [] or *..* — stage-direction territory —
    so a multi-word phrase may resolve by its words ("wiggles excitedly"
    -> wiggle). Never done for (), which often wraps real speech.
    """
    steps = []
    for part in re.split(r"[,;/&+]| and ", inner):
        part = _norm(part)
        if not part:
            continue
        if ":" in part:
            ns, _, name = part.partition(":")
            ns = _NAMESPACES.get(ns.strip())
            name = _norm(name)
            if ns == "sound":
                name = ALIASES.get(name, name)
                if name not in SOUND_CUES:
                    hit = difflib.get_close_matches(name, SOUND_CUES, 1, cutoff=0.7)
                    name = hit[0] if hit else None
                if name:
                    steps.append(("sound", name))
            elif ns == "face" and (r := _resolve_bare(name)) and r[0] == "face":
                steps.append(r)
            elif ns == "move" and (r := _resolve_bare(name)) and r[0] == "move":
                steps.append(r)
        else:
            r = _resolve_bare(part, fuzzy=stagey)
            if r:
                steps.append(r)
            elif stagey and " " in part:
                for word in part.split():
                    if r := _resolve_bare(word):
                        steps.append(r)
                        break
    return steps


class Plan:
    def __init__(self, speech, display, steps):
        self.speech = speech
        self.display = display
        self.steps = steps

    def actions(self):
        """The sound/move steps perform() runs (faces ride the emoji path)."""
        return [s for s in self.steps if s[0] != "face"]


def parse(text):
    """Extract stage cues from a brain reply, best-effort."""
    steps = []
    counts = {"face": 0, "move": 0, "sound": 0}
    caps = {"face": MAX_FACES, "move": MAX_MOVES, "sound": MAX_SOUNDS}

    def replace(m):
        inner = next(g for g in m.groups() if g is not None)
        stagey = m.group(1) is not None or m.group(5) is not None
        resolved = _resolve(inner, stagey)
        if resolved:
            out = []
            for kind, name in resolved:
                if counts[kind] >= caps[kind]:
                    continue
                counts[kind] += 1
                steps.append((kind, name))
                if kind == "face":
                    out.append(FACE_EMOJI.get(name, ""))
            return "".join(out) or " "
        # unresolved: square brackets / asterisks are stage chatter — drop;
        # parentheses (and <>, {}) may be real speech — keep them verbatim
        return " " if m.group(1) is not None or m.group(5) is not None else m.group(0)

    display = _TOKEN.sub(replace, text)
    display = re.sub(r" {2,}", " ", display).strip()
    # speech = display minus the face emojis we just planted (tts_bank strips
    # emoji anyway, but keeping speech clean makes it testable on its own)
    speech = display
    for e in FACE_EMOJI.values():
        speech = speech.replace(e, " ")
    speech = re.sub(r"\s+([.,!?;:])", r"\1", re.sub(r" {2,}", " ", speech)).strip()

    # fallback: no face cues but the model used emojis — count those as faces
    if counts["face"] == 0:
        from .emote import parse_emojis
        steps.extend(("face", f) for f in parse_emojis(display))
    return Plan(speech, display, steps)


def perform(r, steps, gap=0.4):
    """Run the sound/move steps in order on the robot (blocking; call via a
    worker holding rlock, same contract as behaviors)."""
    from . import behaviors
    from .sounds import BMO_SEQUENCES
    for kind, name in steps:
        if kind == "sound":
            key = SOUND_CUES.get(name, name)
            if key.startswith("seq:"):
                for sid in BMO_SEQUENCES[key[4:]]["ids"]:
                    r.play(sid)
                    time.sleep(BMO_SOUND_SLOTS[sid]["slot_seconds"])
            else:
                r.play(key)
                slot = next((s for s in BMO_SOUND_SLOTS.values()
                             if s["key"] == key), None)
                time.sleep(slot["slot_seconds"] if slot else 1.0)
        elif kind == "move":
            getattr(behaviors, MOVES[name])(r)
        time.sleep(gap)
