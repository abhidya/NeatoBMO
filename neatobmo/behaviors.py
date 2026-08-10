"""BMO personality behaviors: emotes, dances, reactions.

Motion behaviors need floor space — every one starts gentle and small.
"""
import time


def hello(r):
    r.led("green")
    r.play("hello")
    r.lcd("BGWhite")


def happy_wiggle(r):
    """Quick left-right shimmy + happy chirp."""
    r.play("happy")
    for _ in range(3):
        r.move(40, -40, 180)
        time.sleep(0.5)
        r.move(-40, 40, 180)
        time.sleep(0.5)
    r.stop()


def scared(r):
    r.play("scared")
    r.led("red")
    r.move(-60, -60, 250)  # recoil
    time.sleep(0.8)
    r.led("amber_dim")


def curious_look(r):
    """Slow scan left, then right, like looking around."""
    r.play("curious")
    r.turn(30, 80)
    time.sleep(1.2)
    r.turn(-60, 80)
    time.sleep(1.6)
    r.turn(30, 80)


def spin_flourish(r):
    r.play("happy")
    r.turn(360, 300)


def dance(r):
    """First choreography: wiggle, spin, bow."""
    r.led("green")
    r.play("starting_cleaning")
    time.sleep(1)
    happy_wiggle(r)
    time.sleep(0.6)
    r.turn(180, 250)
    time.sleep(1.5)
    r.turn(-180, 250)
    time.sleep(1.5)
    spin_flourish(r)
    time.sleep(2.5)
    r.move(-50, -50, 60)   # slow reverse "bow"
    time.sleep(1.2)
    r.move(50, 50, 120)
    r.play("grateful")
    r.led("green_dim")


def sound_tour(r, pause=2.0):
    """Play every sound with its name — pick BMO's voice palette."""
    from .sounds import SOUNDS
    for name, sid in sorted(SOUNDS.items(), key=lambda kv: kv[1]):
        print(f"{sid:2d}  {name}")
        r.play(sid)
        time.sleep(pause)


# ---- reaction loop (pickup / petting) ----

def monitor(r, poll_hz=4):
    """Always-on reflexes: squeal when picked up, chirp when petted."""
    airborne = False
    while True:
        a = r.accel()
        tilted = abs(a.get("PitchInDegrees", 0)) > 15 or abs(a.get("RollInDegrees", 0)) > 15
        if tilted and not airborne:
            airborne = True
            r.play("scared")
            r.led("red")
        elif not tilted and airborne:
            airborne = False
            r.play("grateful")
            r.led("green")
        btns = r.buttons()
        if any(btns.values()):
            r.play("happy")
        time.sleep(1.0 / poll_hz)
