import time
import unittest
from pathlib import Path
from unittest import mock

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


class AuthenticClipPlaybackTests(unittest.TestCase):
    def test_catalog_hit_uses_esp32_runtime_audio_without_flash_bank(self):
        class Board:
            def synth(self, text):
                return b"RIFFauthentic" if text == "Hello there" else None

        class Voice:
            default_voice = "bmo-rvc"
            soundboard = Board()

        class Esp32:
            def __init__(self):
                self.wavs = []

            def speak(self, wav):
                self.wavs.append(wav)

        class Body:
            attached = True
            esp32 = Esp32()

        service = speech.SpeechService(Body(), Voice())
        job, error = service.speak("Hello there")
        self.assertIsNone(error)
        deadline = time.time() + 1
        while job["state"] not in ("complete", "error") and time.time() < deadline:
            time.sleep(0.01)

        self.assertEqual(job["state"], "complete")
        self.assertEqual(service.body.esp32.wavs, [b"RIFFauthentic"])
        self.assertEqual(service.flash_writes, 0)


class PreGeneratedSoundboardInstallTests(unittest.TestCase):
    def test_install_uses_saved_verified_module_without_rebuilding(self):
        class Body:
            attached = True

            def call(self, fn):
                return fn(object())

        class Board:
            def module_artifact(self, key):
                return {"key": key, "label": "hello there",
                        "sha256": "a" * 64, "path": Path("prepared.bin"),
                        "payload": b"prepared", "slots": [0]}

        service = speech.SpeechService(Body(), object())
        proof = {"sha256": "a" * 64, "accepted_ids": list(range(21)),
                 "receiver_hex": "06"}
        with mock.patch.object(speech.tts_bank.BankBurner, "restore_bank",
                               return_value=proof) as restore:
            result = service.install_soundboard_module(
                Board(), "approved-key",
                speech.SOUNDBOARD_INSTALL_CONFIRMATION)

        self.assertTrue(result["pre_generated"])
        self.assertEqual(service.flash_writes, 1)
        restore.assert_called_once_with(
            Path("prepared.bin"), "a" * 64,
            "pre-generated BMO page for hello there")

    def test_install_requires_explicit_confirmation(self):
        class Body:
            attached = True

        service = speech.SpeechService(Body(), object())
        result = service.install_soundboard_module(object(), "key", "yes")
        self.assertIn(speech.SOUNDBOARD_INSTALL_CONFIRMATION,
                      result["error"])


if __name__ == "__main__":
    unittest.main()
