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
        if self.peek_pending():
            return self.expect
        self.expect = None
        return None

    def peek_pending(self):
        """Return the live expectation without consuming or clearing it."""
        if self.expect and time.time() <= self.expect_deadline:
            return self.expect
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
    if pct <= 35:
        return "Battery low shut down [sleepy]"
    mood = "[party] [sound:yeah]" if pct > 75 else "[happy] [sound:beep]"
    return f"Battery {pct:.0f} percent! {mood}"


def _miner_reply(ctx):
    """BMO reports on the Bitcoin lottery in-character."""
    miner = ctx.get("miner")
    if miner is None:
        return "No coin machine! [sad] [sound:beep] [look]"
    try:
        status = miner.status()
    except Exception:
        status = {}
    state = status.get("state")
    # "disabled" with an address already saved is a different situation from
    # having no wallet at all; telling a friend to set an address they just
    # set reads as BMO not paying attention.
    if state in ("no_address",) or not (status.get("address") or "").strip():
        return ("BMO needs a coin wallet! Ask friend to set a bitcoin "
                "address in the console. [surprised] [sound:json] [look]")
    if state in ("idle", "disabled", "stopped"):
        return ("Coin mine is off. Press start and BMO will dig! "
                "[sleepy] [sound:beep] [look]")
    if state in ("starting", "connecting"):
        return "BMO plugging into the coin mine... [look] [sound:beep]"
    if state == "error":
        error = (status.get("error") or "unknown")[:60]
        return f"Coin mine broke! {error}. [sad] [sound:json] [look]"
    hashrate = status.get("hashrate_label") or "0 H/s"
    if status.get("block_found"):
        return (f"BMO WON the shiny coin lottery! {hashrate}! "
                "[party] [sound:yeah] [spin]")
    best = status.get("best_seen_difficulty") or 0
    if state == "mining":
        return (f"BMO digging for shiny coins! {hashrate}, closest luck "
                f"{best:.1f}. No coin yet. [happy] [sound:videogames] [wiggle]")
    return f"Coin mine off. BMO resting. {hashrate}. [sleepy] [sound:beep]"


def _decrypt_reply(ctx):
    """Run the real Cruz .enc decrypt attempt; celebrate only on a true hit.

    The context carries the image path, the ESP32 client, and the output dir
    (all optional).  The reply is stage-cue text, so a success's [dance]
    [party] [sound:yeah] rides the normal cues pipeline to the body.
    """
    from . import decrypt
    settings = ctx.get("decrypt") or {}
    try:
        result = decrypt.attempt(
            settings.get("image"), esp32=ctx.get("esp32"),
            output_dir=settings.get("output_dir"))
        return result.reply
    except Exception:
        return decrypt.BROKEN_REPLY


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
     "replies": ["Battery low shut down [sleepy]"]},
    {"name": "game",
     "patterns": [r"\bplay a game\b", r"\bwanna play\b", r"\blet'?s play\b",
                  r"\bvideo ?games?\b"],
     "replies": ["Racing or dancing? [sound:videogames] [party] [happy]"],
     "expect": "game_choice"},
    {"name": "stop",
     "patterns": [r"\bstop\b", r"\bquiet\b", r"\bhush\b", r"\bcalm down\b"],
     "replies": ["Okay. Quiet mode. [neutral]"]},
    {"name": "decrypt",
     "patterns": [r"\bdecrypt\w*\b", r"\bunlock\w*\b",
                  r"\bcrack(ed|ing)?\b", r"\bhack(ed|ing)?\b"],
     "replies": [_decrypt_reply]},
    {"name": "miner",
     "patterns": [r"\b(bitcoin|btc|crypto)\b", r"\b(miner|mining|mine)\b",
                  r"\blottery\b", r"\bhash ?rate\b", r"\bare you winning\b",
                  r"\bshiny coin\b"],
     "replies": [_miner_reply]},
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


_COVERAGE = {
    "greet": [r"(hello|hi|hiya|hi there|hey (beemo|bmo|buddy)|good morning)"],
    "who": [r"(who are you|what are you|what'?s your name|your name)"],
    "dance": [r"(can you |please |would you )?(dance|bust a move|boogie)( for me)?"],
    "spin": [r"(can you |please |would you )?(spin|turn around|twirl)"],
    "sing": [r"(can you |please |would you )?(sing|sing a song|play music)"],
    "love": [r"(i love you|you are my best friend|best friend|(?:i )?miss(ed)? you)"],
    "joke": [r"(tell me |make me |say )?(a )?(joke|something funny|laugh)( instead)?"],
    "time": [r"(what time is it|what is the time|tell me the time|time please)"],
    "battery": [r"(check |how is |what is |what'?s )?(your )?(battery|charge|fuel)( level| status)?"],
    "sleep": [r"(good ?night|go to sleep|bed ?time)"],
    "game": [r"(play a game|wanna play|let'?s play(?: a game)?|video ?games?)"],
    "stop": [r"(stop|quiet|hush|calm down)"],
    "decrypt": [r"(can you |please |would you )?(decrypt|unlock|crack|hack)"
                r"( your| the)?( software| firmware)?"],
    "miner": [r"(play( the)? )?(bitcoin|btc|crypto)( lottery| miner| mining)?",
              r"(mine|mining|miner)( bitcoin| btc)?",
              r"(how'?s|how is)( the)?( mining| miner| hashrate| hash rate)",
              r"(any )?bitcoin(s)?( yet)?",
              r"are you winning",
              r"what'?s (your|the) hashrate",
              r"bitcoin lottery"],
}

_FOLLOW_UP_COVERAGE = {
    "game_choice": [
        r"(the )?(rac\w*|adventure|first|both)( one)?",
        r"(the )?(dance|battle|second)( one)?",
        r"(no|nah|later|not now)",
    ],
}


def _routine_by_name(name):
    for routine in ROUTINES:
        if routine["name"] == name:
            return routine
    return None


def _covered_text(text):
    t = re.sub(r"\s+", " ", text.strip().lower())
    t = re.sub(r"^(please|can you|could you|would you|hey|okay|ok),?\s+", "", t)
    t = re.sub(r"\s+(please)$", "", t)
    return t


def accepts(name, text):
    """True when a routine can safely own the complete request text.

    This is intentionally stricter than match(): match() discovers a quick
    answer anywhere in an utterance, while the turn planner needs full-part
    coverage so it never drops the unhandled rest of a compound request.
    """
    t = _covered_text(text)
    return any(re.fullmatch(p, t) for p in _COVERAGE.get(name, []))


def accepted_routine(text, state=None):
    """Return the first routine name that fully covers text, else None."""
    pending = state.peek_pending() if state is not None else None
    if pending:
        covered = _covered_text(text)
        for index, pattern in enumerate(_FOLLOW_UP_COVERAGE.get(pending, [])):
            if re.fullmatch(pattern, covered):
                return f"{pending}:{index}"
    for routine in ROUTINES:
        name = routine["name"]
        if accepts(name, text):
            return name
    return None


def run(name, state=None, ctx=None):
    """Execute one already-planned routine by name."""
    ctx = ctx or {}
    if ":" in name:
        pending, raw_index = name.rsplit(":", 1)
        try:
            _patterns, replies = FOLLOW_UPS[pending][int(raw_index)]
        except (KeyError, IndexError, ValueError):
            return None
        if state is not None:
            state.expect = None
        return Hit(pending, _pick(name, replies, ctx))
    routine = _routine_by_name(name)
    if routine is None:
        return None
    if state is not None and state.pending():
        state.expect = None
    if state is not None and routine.get("expect"):
        state.arm(routine["expect"])
    return Hit(name, _pick(name, routine["replies"], ctx))


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
