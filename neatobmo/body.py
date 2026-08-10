"""BodyController: the one owner of robot access policy.

Every robot touch in the orchestrator goes through here, so the lock, the
"never crash the chat" swallow policy, the worker threads, and the
ESP32-first / USB-fallback emote path live in one module.  Callers hold no
lock and check no globals; tests inject a fake robot (and fake Esp32Client)
and hit the same six methods production uses.
"""
import threading

from . import cues
from . import faces


class BodyController:
    def __init__(self, robot=None, esp32=None, via=None):
        self.robot = robot
        self.esp32 = esp32
        self.via = via          # "usb" | "bridge" | None, for the UI pill
        self._lock = threading.Lock()

    @property
    def attached(self):
        return self.robot is not None

    # ---- synchronous access (raises; caller shapes the error) -----------
    def call(self, fn):
        """Run fn(robot) under the lock and return its value."""
        if self.robot is None:
            raise RuntimeError("body not attached")
        with self._lock:
            return fn(self.robot)

    def try_call(self, fn, lock_timeout):
        """call() with a bounded lock wait; False if the body is busy.

        Exceptions from fn are swallowed — this is the polite path used by
        background loops (thinking sounds) that must never wedge speech.
        """
        if self.robot is None:
            return False
        if not self._lock.acquire(timeout=lock_timeout):
            return False
        try:
            fn(self.robot)
            return True
        except Exception:
            return False
        finally:
            self._lock.release()

    # ---- fire-and-forget (never raises, never blocks the caller) --------
    def run(self, fn):
        """Run fn(robot) on a worker thread under the lock; swallow errors."""
        if self.robot is None:
            return
        def work():
            try:
                with self._lock:
                    fn(self.robot)
            except Exception:
                pass
        threading.Thread(target=work, daemon=True).start()

    def perform(self, actions):
        """Run stage-cue sound/move steps (cues.perform) off-thread."""
        if actions:
            self.run(lambda r: cues.perform(r, actions))

    def emote(self, text):
        """LCD face cascade: prefer the ESP32 (faces.c), fall back to USB.

        Both paths draw the identical faces — the ESP32 runs faces.c, the
        USB fallback runs neatobmo/faces.py (a 1:1 port of the same tables).
        """
        def push():
            if self.esp32 is not None:
                try:
                    self.esp32.emote(text)
                    return
                except Exception:
                    pass
            if self.robot is not None:
                try:
                    with self._lock:
                        faces.cascade(self.robot, text)
                except Exception:
                    pass
        threading.Thread(target=push, daemon=True).start()
