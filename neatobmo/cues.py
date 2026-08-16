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

# sound-namespace inventions -> real slots. The model freely makes up sound
# names; every one observed by tools/cue_profile.py maps to the closest
# installed clip (json_bmon IS the surprised reaction, yeah covers cheers).
SOUND_ALIASES = {
    "welcome": "hello", "greeting": "hello",
    "love": "homeboys", "friend": "homeboys",
    "party": "yeah", "cheer": "yeah", "laughter": "yeah", "laugh": "yeah",
    "happy": "yeah", "excited": "yeah", "wiggle": "yeah",
    "music": "videogames", "song": "videogames", "sing": "videogames",
    "game": "videogames", "dance": "bmotime",
    "surprise": "json", "surprised": "json", "wow": "json",
    "spin": "beep", "alert": "beep", "sad": "beep", "scared": "beep",
}

# sloppy synonyms a small model actually emits -> canonical bare cue
ALIASES = {
    "laughs": "laugh", "laughing": "laugh", "giggle": "laugh", "giggles": "laugh",
    "smile": "happy", "smiles": "happy", "grin": "happy", "yay": "happy",
    "heart": "love", "hearts": "love", "hugs": "love", "hug": "love",
    "cry": "sad", "cries": "sad", "crying": "sad", "tear": "sad",
    "wow": "surprised", "gasp": "surprised", "shocked": "surprised",
    "startled": "surprised",
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
                name = SOUND_ALIASES.get(name, ALIASES.get(name, name))
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


# face -> the soundboard clip that carries the same feeling, used when a
# performance needs a voice but the model gave none
FACE_SOUND = {
    "happy": "yeah", "laugh": "yeah", "love": "homeboys", "sad": "beep",
    "surprised": "json", "party": "videogames", "sleepy": "beep",
    "angry": "beep", "wink": "butt", "neutral": "hello",
}


# Soundbyte-first speech budget. Precedent (Anki Cozmo/Vector; the HRI
# semantic-free-utterance literature) says expressive sounds should sit
# ALONGSIDE a few words, not replace them, and that fluent speech inflates
# expectations of intelligence the small brain can't meet. The installed BMO
# clips run 0.3-2.9 s, so spoken bursts are budgeted by DURATION to blend
# into the same performance: at espeak -s160 (~2.7 words/s), the default
# 1.5 s burst is 4 words.
SPEECH_WORDS_PER_SECOND = 2.7
SPEECH_BURST_SECONDS = 1.5


def _trim_to_budget(text, max_words):
    """First max_words words of text, preferring whole sentences.

    ("Hello!" beats "Hello! Did BMO miss"); falls back to a hard cut —
    finished with a '!' — only when the first sentence alone is over
    budget.  Returns text unchanged when it already fits.
    """
    words = text.split()
    if len(words) <= max_words:
        return text
    kept, count = [], 0
    for sent in re.split(r"(?<=[.!?])\s+", text):
        n = len(sent.split())
        if count + n > max_words:
            break
        kept.append(sent)
        count += n
    if kept:
        return " ".join(kept)
    cut = " ".join(words[:max_words]).rstrip(",;:.")
    if cut and cut[-1] not in "!?":
        cut += "!"
    return cut


class BurstBudget:
    """The one owner of "how much BMO says" in soundbyte mode.

    Both reply paths use it: condense() feeds it the whole reply at once,
    the streaming /chat loop feeds it sentence by sentence.  Same setting,
    same rules — including the "always leave with a soundbyte" guarantee
    that the old inline streaming copy silently lacked.

    feed(speech) -> the admitted (possibly trimmed) text, or None when the
    sentence doesn't fit the remaining budget.  fallback_sound(...) names
    the soundboard clip to add when no sound cue was heard.
    """

    def __init__(self, burst_seconds=SPEECH_BURST_SECONDS, max_words=None):
        self.max_words = (max_words if max_words is not None
                          else max(1, round(SPEECH_WORDS_PER_SECOND
                                            * burst_seconds)))
        self.words = 0

    def feed(self, speech):
        words = speech.split()
        if not words:
            return None
        if self.words and self.words + len(words) > self.max_words:
            return None
        if len(words) > self.max_words:
            speech = _trim_to_budget(speech, self.max_words)
            words = speech.split()
        self.words += len(words)
        return speech

    @staticmethod
    def fallback_sound(steps, speech=""):
        """The soundboard clip carrying the reply's feeling, when the model
        gave no sound cue of its own; None when one was already heard."""
        if any(k == "sound" for k, _ in steps):
            return None
        face = (next((n for k, n in steps if k == "face"), None)
                or _mood_face(speech) or "happy")
        return FACE_SOUND.get(face, "beep")


def condense(plan, max_words=None, burst_seconds=SPEECH_BURST_SECONDS,
             prefer_soundboard=False):
    """Soundbyte-first mode: cap the spoken burst, let cues do the talking.

    The spoken reply is trimmed to its first ``max_words`` words — by
    default a ``burst_seconds`` speech burst at the espeak rate — finished
    with a '!' when the cut lands mid-sentence. The full text stays in
    ``display`` so the chat log reads like subtitles. If the model gave no
    soundbyte, one is chosen from the reply's leading (or mood-guessed)
    face so the performance always has a voice.
    """
    budget = BurstBudget(burst_seconds=burst_seconds, max_words=max_words)
    explicit_sound = any(kind == "sound" for kind, _ in plan.steps)
    speech = "" if prefer_soundboard and explicit_sound else budget.feed(plan.speech)
    if speech is None:
        speech = plan.speech
    steps = list(plan.steps)
    sound = BurstBudget.fallback_sound(steps, plan.speech)
    if sound:
        steps.append(("sound", sound))
    return Plan(speech, plan.display, steps)


def parse(text):
    """Extract stage cues from a brain reply, best-effort."""
    # MAX_NEW truncation can shear a trailing cue in half ("... [sound");
    # an unterminated bracket at the end is never speech — drop it
    text = re.sub(r"[\[<{(][^\[\]<>{}()]*$", "", text).rstrip()
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
                if kind == "face" and steps and steps[-1] == (kind, name):
                    continue        # model stutter: [sleepy] [sleepy]
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
    # cue removal can butt punctuation together ("[party]!" -> "Hello!!",
    # "[sad]." -> "that,."): keep the first mark of any run
    speech = re.sub(r"([.,!?;:])[.,!?;:]+", r"\1", speech)

    # fallback: no face cues but the model used emojis — count those as faces
    if counts["face"] == 0:
        from .faces import parse_emojis
        emoji_faces = parse_emojis(display)
        steps.extend(("face", f) for f in emoji_faces)
        # last resort: no cues and no emojis — read the mood off the words
        # so the face never stays blank (a buddy always reacts)
        if not emoji_faces:
            face = _mood_face(speech)
            if face:
                steps.append(("face", face))
    return Plan(speech, display, steps)


# keyword mood guesses, checked in order; first hit wins (mirrors the old
# chirp_for heuristic, but for faces)
_MOOD_KEYWORDS = [
    ("love", ("love", "friend", "miss you", "hug")),
    ("sad", ("sad", "sorry", "oh no", "bad day")),
    ("surprised", ("wow", "whoa", "really?", "guess what", "amazing")),
    ("sleepy", ("sleep", "tired", "goodnight", "good night")),
    ("laugh", ("haha", "funny", "joke")),
    ("happy", ("yay", "fun", "play", "great", "!")),
]


def _mood_face(text):
    t = text.lower()
    for face, words in _MOOD_KEYWORDS:
        if any(w in t for w in words):
            return face
    return None


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
