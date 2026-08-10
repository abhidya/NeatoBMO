"""Ground-truth probe for SetLCD behavior: reply latency + visual checkpoints.

Sends a tiny labeled sequence, printing each command's reply time so we can
see which command (if any) stalls or never ACKs. Pauses between checkpoints
so the screen can be photographed/verified.

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
    ("white HLine at row 30 (expect the band to VANISH, column keeps a gap?)",
     ["FGWhite", "HLine 30", "FGBlack"]),
    ("white VLine at col 40 (expect the column to vanish)",
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
