"""Ground-truth probe for SetLCD behavior: reply latency + visual checkpoints.

Sends a tiny labeled sequence, printing each command's reply time so we can
see which command (if any) stalls or never ACKs. Pauses between checkpoints
so the screen can be photographed/verified.

Verified results (2026-08-09/10, fw 2.4.15667): HLine/VLine take one number
and draw full-span 1px black lines; FGWhite is a complete no-op (ACKs, never
draws or erases) — there is no selective erase, only BGWhite/BGBlack fills;
any extra trailing number is parsed as a Contrast value and written to NAND,
so never send segment-style args; the fw drains SetLCD on a 10 Hz tick.

Run:  python3 tools/lcd_probe.py
"""
import sys
import time

sys.path.insert(0, __import__("os").path.join(__import__("os").path.dirname(__file__), ".."))
from neatobmo.robot import Robot  # noqa: E402

CHECKPOINTS = [
    ("contrast + clear", ["Contrast 45", "BGWhite", "FGBlack"]),
    ("one black HLine at row 30 (expect full-width dark band)", ["HLine 30"]),
    ("one black VLine at col 40 (expect full-height dark column)", ["VLine 40"]),
    ("white HLine at row 30 (expect NO change — FGWhite is a verified no-op)",
     ["FGWhite", "HLine 30", "FGBlack"]),
    ("white VLine at col 40 (expect NO change — FGWhite is a verified no-op)",
     ["FGWhite", "VLine 40", "FGBlack"]),
    ("high column VLine 96 (right-eye range)", ["VLine 96"]),
    ("high row HLine 120 (bottom range)", ["HLine 120"]),
]


def main():
    r = Robot()
    r.testmode(True)
    print("TestMode on. Watch the LCD between steps.\n")
    for label, ops in CHECKPOINTS:
        print(f"== {label}")
        for op in ops:
            t0 = time.monotonic()
            try:
                reply = r.cmd("SetLCD " + op)
                dt = (time.monotonic() - t0) * 1000
                tail = reply.strip().splitlines()[-1] if reply.strip() else "(empty)"
                flag = "  <-- SLOW/TIMEOUT" if dt > 1000 else ""
                print(f"   {op:<14} {dt:7.1f} ms  reply: {tail!r}{flag}")
            except Exception as e:
                print(f"   {op:<14} FAILED: {e}")
        input("   [enter] when you've looked at the screen... ")
    print("\nDone. Leaving TestMode on; run 'TestMode Off' when finished.")


if __name__ == "__main__":
    main()
