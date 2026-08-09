#!/usr/bin/env python3
"""BMO agent: local-LLM brain wired to the Neato body.

Works with any OpenAI-compatible endpoint (Colibri, Ollama, llama.cpp server):
    python3 bmo_agent.py --llm http://10.0.0.63:11434/v1 --model qwen3:8b
    python3 bmo_agent.py --mock          # no LLM: type tool calls directly
    python3 bmo_agent.py --robot 10.0.0.106:3333   # via ESP32 bridge (default: USB)
"""
import argparse
import json
import os
import sys
import time
import urllib.request

from neatobmo import Robot
from neatobmo import behaviors

PROVIDERS_PATH = os.path.expanduser("~/.neatobmo/providers.json")


def load_provider(name=None):
    """Pick an LLM provider: named, or first reachable in configured order.

    Returns (base_url, model, api_key or None) or None if none reachable."""
    if not os.path.exists(PROVIDERS_PATH):
        return None
    with open(PROVIDERS_PATH) as f:
        cfg = json.load(f)
    names = [name] if name else cfg.get("order", list(cfg["providers"]))
    for n in names:
        p = cfg["providers"].get(n)
        if not p:
            continue
        key = None
        if p.get("api_key_env"):
            key = os.environ.get(p["api_key_env"])
            if not key:
                continue
        elif p.get("api_key_file"):
            path = os.path.expanduser(p["api_key_file"])
            if os.path.exists(path):
                key = open(path).read().strip()
        try:
            req = urllib.request.Request(p["base_url"].rstrip("/") + "/models",
                                         headers={"Authorization": f"Bearer {key}"} if key else {})
            with urllib.request.urlopen(req, timeout=2) as resp:
                data = json.loads(resp.read())
            model = p["model"]
            if model == "auto":
                model = data["data"][0]["id"]
            print(f"[brain] using provider '{n}' ({model})")
            return p["base_url"], model, key
        except Exception:
            continue
    return None

PERSONA = """You are BMO, a cheerful little robot buddy living in a Neato robot vacuum body.
You are curious, playful, a bit dramatic, and you love your human. Keep replies short and
spoken-style. You have a real physical body — use your tools to express yourself with
sounds, lights, movement, and dances. Never move if you were told you're on a desk."""

TOOLS = [
    {"type": "function", "function": {
        "name": "play_sound",
        "description": "Play an emotional sound: happy, hello, curious, scared, sad, grateful, alarm, protest",
        "parameters": {"type": "object", "properties": {"mood": {"type": "string"}}, "required": ["mood"]}}},
    {"type": "function", "function": {
        "name": "set_led",
        "description": "Set the body light mood: green, amber, red, green_dim, amber_dim, off",
        "parameters": {"type": "object", "properties": {"color": {"type": "string"}}, "required": ["color"]}}},
    {"type": "function", "function": {
        "name": "behavior",
        "description": "Run a full-body behavior: hello, happy_wiggle, scared, curious_look, spin_flourish, dance",
        "parameters": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}}},
    {"type": "function", "function": {
        "name": "drive",
        "description": "Drive: left/right wheel distance in mm (+fwd/-back), speed mm/s (max 300)",
        "parameters": {"type": "object", "properties": {
            "left_mm": {"type": "number"}, "right_mm": {"type": "number"},
            "speed": {"type": "number"}}, "required": ["left_mm", "right_mm"]}}},
    {"type": "function", "function": {
        "name": "get_sensors",
        "description": "Read body state: battery, tilt/accelerometer, buttons, bumpers",
        "parameters": {"type": "object", "properties": {}}}},
]


def run_tool(robot, name, args):
    if name == "play_sound":
        robot.play(args["mood"])
        return "played"
    if name == "set_led":
        robot.led(args["color"])
        return "set"
    if name == "behavior":
        fn = getattr(behaviors, args["name"], None)
        if not fn:
            return f"unknown behavior {args['name']}"
        fn(robot)
        return "done"
    if name == "drive":
        speed = min(int(args.get("speed", 100)), 300)
        robot.move(args["left_mm"], args["right_mm"], speed)
        return "moving"
    if name == "get_sensors":
        return json.dumps({
            "charger": robot.charger(), "accel": robot.accel(),
            "buttons": robot.buttons(), "bumpers": robot.digital(),
        })
    return "unknown tool"


def llm_chat(base, model, messages, api_key=None):
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(
        base.rstrip("/") + "/chat/completions",
        data=json.dumps({"model": model, "messages": messages, "tools": TOOLS}).encode(),
        headers=headers)
    with urllib.request.urlopen(req, timeout=300) as resp:
        return json.loads(resp.read())["choices"][0]["message"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--llm", default=None, help="explicit OpenAI-compatible base URL (overrides providers.json)")
    ap.add_argument("--model", default="qwen3:8b")
    ap.add_argument("--provider", default=None, help="force a named provider from ~/.neatobmo/providers.json")
    ap.add_argument("--robot", default=None, help="'usb' (default) or ESP32 'ip:3333'")
    ap.add_argument("--mock", action="store_true", help="no LLM; REPL of raw tool calls")
    args = ap.parse_args()

    robot = Robot(args.robot)
    print("body connected:", robot.version().get("ModelID", "?"))

    api_key = None
    if not args.llm and not args.mock:
        picked = load_provider(args.provider)
        if picked:
            args.llm, args.model, api_key = picked

    if args.mock or not args.llm:
        print("mock mode — try: behavior dance | play_sound happy | drive 100 100 | sensors")
        while True:
            try:
                line = input("bmo> ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not line:
                continue
            parts = line.split()
            try:
                if parts[0] == "sensors":
                    print(run_tool(robot, "get_sensors", {}))
                elif parts[0] == "behavior":
                    print(run_tool(robot, "behavior", {"name": parts[1]}))
                elif parts[0] == "play_sound":
                    print(run_tool(robot, "play_sound", {"mood": parts[1]}))
                elif parts[0] == "set_led":
                    print(run_tool(robot, "set_led", {"color": parts[1]}))
                elif parts[0] == "drive":
                    print(run_tool(robot, "drive",
                                   {"left_mm": float(parts[1]), "right_mm": float(parts[2]),
                                    "speed": float(parts[3]) if len(parts) > 3 else 100}))
                else:
                    print(robot.cmd(line))  # raw Neato command passthrough
            except Exception as e:
                print("error:", e)
        return

    messages = [{"role": "system", "content": PERSONA}]
    print(f"BMO is awake (brain: {args.model} @ {args.llm}). Say hi!")
    while True:
        try:
            user = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not user:
            continue
        messages.append({"role": "user", "content": user})
        while True:
            msg = llm_chat(args.llm, args.model, messages, api_key)
            messages.append(msg)
            calls = msg.get("tool_calls") or []
            if not calls:
                print("bmo>", msg.get("content", ""))
                break
            for call in calls:
                fn = call["function"]
                try:
                    result = run_tool(robot, fn["name"], json.loads(fn["arguments"] or "{}"))
                except Exception as e:
                    result = f"error: {e}"
                print(f"  [{fn['name']}] {result}")
                messages.append({"role": "tool", "tool_call_id": call["id"], "content": str(result)})


if __name__ == "__main__":
    main()
