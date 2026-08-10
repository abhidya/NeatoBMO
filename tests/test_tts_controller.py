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
        self.assertEqual(robot.events[0], ("cmd", "GetVersion"))  # pre-burn
        self.assertEqual(robot.events[1], ("upload", "Upload sound", self.sha))
        self.assertEqual(robot.events[2], ("cmd", "GetVersion"))  # post-burn
        sweep = [e[1] for e in robot.events[3:]]
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

    def test_verification_polls_instead_of_fixed_sleep(self):
        # robot answers immediately: no stabilization sleep at all
        robot = FakeRobot()
        sleep = FakeSleep()
        BankBurner(robot, sleep=sleep).burn_and_verify(self.bank, self.sha, "x")
        self.assertNotIn(5.0, sleep.calls)

    def test_verification_retries_until_robot_returns(self):
        class SlowRobot(FakeRobot):
            def __init__(self):
                super().__init__()
                self.version_calls = 0

            def cmd(self, text, timeout=None):
                if text == "GetVersion":
                    self.version_calls += 1
                    # pre-burn check answers; after the burn the robot needs
                    # three polls before it identifies again
                    if 1 < self.version_calls < 4:
                        return ""
                return super().cmd(text, timeout)

        robot = SlowRobot()
        sleep = FakeSleep()
        BankBurner(robot, sleep=sleep).burn_and_verify(self.bank, self.sha, "x")
        self.assertEqual(sleep.calls.count(tts_bank.VERIFY_POLL_SECONDS), 2)

    def test_verification_gives_up_after_ceiling(self):
        class DeadAfterBurn(FakeRobot):
            def __init__(self):
                super().__init__()
                self.version_calls = 0

            def cmd(self, text, timeout=None):
                if text == "GetVersion":
                    self.version_calls += 1
                    if self.version_calls > 1:
                        return ""
                return super().cmd(text, timeout)

        with self.assertRaisesRegex(RobotVerificationError, "identity check"):
            BankBurner(DeadAfterBurn(), sleep=FakeSleep()).burn_and_verify(
                self.bank, self.sha, "x")


class PlaybackTests(unittest.TestCase):
    def test_playback_is_duration_paced_one_command_per_slot(self):
        robot = FakeRobot()
        sleep = FakeSleep()
        segments = make_segments()
        result = BankBurner(robot, sleep=sleep).play_paced(
            segments, PlaybackProgress())
        self.assertFalse(result["stopped"])
        # strict alternation: PlaySound, content-length wait, next command
        # (padding is silence, so interrupting it is inaudible — pacing waits
        # content + margin, not the declared slot length)
        self.assertEqual([e[1] for e in robot.events],
                         ["PlaySound 0", "PlaySound 1", "PlaySound 19"])
        self.assertEqual(
            sleep.calls,
            [seg.content_seconds + tts_bank.PLAYBACK_MARGIN_SECONDS
             for seg in segments])
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


class SilentModeAndAutoSpeechTests(unittest.TestCase):
    """The automatic path: no audible sweep, chunked speech, persistence."""

    @classmethod
    def setUpClass(cls):
        cls.baseline = BMO_ARTIFACT.read_bytes()

    def make_chunks(self, count=2):
        from array import array
        records = {r.sound_id: r for r in
                   tts_bank.record_ranges_from_bytes(self.baseline)}
        capacities = [(sid, records[sid].sample_count)
                      for sid in tts_bank.SLOT_SEQUENCE]
        chunks = []
        for i in range(count):
            samples = array("h", [1000 + i] * 4000)
            segments = tts_bank.plan_segments(samples, capacities)
            tts_bank.attach_text_fragments(segments, f"chunk {i}")
            chunks.append({"text": f"chunk {i}", "samples": samples,
                           "segments": segments})
        return chunks

    def test_silent_burn_skips_the_audible_sweep(self):
        robot = FakeRobot()
        result = BankBurner(robot, sleep=FakeSleep()).burn_and_verify(
            self.baseline, tts_bank.BMO_BANK_SHA256, "chunk", sweep=False)
        self.assertIsNone(result["accepted_ids"])
        commands = [e[1] for e in robot.events if e[0] == "cmd"]
        self.assertEqual(commands, ["GetVersion", "GetVersion"])  # no sounds

    def test_playback_reply_verifies_slots_after_silent_burn(self):
        robot = FakeRobot(live_ids=tts_bank.LIVE_SOUND_IDS - {0})
        with self.assertRaisesRegex(RobotVerificationError, "slot 0"):
            BankBurner(robot, sleep=FakeSleep()).play_paced(
                make_segments(), PlaybackProgress())

    def test_speak_chunks_burns_and_speaks_each_chunk_in_order(self):
        robot = FakeRobot()
        progress = PlaybackProgress()
        chunks = self.make_chunks(2)
        report = tts_bank.speak_chunks_operation(
            BankBurner(robot, sleep=FakeSleep()), self.baseline, chunks,
            progress=progress)
        uploads = [e for e in robot.events if e[0] == "upload"]
        self.assertEqual(len(uploads), 2)  # one write per chunk, no restore
        self.assertTrue(report["persisted"])
        self.assertFalse(report["stopped"])
        self.assertEqual(report["installed_bank_sha256"],
                         report["chunks"][-1]["sha256"])
        self.assertEqual(progress.state, "spoken")
        self.assertEqual(progress.total_chunks, 2)
        # every chunk's bank differs from the BMO artifact (speech, not BMO)
        for chunk in report["chunks"]:
            self.assertNotEqual(chunk["sha256"], tts_bank.BMO_BANK_SHA256)

    def test_stop_between_chunks_halts_cleanly(self):
        import threading
        stop = threading.Event()
        stop.set()
        robot = FakeRobot()
        report = tts_bank.speak_chunks_operation(
            BankBurner(robot, sleep=FakeSleep()), self.baseline,
            self.make_chunks(2), stop_event=stop)
        self.assertTrue(report["stopped"])
        self.assertEqual([e for e in robot.events if e[0] == "upload"], [])

    def test_validation_failure_blocks_the_burn(self):
        # corrupt the baseline's directory so every build fails validation
        robot = FakeRobot()
        chunks = self.make_chunks(1)
        bad_baseline = bytearray(self.baseline)
        bad_baseline[300] ^= 0xFF  # inside page 0, outside the slot table

        class TamperedBuild(dict):
            pass

        original_build = tts_bank.build_tts_bank

        def tampered_build(baseline, segments, unused, **kwargs):
            built, manifest = original_build(baseline, segments, unused, **kwargs)
            tampered = bytearray(built)
            tampered[300] ^= 0xFF  # directory byte: validation must fail
            return bytes(tampered), manifest

        tts_bank.build_tts_bank, saved = tampered_build, tts_bank.build_tts_bank
        try:
            with self.assertRaises(tts_bank.BankValidationError):
                tts_bank.speak_chunks_operation(
                    BankBurner(robot, sleep=FakeSleep()), self.baseline, chunks)
        finally:
            tts_bank.build_tts_bank = saved
        self.assertEqual([e for e in robot.events if e[0] == "upload"], [])


class StreamingPipelineTests(unittest.TestCase):
    """pack_audio_chunks + skip-identical-burn: the low-latency path."""

    @classmethod
    def setUpClass(cls):
        cls.baseline = BMO_ARTIFACT.read_bytes()
        records = {r.sound_id: r for r in
                   tts_bank.record_ranges_from_bytes(cls.baseline)}
        cls.capacities = [(sid, records[sid].sample_count)
                          for sid in tts_bank.SLOT_SEQUENCE]

    @staticmethod
    def synth_seconds(seconds_per_word):
        from array import array

        def synth(text):
            n = int(len(text.split()) * seconds_per_word
                    * tts_bank.PCM_SAMPLE_RATE)
            return array("h", [1000] * n)
        return synth

    def test_eager_first_chunk_flushes_on_first_sentence(self):
        chunks = list(tts_bank.pack_audio_chunks(
            ["First one.", "Second one.", "Third one."],
            self.synth_seconds(0.4), self.capacities))
        self.assertEqual(chunks[0]["text"], "First one.")
        self.assertEqual(" ".join(c["text"] for c in chunks),
                         "First one. Second one. Third one.")

    def test_later_chunks_pack_maximally(self):
        # 6 sentences x 2 words x 2s = 4s each; after the eager first chunk,
        # ~4 sentences (incl. gaps) fit per bank
        units = [f"s{i} words." for i in range(6)]
        chunks = list(tts_bank.pack_audio_chunks(
            units, self.synth_seconds(2.0), self.capacities))
        self.assertEqual(chunks[0]["text"], units[0])
        self.assertGreater(len(chunks[1]["text"].split(".")[0:-1]), 1)
        self.assertEqual(" ".join(c["text"] for c in chunks), " ".join(units))

    def test_live_generator_units_are_consumed_lazily(self):
        synth_calls = []
        base = self.synth_seconds(0.5)

        def synth(text):
            synth_calls.append(text)
            return base(text)

        def units():
            yield "One two."
            yield "Three four."

        gen = tts_bank.pack_audio_chunks(units(), synth, self.capacities)
        first = next(gen)
        self.assertEqual(first["text"], "One two.")
        self.assertEqual(synth_calls, ["One two."])  # second not synthesized yet
        rest = list(gen)
        self.assertEqual(rest[0]["text"], "Three four.")

    def test_overlong_final_sentence_word_splits_and_drains(self):
        units = ["short one.", " ".join(f"w{i}" for i in range(40))]
        chunks = list(tts_bank.pack_audio_chunks(
            units, self.synth_seconds(1.0), self.capacities))
        joined = " ".join(c["text"] for c in chunks)
        self.assertEqual(joined.split()[-1], "w39")  # nothing lost at the end

    def test_identical_bank_skips_the_flash_write(self):
        from array import array
        samples = array("h", [1000] * 4000)
        segments = tts_bank.plan_segments(samples, self.capacities)
        tts_bank.attach_text_fragments(segments, "same words")
        chunk = {"text": "same words", "samples": samples,
                 "segments": segments}
        robot = FakeRobot()
        burner = BankBurner(robot, sleep=FakeSleep())
        first = tts_bank.speak_chunks_operation(
            burner, self.baseline, [chunk])
        uploads = [e for e in robot.events if e[0] == "upload"]
        self.assertEqual(len(uploads), 1)
        second = tts_bank.speak_chunks_operation(
            burner, self.baseline, [chunk],
            installed_sha256=first["installed_bank_sha256"])
        uploads = [e for e in robot.events if e[0] == "upload"]
        self.assertEqual(len(uploads), 1)  # no second write
        self.assertEqual(second["reused_burns"], 1)
        self.assertEqual(second["installed_bank_sha256"],
                         first["installed_bank_sha256"])
        # but it still spoke
        plays = [e for e in robot.events if e[0] == "cmd"
                 and e[1].startswith("PlaySound")]
        self.assertGreaterEqual(len(plays), 2)


class BridgeRelayTests(unittest.TestCase):
    def test_bridge_only_relays_sound_bank_uploads(self):
        from neatobmo.transport import BridgeTransport, BinaryTransferError
        bridge = BridgeTransport.__new__(BridgeTransport)
        bridge.host = "192.0.2.1"
        with self.assertRaisesRegex(BinaryTransferError, "only relays"):
            bridge.send_binary("PlaySound File", b"\x00" * 64)


class NoHardwareAccessTests(unittest.TestCase):
    def test_tts_bank_module_never_imports_serial(self):
        import neatobmo.tts_bank as module
        source = Path(module.__file__).read_text()
        self.assertNotIn("import serial", source)
        self.assertNotIn("usbmodem", source)


if __name__ == "__main__":
    unittest.main()
