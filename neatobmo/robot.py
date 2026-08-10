"""High-level Neato XV control: typed sensors, sounds, moods, motion, faces."""
import time

from .sounds import SOUNDS, MOODS
from . import transport


def _kv(text, cast=str):
    out = {}
    for line in text.splitlines()[2:]:
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 2 and parts[0]:
            try:
                out[parts[0]] = cast(parts[1]) if parts[1] else parts[1]
            except ValueError:
                out[parts[0]] = parts[1]
    return out


class Robot:
    def __init__(self, target=None):
        self.t = transport.connect(target)
        self._testmode = False

    def cmd(self, c, timeout=3.0):
        return self.t.send(c, timeout)

    # ---- modes
    def testmode(self, on=True):
        self.cmd("TestMode " + ("On" if on else "Off"))
        self._testmode = on

    def _need_testmode(self):
        if not self._testmode:
            self.testmode(True)

    # ---- sensors
    def version(self):
        return _kv(self.cmd("GetVersion"))

    def accel(self):
        return _kv(self.cmd("GetAccel"), float)

    def buttons(self):
        return {k: bool(int(v)) for k, v in _kv(self.cmd("GetButtons")).items()}

    def charger(self):
        return _kv(self.cmd("GetCharger"), float)

    def analog(self):
        return _kv(self.cmd("GetAnalogSensors"), int)

    def digital(self):
        return {k: bool(int(v)) for k, v in _kv(self.cmd("GetDigitalSensors")).items()}

    def lds_scan(self):
        """{angle_deg: dist_mm} for valid returns, plus rotation rpm."""
        points, rpm = {}, 0.0
        for line in self.cmd("GetLDSScan", timeout=4).splitlines():
            p = line.split(",")
            if p[0] == "ROTATION_SPEED" and len(p) > 1:
                try:
                    rpm = float(p[1])
                except ValueError:
                    pass
            elif len(p) == 4 and p[0].isdigit():
                try:
                    a, d, _, err = (int(x, 0) for x in p)
                except ValueError:
                    continue
                if err == 0 and 0 < d < 10000:
                    points[a] = d
        return points, rpm

    def lidar(self, on=True):
        self._need_testmode()
        self.cmd("SetLDSRotation " + ("On" if on else "Off"))

    # ---- voice
    def play(self, sound):
        """sound: id int, sound name, or mood name."""
        if isinstance(sound, str):
            sound = SOUNDS.get(MOODS.get(sound, sound), None) if not sound.isdigit() else int(sound)
        if sound is None:
            raise ValueError(f"unknown sound")
        self.cmd(f"PlaySound {sound}")

    def play_file(self, wav):
        """Stream a PCM WAV to firmware patched with ``PlaySound File``.

        The command is deliberately separate from ``play`` so stock sound IDs
        remain available and callers get a clear transfer error on unpatched
        firmware instead of silently falling back to another speaker.
        """
        wav = bytes(wav)
        if len(wav) < 44 or wav[:4] != b"RIFF" or wav[8:12] != b"WAVE":
            raise ValueError("PlaySound File requires a WAV payload")
        if len(wav) > 512 * 1024:
            raise ValueError("PlaySound File WAV exceeds the 512 KiB transfer limit")
        self.t.send_binary("PlaySound File", wav)

    # ---- light moods
    def led(self, mode):
        """mode: green|amber|red|green_dim|amber_dim|off|backlight_on|backlight_off"""
        m = {
            "green": "ButtonGreen", "amber": "ButtonAmber", "red": "LEDRed",
            "green_dim": "ButtonGreenDim", "amber_dim": "ButtonAmberDim",
            "off": "ButtonOff", "backlight_on": "BacklightOn", "backlight_off": "BacklightOff",
        }[mode]
        self._need_testmode()
        self.cmd("SetLED " + m)

    # ---- motion (TestMode)
    def move(self, left_mm, right_mm, speed=100, accel=None):
        self._need_testmode()
        c = f"SetMotor LWheelDist {int(left_mm)} RWheelDist {int(right_mm)} Speed {int(speed)}"
        if accel:
            c += f" Accel {int(accel)}"
        self.cmd(c)

    def stop(self):
        self._need_testmode()
        self.cmd("SetMotor LWheelDist 0 RWheelDist 0 Speed 1")

    WHEEL_TRACK = 254  # mm, from neato-driver-python

    def turn(self, degrees, speed=150):
        import math
        arc = math.pi * self.WHEEL_TRACK * degrees / 360.0
        self.move(arc, -arc, speed)

    # ---- LCD (crude primitives; full-span lines per firmware help)
    def lcd(self, *ops):
        """ops: sequences like 'BGWhite', 'FGBlack', 'HLine 20', 'VBars'."""
        self._need_testmode()
        for op in ops:
            self.cmd("SetLCD " + op)

    def close(self):
        self.t.close()
