"""Instant conversation routines: the Siri/Google layer in front of the LLM.

The local OLMoE brain takes tens of seconds per reply. For the utterances a
buddy hears all day ("hello!", "dance!", "I love you") that latency kills the
magic, so this module matches intents with plain patterns and answers from
precached scripts — no model in the loop. Replies are written in the stage-cue
language (neatobmo/cues.py), so a routine's choreography (dance + soundboard +
faces) rides the exact pipeline a brain reply does: parse -> faces/sounds/
moves/speech.

A tiny state machine handles multi-turn exchanges: a routine may set
``expect``, and the next utterance is first offered to that follow-up handler
(e.g. "wanna play a game?" -> yes/no). State lives in a ConvoState the caller
keeps per session.

Dynamic answers (time, battery) are callables receiving a context dict; the
web layer passes ``{"robot": robot}``.

    from neatobmo import routines
    state = routines.ConvoState()
    hit = routines.match("dance for me!", state, ctx={})
    if hit:            # hit.reply is cue-annotated text, ready for cues.parse
        ...
"""
import re
import time


class ConvoState:
    def __init__(self):
        self.expect = None          # name of a pending follow-up, or None
        self.expect_deadline = 0.0  # follow-ups expire; stale yes/no is chat

    def arm(self, name, ttl=45.0):
        self.expect = name
        self.expect_deadline = time.time() + ttl

    def pending(self):
        if self.expect and time.time() <= self.expect_deadline:
            return self.expect
        self.expect = None
        return None


class Hit:
    def __init__(self, routine, reply):
        self.routine = routine      # routine name, for logs/UI
        self.reply = reply          # cue-annotated text, same as a brain reply


def _time_reply(ctx):
    now = time.localtime()
    return time.strftime("It is %I:%M! [happy] [sound:beep]", now).replace(" 0", " ")


def _battery_reply(ctx):
    robot = ctx.get("robot")
    if robot is None:
        return "No tummy plugged! [sad] [sound:beep] [look]"
    try:
        pct = robot.charger().get("FuelPercent", None)
    except Exception:
        pct = None
    if pct is None:
        return "Tummy not answering! [surprised] [sound:json] [look]"
    mood = "[party] [sound:yeah]" if pct > 75 else \
        "[happy] [sound:beep]" if pct > 35 else "[sleepy] [sound:beep]"
    return f"Battery {pct:.0f} percent! {mood}"


# Jokes need their words; every other script is soundbyte-first: at most a
# few spoken words, the soundboard/moves/faces carry the feeling (matches
# the SPEAK AT MOST 3 WORDS persona so BMO sounds the same either way).
JOKES = [
    "Why did the vacuum go to therapy? Too much baggage! [laugh] [sound:yeah]",
    "What is a robot's favorite snack? Micro chips! [laugh] [sound:butt] [wiggle]",
    "BMO would tell a vacuum joke, but it sucks! [laugh] [sound:butt]",
]

# Each routine: patterns (regex, matched on lowered text), replies (strings or
# callables(ctx) -> string), optional expect (arms a follow-up).
ROUTINES = [
    {"name": "greet",
     "patterns": [r"\b(hello|hi there|hiya|hey (beemo|bmo|buddy)|good morning)\b"],
     "replies": ["You came back! [sound:hello] [happy] [wiggle]",
                 "Hi hi hi! [sound:yeah] [happy] [spin]",
                 "Hello, friend! [sound:hello] [love] [wiggle]"]},
    {"name": "who",
     "patterns": [r"\bwho are you\b", r"\bwhat are you\b", r"\byour name\b"],
     "replies": ["BMO is BMO! [sound:bmotime] [happy] [spin]"]},
    {"name": "dance",
     "patterns": [r"\bdance\b", r"\bbust a move\b", r"\bboogie\b"],
     "replies": ["Watch this! [sound:bmotime] [dance] [party]",
                 "Dance time! [sound:videogames] [dance] [party] [sound:yeah]"]},
    {"name": "spin",
     "patterns": [r"\bspin\b", r"\bturn around\b", r"\btwirl\b"],
     "replies": ["Wheee! [spin] [sound:yeah] [party]"]},
    {"name": "sing",
     "patterns": [r"\bsing\b", r"\bsong\b", r"\bmusic\b"],
     "replies": ["My favorite! [sound:videogames] [party] [wiggle]"]},
    {"name": "love",
     "patterns": [r"\bi love you\b", r"\bbest friend\b", r"\bmiss(ed)? you\b"],
     "replies": ["Love you more! [love] [sound:homeboys] [wiggle]",
                 "Best friends forever! [love] [sound:yeah] [spin]"]},
    {"name": "joke",
     "patterns": [r"\bjoke\b", r"\bmake me laugh\b", r"\bsomething funny\b"],
     "replies": JOKES},
    {"name": "time",
     "patterns": [r"\bwhat time\b", r"\btime is it\b"],
     "replies": [_time_reply]},
    {"name": "battery",
     "patterns": [r"\bbattery\b", r"\bcharge(d)?\b", r"\bfuel\b"],
     "replies": [_battery_reply]},
    {"name": "sleep",
     "patterns": [r"\bgood ?night\b", r"\bgo to sleep\b", r"\bbed ?time\b"],
     "replies": ["Night night, friend. [sleepy] [sound:beep]"]},
    {"name": "game",
     "patterns": [r"\bplay a game\b", r"\bwanna play\b", r"\blet'?s play\b",
                  r"\bvideo ?games?\b"],
     "replies": ["Racing or dancing? [sound:videogames] [party] [happy]"],
     "expect": "game_choice"},
    {"name": "stop",
     "patterns": [r"\bstop\b", r"\bquiet\b", r"\bhush\b", r"\bcalm down\b"],
     "replies": ["Okay. Quiet mode. [neutral]"]},
]

# follow-up handlers for armed expectations: name -> [(patterns, replies)]
FOLLOW_UPS = {
    "game_choice": [
        ([r"\b(rac\w*|adventure|first|both)\b"],
         ["Racing! Zoom zoom! [sound:videogames] [spin] [party]"]),
        ([r"\b(dance|battle|second)\b"],
         ["Dance battle! [sound:bmotime] [dance] [party]"]),
        ([r"\b(no|nah|later|not now)\b"],
         ["Aww okay, next time! [sad] [sound:beep]"]),
    ],
}

_rotation = {}   # routine key -> last reply index (rotate, never repeat)


def _pick(key, replies, ctx):
    i = _rotation[key] = (_rotation.get(key, -1) + 1) % len(replies)
    r = replies[i]
    return r(ctx) if callable(r) else r


def match(text, state=None, ctx=None):
    """Return a Hit if a routine answers this utterance instantly, else None."""
    ctx = ctx or {}
    t = " " + re.sub(r"\s+", " ", text.strip().lower()) + " "
    if state is not None and (pending := state.pending()):
        for i, (patterns, replies) in enumerate(FOLLOW_UPS.get(pending, [])):
            if any(re.search(p, t) for p in patterns):
                state.expect = None
                return Hit(f"{pending}", _pick(f"{pending}:{i}", replies, ctx))
        state.expect = None      # user changed the subject; fall through
    for routine in ROUTINES:
        if any(re.search(p, t) for p in routine["patterns"]):
            if state is not None and routine.get("expect"):
                state.arm(routine["expect"])
            return Hit(routine["name"],
                       _pick(routine["name"], routine["replies"], ctx))
    return None


def canned_texts():
    """Every static reply, for pre-synthesis caches (dynamic ones excluded)."""
    out = []
    for routine in ROUTINES:
        out.extend(r for r in routine["replies"] if not callable(r))
    for handlers in FOLLOW_UPS.values():
        for _, replies in handlers:
            out.extend(r for r in replies if not callable(r))
    return out
