#!/usr/bin/env python3
"""Stage-cue POC: show what a brain reply makes the body do.

Runs the cue parser over canned sloppy-model replies (offline, always works)
and — with --live "prompt" — over a real reply from the Colibri brain using
the same persona bmo_web sends.

    python3 tools/cue_demo.py
    python3 tools/cue_demo.py --live "do you want to play a game?"
"""
import argparse
import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from neatobmo import cues

CANNED = [
    "Yay, you are back! [happy] [wiggle] Who wants to play video games? "
    "[sound:videogames] [party]",
    "*wiggles excitedly* I missed you so much!! *happy*",
    "[laughs] That was a good one! [dancing]",
    "Oh no, I dropped it... [sad] [sound: bmo time] wait, that cheered me up!",
    "[thinks very hard] Hmm (just between us) I think yes! [wink]",
    "I love you! \U0001F60D\U0001F389",          # emoji-only, old style
    "Hello friend, how are you today?",           # no cues at all
]


def show(reply):
    plan = cues.parse(reply)
    print(f"reply:   {reply!r}")
    print(f"speech:  {plan.speech!r}")
    print(f"display: {plan.display!r}")
    print(f"steps:   {plan.steps or '(none)'}")
    print("-" * 60)


def live(prompt):
    brain = os.environ.get("NEATOBMO_BRAIN", "http://127.0.0.1:8000/v1").rstrip("/")
    key_file = os.path.expanduser("~/.neatobmo/coli_api_key")
    key = open(key_file).read().strip() if os.path.exists(key_file) else None
    import bmo_web
    req = urllib.request.Request(
        brain + "/chat/completions",
        data=json.dumps({"model": "olmoe", "messages": [
            {"role": "system", "content": bmo_web.PERSONA},
            {"role": "user", "content": prompt}]}).encode(),
        headers={"Content-Type": "application/json",
                 **({"Authorization": f"Bearer {key}"} if key else {})})
    reply = json.loads(urllib.request.urlopen(req, timeout=300).read()
                       )["choices"][0]["message"]["content"]
    show(reply)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", metavar="PROMPT",
                    help="ask the running Colibri brain instead of canned text")
    args = ap.parse_args()
    if args.live:
        live(args.live)
    else:
        for r in CANNED:
            show(r)
