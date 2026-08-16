import unittest

from neatobmo import speech


class FakeStop:
    def __init__(self, stop_on=None):
        self.stop_on = stop_on
        self.waits = []

    def wait(self, seconds):
        self.waits.append(seconds)
        return len(self.waits) == self.stop_on


class FakeRobot:
    def __init__(self):
        self.commands = []

    def cmd(self, command, timeout=None):
        self.commands.append((command, timeout))


class FakeBody:
    def __init__(self):
        self.robot = FakeRobot()

    def try_call(self, fn, lock_timeout):
        fn(self.robot)
        return True


class ThinkingFeedbackTests(unittest.TestCase):
    def service(self):
        service = speech.SpeechService(FakeBody(), object())
        service.thinking_sounds = {3: b"curious", 19: b"boop", 9: b"unused"}
        return service

    def test_thinking_feedback_is_delayed_bounded_and_never_hums(self):
        service = self.service()
        stop = FakeStop()

        service._thinking_loop(stop)

        self.assertEqual(stop.waits, [2.2, 4.5])
        self.assertEqual(service.body.robot.commands,
                         [("PlaySound 3", 3), ("PlaySound 19", 3)])

    def test_short_wait_stays_silent(self):
        service = self.service()
        stop = FakeStop(stop_on=1)

        service._thinking_loop(stop)

        self.assertEqual(stop.waits, [2.2])
        self.assertEqual(service.body.robot.commands, [])


if __name__ == "__main__":
    unittest.main()
