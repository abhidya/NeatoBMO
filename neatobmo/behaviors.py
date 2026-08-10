"""BMO personality behaviors: emotes, dances, reactions.

Motion behaviors need floor space — every one starts gentle and small.
"""
import time


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
