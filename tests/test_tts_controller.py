"""BankBurner / run_speech_operation tests with fakes — no robot, no serial.

Nothing in this file (or in neatobmo.tts_bank) opens a serial port; the robot
is a plain in-memory fake recording every interaction.
"""

import threading
import unittest
from pathlib import Path

from neatobmo import tts_bank
from neatobmo.tts_bank import (
    BankBurner,
    BankValidationError,
    PlaybackProgress,
    RestoreArtifactError,
    RobotVerificationError,
    SlotSegment,
    run_speech_operation,
    sha256,
)

ROOT = Path(__file__).resolve().parents[1]
BMO_ARTIFACT = ROOT / ("assets/bmo-sound-bank-offline-20260810/"
                       "DfltSoundLib.BMO.pcm-only.Rev1.0.bin")
GOOD_VERSION = ("GetVersion\r\nComponent,Major,Minor\r\n"
                "Serial Number,WTD41611DD-0037829-P\r\nSoftware,2,4,15667\r\n")


class FakeRobot:
    def __init__(self, version=GOOD_VERSION, live_ids=tts_bank.LIVE_SOUND_IDS,
                 fail_playsound_call=None):
        self.version = version
        self.live_ids = set(live_ids)
        self.fail_playsound_call = fail_playsound_call
        self.playsound_calls = 0
        self.events = []
        self.t = self

    def send_binary(self, command, payload, timeout=None):
        self.events.append(("upload", command, sha256(payload)))
        return b"\x06\x1a"

    def cmd(self, text, timeout=None):
        self.events.append(("cmd", text))
        if text == "GetVersion":
            return self.version
        if text.startswith("PlaySound "):
            self.playsound_calls += 1
            if self.fail_playsound_call == self.playsound_calls:
                raise RuntimeError("serial dropout during PlaySound")
            sound_id = int(text.split()[1])
            return "" if sound_id in self.live_ids else "PlaySound out of range"
        return ""


class FakeSleep:
    def __init__(self, on_sleep=None):
        self.calls = []
        self.on_sleep = on_sleep

    def __call__(self, seconds):
        self.calls.append(seconds)
        if self.on_sleep:
            self.on_sleep(seconds)


def make_segments():
    return [SlotSegment(0, 0, 63548, b"\x01\x02" * 100),
            SlotSegment(1, 1, 41176, b"\x03\x04" * 100),
            SlotSegment(2, 19, 7088, b"\x05\x06" * 50)]


class BurnVerifyTests(unittest.TestCase):
    def setUp(self):
        self.bank = BMO_ARTIFACT.read_bytes()
        self.sha = tts_bank.BMO_BANK_SHA256

    def test_only_the_validated_image_is_burnable(self):
        robot = FakeRobot()
        burner = BankBurner(robot, sleep=FakeSleep())
        with self.assertRaises(BankValidationError):
            burner.burn_and_verify(self.bank, "0" * 64, "temporary TTS bank")
        with self.assertRaises(BankValidationError):
            burner.burn_and_verify(self.bank[:-1], self.sha, "temporary TTS bank")
        self.assertEqual(robot.events, [])  # rejected before any robot contact

    def test_burn_verifies_identity_and_exact_slot_map(self):
        robot = FakeRobot()
        result = BankBurner(robot, sleep=FakeSleep()).burn_and_verify(
            self.bank, self.sha, "temporary TTS bank")
        self.assertEqual(result["sha256"], self.sha)
        self.assertEqual(set(result["accepted_ids"]), tts_bank.LIVE_SOUND_IDS)
        self.assertEqual(robot.events[0], ("upload", "Upload sound", self.sha))
        self.assertEqual(robot.events[1], ("cmd", "GetVersion"))
        sweep = [e[1] for e in robot.events[2:]]
        self.assertEqual(sweep, [f"PlaySound {i}" for i in range(21)])

    def test_identity_mismatch_fails_verification(self):
        robot = FakeRobot(version="Software,9,9,999 SomeOtherBot")
        with self.assertRaises(RobotVerificationError):
            BankBurner(robot, sleep=FakeSleep()).burn_and_verify(
                self.bank, self.sha, "temporary TTS bank")

    def test_slot_map_mismatch_fails_verification(self):
        robot = FakeRobot(live_ids=tts_bank.LIVE_SOUND_IDS - {19})
        with self.assertRaisesRegex(RobotVerificationError, "slot map"):
            BankBurner(robot, sleep=FakeSleep()).burn_and_verify(
                self.bank, self.sha, "temporary TTS bank")

    def test_stabilization_wait_before_getversion(self):
        robot = FakeRobot()
        sleep = FakeSleep()
        BankBurner(robot, sleep=sleep, stabilize_seconds=5.0).burn_and_verify(
            self.bank, self.sha, "x")
        self.assertEqual(sleep.calls[0], 5.0)


class PlaybackTests(unittest.TestCase):
    def test_playback_is_duration_paced_one_command_per_slot(self):
        robot = FakeRobot()
        sleep = FakeSleep()
        segments = make_segments()
        result = BankBurner(robot, sleep=sleep).play_paced(
            segments, PlaybackProgress())
        self.assertFalse(result["stopped"])
        # strict alternation: PlaySound, full declared slot wait, next command
        self.assertEqual([e[1] for e in robot.events],
                         ["PlaySound 0", "PlaySound 1", "PlaySound 19"])
        self.assertEqual(sleep.calls,
                         [seg.slot_seconds for seg in segments])
        for command in robot.events:
            self.assertNotIn("-", command[1])  # never combined-ID syntax

    def test_progress_is_exposed_during_playback(self):
        progress = PlaybackProgress()
        seen = []
        sleep = FakeSleep(on_sleep=lambda s: seen.append(progress.snapshot()))
        BankBurner(FakeRobot(), sleep=sleep).play_paced(make_segments(), progress)
        self.assertEqual([s["current_slot"] for s in seen], [0, 1, 19])
        self.assertEqual(seen[0]["remaining_segments"], 3)
        self.assertEqual(progress.remaining_segments, 0)

    def test_stop_halts_between_segments(self):
        stop = threading.Event()
        sleep = FakeSleep(on_sleep=lambda s: stop.set())
        robot = FakeRobot()
        result = BankBurner(robot, sleep=sleep).play_paced(
            make_segments(), PlaybackProgress(), stop_event=stop)
        self.assertTrue(result["stopped"])
        self.assertEqual([e[1] for e in robot.events], ["PlaySound 0"])


class RestoreTests(unittest.TestCase):
    def setUp(self):
        self.bank = BMO_ARTIFACT.read_bytes()
        self.sha = tts_bank.BMO_BANK_SHA256

    def test_restore_rejects_tampered_artifact_without_robot_contact(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            tampered = Path(directory) / "bmo.bin"
            data = bytearray(self.bank)
            data[5000] ^= 0xFF
            tampered.write_bytes(data)
            robot = FakeRobot()
            with self.assertRaisesRegex(RestoreArtifactError, "hash"):
                BankBurner(robot, sleep=FakeSleep()).restore_bank(
                    tampered, self.sha, "persistent BMO bank")
            self.assertEqual(robot.events, [])

    def test_restore_rejects_wrong_size_artifact(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            short = Path(directory) / "bmo.bin"
            short.write_bytes(self.bank[:1000])
            with self.assertRaisesRegex(RestoreArtifactError, "bytes"):
                BankBurner(FakeRobot(), sleep=FakeSleep()).restore_bank(
                    short, self.sha, "persistent BMO bank")

    def test_full_operation_burns_speaks_then_auto_restores(self):
        robot = FakeRobot()
        report = run_speech_operation(
            BankBurner(robot, sleep=FakeSleep()), self.bank, self.sha,
            make_segments(), BMO_ARTIFACT, auto_restore=True)
        uploads = [e for e in robot.events if e[0] == "upload"]
        self.assertEqual(len(uploads), 2)  # temporary bank, then BMO restore
        self.assertEqual(uploads[1][2], tts_bank.BMO_BANK_SHA256)
        self.assertFalse(report["temporary_bank_installed"])
        self.assertEqual(set(report["restore"]["accepted_ids"]),
                         tts_bank.LIVE_SOUND_IDS)

    def test_restore_still_runs_after_playback_failure(self):
        # first 21 PlaySound calls are the post-burn sweep; call 22 is the
        # first real speech segment — make it die mid-playback.
        robot = FakeRobot(fail_playsound_call=22)
        progress = PlaybackProgress()
        report = run_speech_operation(
            BankBurner(robot, sleep=FakeSleep()), self.bank, self.sha,
            make_segments(), BMO_ARTIFACT, auto_restore=True,
            progress=progress)
        uploads = [e for e in robot.events if e[0] == "upload"]
        self.assertEqual(len(uploads), 2)
        self.assertIn("serial dropout", report["playback_error"])
        self.assertEqual(progress.state, "error")
        self.assertFalse(report["temporary_bank_installed"])

    def test_without_auto_restore_temporary_install_is_flagged_loudly(self):
        robot = FakeRobot()
        progress = PlaybackProgress()
        report = run_speech_operation(
            BankBurner(robot, sleep=FakeSleep()), self.bank, self.sha,
            make_segments(), BMO_ARTIFACT, auto_restore=False,
            progress=progress)
        uploads = [e for e in robot.events if e[0] == "upload"]
        self.assertEqual(len(uploads), 1)
        self.assertTrue(report["temporary_bank_installed"])
        self.assertEqual(progress.state, "temporary-installed")

    def test_stopped_playback_still_restores(self):
        stop = threading.Event()
        stop.set()
        robot = FakeRobot()
        report = run_speech_operation(
            BankBurner(robot, sleep=FakeSleep()), self.bank, self.sha,
            make_segments(), BMO_ARTIFACT, auto_restore=True, stop_event=stop)
        uploads = [e for e in robot.events if e[0] == "upload"]
        self.assertEqual(len(uploads), 2)
        self.assertTrue(report["playback"]["stopped"])
        self.assertFalse(report["temporary_bank_installed"])


class NoHardwareAccessTests(unittest.TestCase):
    def test_tts_bank_module_never_imports_serial(self):
        import neatobmo.tts_bank as module
        source = Path(module.__file__).read_text()
        self.assertNotIn("import serial", source)
        self.assertNotIn("usbmodem", source)


if __name__ == "__main__":
    unittest.main()
