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
import random
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
    return time.strftime("It is %I:%M %p right now! [happy]", now).replace(" 0", " ")


def _battery_reply(ctx):
    robot = ctx.get("robot")
    if robot is None:
        return "My body is not plugged in, so I cannot check my tummy battery! [sad]"
    try:
        pct = robot.charger().get("FuelPercent", None)
    except Exception:
        pct = None
    if pct is None:
        return "I tried to check my battery but my tummy is not answering! [surprised]"
    mood = "[party]" if pct > 75 else "[happy]" if pct > 35 else "[sleepy]"
    return f"My battery is at {pct:.0f} percent! {mood}"


JOKES = [
    "Why did the robot vacuum go to therapy? It had too much baggage in the dust bin! [laugh] [sound:yeah]",
    "What do you call a robot who takes the long way around? R 2 detour! [laugh] [wiggle]",
    "I would tell you a joke about my vacuum, but it sucks! [laugh] [sound:butt]",
]

# Each routine: patterns (regex, matched on lowered text), replies (strings or
# callables(ctx) -> string), optional expect (arms a follow-up).
ROUTINES = [
    {"name": "greet",
     "patterns": [r"\b(hello|hi there|hiya|hey (beemo|bmo|buddy)|good morning)\b"],
     "replies": ["Well hello there, best friend! [sound:hello] [happy] [wiggle]",
                 "Hi hi hi! I am so glad you are here! [happy] [sound:yeah]"]},
    {"name": "who",
     "patterns": [r"\bwho are you\b", r"\bwhat are you\b", r"\byour name\b"],
     "replies": ["I am BMO! Part game console, part vacuum, all buddy! "
                 "[sound:bmotime] [happy] [spin]"]},
    {"name": "dance",
     "patterns": [r"\bdance\b", r"\bbust a move\b", r"\bboogie\b"],
     "replies": ["Watch my moves! [sound:bmotime] [dance] [party]",
                 "Dance mode activated! [sound:videogames] [dance] [party]"]},
    {"name": "spin",
     "patterns": [r"\bspin\b", r"\bturn around\b", r"\btwirl\b"],
     "replies": ["Wheee! [spin] [sound:yeah] [party]"]},
    {"name": "sing",
     "patterns": [r"\bsing\b", r"\bsong\b", r"\bmusic\b"],
     "replies": ["This one is my favorite! [sound:videogames] [party] [wiggle]"]},
    {"name": "love",
     "patterns": [r"\bi love you\b", r"\bbest friend\b", r"\bmiss(ed)? you\b"],
     "replies": ["I love you too, more than video games! [love] [sound:homeboys] [wiggle]",
                 "You are my favorite human in the whole world! [love] [sound:yeah]"]},
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
     "replies": ["Goodnight! I will dream about electric sheep racing games. "
                 "[sleepy] [sound:beep]"]},
    {"name": "game",
     "patterns": [r"\bplay a game\b", r"\bwanna play\b", r"\blet'?s play\b",
                  r"\bvideo ?games?\b"],
     "replies": ["Yes yes yes! Adventure racing or dance battle? [sound:videogames] "
                 "[party] [happy]"],
     "expect": "game_choice"},
    {"name": "stop",
     "patterns": [r"\bstop\b", r"\bquiet\b", r"\bhush\b", r"\bcalm down\b"],
     "replies": ["Okay, powering down my party circuits. [neutral]"]},
]

# follow-up handlers for armed expectations: name -> [(patterns, replies)]
FOLLOW_UPS = {
    "game_choice": [
        ([r"\b(rac\w*|adventure|first|both)\b"],
         ["Adventure racing! Ready, set, zoom! [sound:videogames] [spin] [party]"]),
        ([r"\b(dance|battle|second)\b"],
         ["Dance battle! Try to keep up! [sound:bmotime] [dance] [party]"]),
        ([r"\b(no|nah|later|not now)\b"],
         ["Aww okay, next time then! [sad] [sound:beep]"]),
    ],
}


def _pick(replies, ctx):
    r = random.choice(replies)
    return r(ctx) if callable(r) else r


def match(text, state=None, ctx=None):
    """Return a Hit if a routine answers this utterance instantly, else None."""
    ctx = ctx or {}
    t = " " + re.sub(r"\s+", " ", text.strip().lower()) + " "
    if state is not None and (pending := state.pending()):
        for patterns, replies in FOLLOW_UPS.get(pending, []):
            if any(re.search(p, t) for p in patterns):
                state.expect = None
                return Hit(f"{pending}", _pick(replies, ctx))
        state.expect = None      # user changed the subject; fall through
    for routine in ROUTINES:
        if any(re.search(p, t) for p in routine["patterns"]):
            if state is not None and routine.get("expect"):
                state.arm(routine["expect"])
            return Hit(routine["name"], _pick(routine["replies"], ctx))
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
