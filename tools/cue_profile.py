#!/usr/bin/env python3
"""Profile the stage-cue protocol against the live Colibri brain.

Fires a battery of emotion-baiting prompts through the real persona, parses
each reply, and reports: cue emission rate, parser catch rate, tokens the
parser dropped without resolving (potential misses), vocabulary coverage
(which faces/moves/sounds the model never touches), and generation latency.

    python3 tools/cue_profile.py            # full battery
    python3 tools/cue_profile.py -n 4       # first 4 prompts only
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from neatobmo import cues, emote

# each prompt baits a different corner of the corpus
PROMPTS = [
    ("greeting",  "I'm home! did you miss me?"),
    ("play",      "want to play video games with me?"),
    ("joke",      "tell me a funny joke!"),
    ("sad",       "I had a really bad day today..."),
    ("dance",     "show me your best dance moves!"),
    ("scare",     "BOO! did I scare you?"),
    ("sleep",     "it's late, time to go to sleep buddy"),
    ("love",      "you're my best friend, I love you BMO"),
    ("angry",     "someone stepped on my sandcastle!"),
    ("surprise",  "guess what? we're going to the beach tomorrow!"),
    ("music",     "sing me a song!"),
    ("curious",   "what do you think is inside the mystery box?"),
]


def brain_ask(prompt, persona):
    brain = os.environ.get("NEATOBMO_BRAIN", "http://127.0.0.1:8000/v1").rstrip("/")
    key_file = os.path.expanduser("~/.neatobmo/coli_api_key")
    key = open(key_file).read().strip() if os.path.exists(key_file) else None
    req = urllib.request.Request(
        brain + "/chat/completions",
        data=json.dumps({"model": "olmoe", "messages": [
            {"role": "system", "content": persona},
            {"role": "user", "content": prompt}]}).encode(),
        headers={"Content-Type": "application/json",
                 **({"Authorization": f"Bearer {key}"} if key else {})})
    t0 = time.time()
    reply = json.loads(urllib.request.urlopen(req, timeout=300).read()
                       )["choices"][0]["message"]["content"]
    return reply, time.time() - t0


def token_texts(raw):
    """Every bracket/asterisk token the tokenizer sees in a raw reply."""
    return [next(g for g in m.groups() if g is not None)
            for m in cues._TOKEN.finditer(raw)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", type=int, default=len(PROMPTS))
    args = ap.parse_args()

    import bmo_web
    used = {"face": set(), "move": set(), "sound": set()}
    rows, missed_tokens = [], []

    for label, prompt in PROMPTS[:args.n]:
        try:
            reply, dt = brain_ask(prompt, bmo_web.PERSONA)
        except Exception as e:
            print(f"{label:9s}  BRAIN ERROR: {e}")
            continue
        plan = cues.parse(reply)
        toks = token_texts(reply)
        resolved = [s for s in plan.steps]
        emoji_faces = emote.parse_emojis(reply)
        # tokens that produced no step and weren't kept as speech: misses
        unresolved = [t for t in toks if not cues._resolve(t, stagey=True)]
        missed_tokens.extend((label, t) for t in unresolved)
        for kind, name in plan.steps:
            used[kind].add(name)
        rows.append({
            "label": label, "latency": dt, "reply": reply,
            "tokens": len(toks), "steps": plan.steps,
            "unresolved": unresolved, "emoji": len(emoji_faces),
            "speech": plan.speech,
        })
        kinds = ",".join(f"{k}:{n}" for k, n in plan.steps) or "-"
        print(f"{label:9s} {dt:5.1f}s  tokens={len(toks)} caught={len(resolved)}"
              f" emoji={len(emoji_faces)}  [{kinds}]")
        if unresolved:
            print(f"          unresolved: {unresolved}")

    if not rows:
        print("no replies — is the brain running?")
        return

    print("\n" + "=" * 64)
    n = len(rows)
    with_cues = sum(1 for r in rows if r["steps"])
    with_toks = sum(1 for r in rows if r["tokens"])
    total_toks = sum(r["tokens"] for r in rows)
    total_caught = sum(len(r["steps"]) for r in rows)
    lat = sorted(r["latency"] for r in rows)
    print(f"replies: {n}   emitted cue tokens: {with_toks}/{n}"
          f"   produced actions: {with_cues}/{n}")
    print(f"tokens seen: {total_toks}   steps caught: {total_caught}"
          f"   unresolved: {len(missed_tokens)}")
    print(f"latency s: min {lat[0]:.1f} / median {lat[n // 2]:.1f} / max {lat[-1]:.1f}")

    print("\ncorpus coverage (never emitted by the model this run):")
    for kind, vocab in (("face", set(cues.FACE_NAMES)),
                        ("move", set(cues.MOVES)),
                        ("sound", set(cues.SOUND_CUES))):
        unused = sorted(vocab - used[kind])
        print(f"  {kind:5s} used {sorted(used[kind]) or '-'}")
        print(f"        unused {unused or '-'}")

    if missed_tokens:
        print("\nunresolved tokens (candidates for new aliases):")
        for label, t in missed_tokens:
            print(f"  {label:9s} {t!r}")

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                       "logs", "cue-profile.json")
    with open(out, "w") as f:
        json.dump(rows, f, indent=1, ensure_ascii=False)
    print(f"\nfull replies -> logs/cue-profile.json")


if __name__ == "__main__":
    main()
