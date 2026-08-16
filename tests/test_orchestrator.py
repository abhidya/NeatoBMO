"""Tests for the deepened orchestrator seams: BurstBudget, BodyController,
and chat_turn wiring — all through their interfaces with fakes."""
import threading
import time
import unittest

from neatobmo import cues
from neatobmo.body import BodyController


class BurstBudgetTests(unittest.TestCase):
    def test_first_sentence_over_budget_is_hard_trimmed(self):
        budget = cues.BurstBudget(max_words=4)
        self.assertEqual(budget.feed("one two three four five six"),
                         "one two three four!")

    def test_whole_sentences_preferred(self):
        budget = cues.BurstBudget(max_words=4)
        self.assertEqual(budget.feed("Hello! Did BMO miss you today?"),
                         "Hello!")

    def test_later_sentences_rejected_once_full(self):
        budget = cues.BurstBudget(max_words=4)
        self.assertEqual(budget.feed("Hi friend!"), "Hi friend!")
        self.assertIsNone(budget.feed("Want to play video games now?"))

    def test_streaming_matches_condense_for_the_same_reply(self):
        # one policy owner: sentence-by-sentence feeding admits the same
        # words condense() keeps for the whole reply at once
        reply = "Hello friend! Did BMO miss you? Yes yes yes!"
        condensed = cues.condense(cues.parse(reply), max_words=4)
        budget = cues.BurstBudget(max_words=4)
        streamed = [s for s in
                    (budget.feed(x) for x in
                     ("Hello friend!", "Did BMO miss you?", "Yes yes yes!"))
                    if s]
        self.assertEqual(" ".join(streamed), condensed.speech)

    def test_fallback_sound_only_when_no_sound_cue(self):
        self.assertIsNone(
            cues.BurstBudget.fallback_sound([("sound", "hello")]))
        self.assertEqual(
            cues.BurstBudget.fallback_sound([("face", "sad")]), "beep")
        self.assertEqual(cues.BurstBudget.fallback_sound([], "so sad"),
                         "beep")


class FakeRobot:
    def __init__(self):
        self.calls = []

    def cmd(self, c, timeout=3.0):
        self.calls.append(c)
        return "OK"


class BodyControllerTests(unittest.TestCase):
    def test_detached_body_never_raises_on_run(self):
        body = BodyController()
        body.run(lambda r: r.cmd("PlaySound 1"))   # no robot: silently no-op
        self.assertFalse(body.attached)

    def test_call_raises_detached_and_returns_value_attached(self):
        body = BodyController()
        with self.assertRaisesRegex(RuntimeError, "not attached"):
            body.call(lambda r: r.cmd("GetVersion"))
        body.robot = FakeRobot()
        self.assertEqual(body.call(lambda r: r.cmd("GetVersion")), "OK")

    def test_run_serializes_with_call(self):
        body = BodyController(robot=FakeRobot())
        order = []
        started = threading.Event()
        release = threading.Event()

        def slow(r):
            order.append("bg")
            started.set()
            release.wait(2)
        body.run(slow)
        started.wait(2)
        release.set()
        body.call(lambda r: order.append("fg"))
        self.assertEqual(order, ["bg", "fg"])

    def test_run_swallows_exceptions(self):
        body = BodyController(robot=FakeRobot())
        done = threading.Event()

        def boom(r):
            done.set()
            raise RuntimeError("robot fell over")
        body.run(boom)
        self.assertTrue(done.wait(2))
        self.assertEqual(body.call(lambda r: "alive"), "alive")

    def test_try_call_gives_up_when_lock_held(self):
        body = BodyController(robot=FakeRobot())
        body._lock.acquire()
        try:
            self.assertFalse(body.try_call(lambda r: r.cmd("PlaySound 3"),
                                           lock_timeout=0.05))
        finally:
            body._lock.release()

    def test_emote_prefers_esp32_and_falls_back_to_usb(self):
        class FakeEsp32:
            def __init__(self, fail):
                self.fail = fail
                self.texts = []

            def emote(self, text):
                if self.fail:
                    raise RuntimeError("unreachable")
                self.texts.append(text)

        class CascadeRobot(FakeRobot):
            pass

        esp32 = FakeEsp32(fail=False)
        body = BodyController(robot=CascadeRobot(), esp32=esp32)
        body.emote("\U0001F600")
        for _ in range(50):
            if esp32.texts:
                break
            threading.Event().wait(0.02)
        self.assertEqual(esp32.texts, ["\U0001F600"])
        self.assertEqual(body.robot.calls, [])   # USB path untouched

        esp32b = FakeEsp32(fail=True)
        body_b = BodyController(robot=CascadeRobot(), esp32=esp32b)
        body_b.emote("\U0001F600")
        for _ in range(100):
            if body_b.robot.calls:
                break
            threading.Event().wait(0.05)
        self.assertIn("TestMode On", body_b.robot.calls)  # cascade ran on USB


class ChatTurnTests(unittest.TestCase):
    """chat_turn through the module seams, with every collaborator faked."""

    def setUp(self):
        import bmo_web
        self.web = bmo_web
        self._saved = (bmo_web.brain, bmo_web.body, bmo_web.speech,
                       bmo_web.convo_state)

    def tearDown(self):
        (self.web.brain, self.web.body, self.web.speech,
         self.web.convo_state) = self._saved

    def test_routine_hit_enters_brain_history(self):
        class FakeBrain:
            def __init__(self):
                self.remembered = []

            def remember(self, user, reply):
                self.remembered.append((user, reply))
        self.web.brain = FakeBrain()
        self.web.body = BodyController()      # detached: reactions no-op
        out = self.web.chat_turn("hello!", speak_on_robot=False)
        self.assertTrue(out["reply"])
        self.assertEqual(out.get("routine"), "greet")
        self.assertEqual(len(self.web.brain.remembered), 1)

    def test_brain_error_reports_cleanly(self):
        class DeadBrain:
            def chat(self, text):
                raise RuntimeError("olmoe is asleep")
        self.web.brain = DeadBrain()
        self.web.body = BodyController()
        out = self.web.chat_turn("please summarize entropy", False)
        self.assertEqual(out["reply"], "")
        self.assertIn("olmoe", out["error"])

    def test_compound_local_turn_finishes_without_brain_generation(self):
        class FakeBrain:
            def __init__(self):
                self.remembered = []

            def remember(self, user, reply):
                self.remembered.append((user, reply))

            def chat(self, text, **kwargs):
                raise AssertionError("local-only turn must not call the brain")

        self.web.brain = FakeBrain()
        self.web.body = BodyController()

        events = list(self.web.chat_events(
            "what time is it and check your battery", False))

        self.assertEqual([e["type"] for e in events], [
            "turn_started", "routine_result", "routine_result",
            "turn_completed",
        ])
        self.assertEqual([e["routine"] for e in events
                          if e["type"] == "routine_result"],
                         ["time", "battery"])
        self.assertFalse(events[-1]["brain_used"])
        self.assertEqual(len(self.web.brain.remembered), 1)

    def test_local_result_precedes_thinking_and_residual_brain_result(self):
        class FakeBrain:
            def __init__(self):
                self.calls = []

            def stream(self, text, on_sentence, **kwargs):
                self.calls.append((text, kwargs))
                on_sentence("Blue light scatters! [happy]")
                return "Blue light scatters! [happy]"

        self.web.brain = FakeBrain()
        self.web.body = BodyController()

        events = list(self.web.chat_events(
            "what time is it and why is the sky blue", False))

        self.assertEqual([e["type"] for e in events], [
            "turn_started", "routine_result", "brain_started",
            "brain_result", "turn_completed",
        ])
        original, options = self.web.brain.calls[0]
        self.assertEqual(original,
                         "what time is it and why is the sky blue")
        self.assertIn("why is the sky blue", options["prompt"])
        self.assertIn("It is", options["assistant_prefix"])
        self.assertTrue(events[-1]["brain_used"])
        self.assertFalse(events[-1]["partial"])

    def test_chat_events_emit_each_brain_sentence_incrementally(self):
        class FakeBrain:
            def stream(self, text, on_sentence, **kwargs):
                on_sentence("Blue light scatters! [happy]")
                on_sentence("That makes the sky blue! [party]")
                return ("Blue light scatters! [happy] "
                        "That makes the sky blue! [party]")

        self.web.brain = FakeBrain()
        self.web.body = BodyController()

        events = list(self.web.chat_events(
            "what time is it and why is the sky blue", False))

        results = [event for event in events
                   if event["type"] == "brain_result"]
        self.assertEqual([event["index"] for event in results], [0, 1])
        self.assertEqual([event["display"] for event in results], [
            "Blue light scatters! 😀", "That makes the sky blue! 🎉",
        ])

    def test_compound_robot_speaks_local_answer_before_brain_generation(self):
        class OrderedSpeech:
            def __init__(self):
                self.calls = []

            def speak(self, text, units=None):
                self.calls.append((text, units))
                return {"stop": threading.Event()}, None

            def active(self):
                return False

        speech = OrderedSpeech()

        class InspectingBrain:
            def stream(self, text, on_sentence, **kwargs):
                self.assert_local_started()
                on_sentence("The sky looks blue. [happy]")
                return "The sky looks blue. [happy]"

            @staticmethod
            def assert_local_started():
                assert speech.calls
                assert "It is" in speech.calls[0][0]
                assert speech.calls[0][1] is None

        self.web.brain = InspectingBrain()
        self.web.body = BodyController()
        self.web.speech = speech

        events = list(self.web.chat_events(
            "what time is it and why is the sky blue", True))

        self.assertEqual(events[1]["type"], "routine_result")
        self.assertEqual(events[2]["type"], "brain_started")
        self.assertTrue(events[-1]["spoke"])

    def test_sound_cue_does_not_replace_streamed_semantic_speech(self):
        class CapturingSpeech:
            def __init__(self):
                self.calls = []

            def speak(self, text, units=None):
                self.calls.append((text, units))
                return {"stop": threading.Event()}, None

        class CueBrain:
            def stream(self, text, on_sentence, **kwargs):
                on_sentence("Entropy increases. [sound:beep]")
                return "Entropy increases. [sound:beep]"

        old_mode = self.web.CFG.speech_mode
        self.web.CFG.speech_mode = "soundboard"
        try:
            speech = CapturingSpeech()
            self.web.brain = CueBrain()
            self.web.body = BodyController()
            self.web.speech = speech

            reply, streamed, err = self.web._streamed_reply("entropy")

            self.assertTrue(streamed)
            self.assertIsNone(err)
            self.assertEqual(reply, "Entropy increases. [sound:beep]")
            self.assertEqual(speech.calls, [("Entropy increases.", None)])
        finally:
            self.web.CFG.speech_mode = old_mode

    def test_spoken_soundboard_cue_suppresses_generated_stream_speech(self):
        class CapturingSpeech:
            def __init__(self):
                self.calls = []

            def active(self):
                return False

            def speak(self, text, units=None):
                self.calls.append((text, units))
                return {"stop": threading.Event()}, None

        class GreetingBrain:
            def stream(self, text, on_sentence, **kwargs):
                on_sentence("Hello, friend! [sound:hello] [happy]")
                return "Hello, friend! [sound:hello] [happy]"

        old_mode = self.web.CFG.speech_mode
        self.web.CFG.speech_mode = "soundboard"
        try:
            speech = CapturingSpeech()
            self.web.brain = GreetingBrain()
            self.web.body = BodyController()
            self.web.speech = speech

            reply, streamed, err = self.web._streamed_reply("hello")

            self.assertTrue(streamed)
            self.assertIsNone(err)
            self.assertIn("Hello, friend!", reply)
            self.assertEqual(speech.calls, [])
        finally:
            self.web.CFG.speech_mode = old_mode

    def test_catalog_hint_is_added_before_brain_generation(self):
        prompt = self.web._compound_prompt(
            "tell me that you love me", "tell me that you love me", "")
        self.assertIn("Authentic recorded BMO lines available", prompt)
        self.assertIn("i love you", prompt)

    def test_chat_to_catalog_to_esp32_uses_no_synth_or_flash(self):
        from neatobmo.speech import SpeechService

        class CatalogBrain:
            def stream(self, text, on_sentence, **kwargs):
                on_sentence("I love you")
                return "I love you"

        class Esp32:
            def __init__(self):
                self.wavs = []

            def speak(self, wav):
                self.wavs.append(wav)

            def emote(self, _text):
                pass

        board = self.web.soundboard_voice

        class Voice:
            default_voice = "bmo-rvc"
            soundboard = board

            def synth(self, _text, _voice=None):
                raise AssertionError("catalog hit reached synthetic voice")

        esp32 = Esp32()
        body = BodyController(robot=FakeRobot(), esp32=esp32)
        service = SpeechService(body, Voice())
        self.web.brain = CatalogBrain()
        self.web.body = body
        self.web.speech = service

        events = list(self.web.chat_events("tell me that you love me", True))
        deadline = time.time() + 2
        while not esp32.wavs and time.time() < deadline:
            time.sleep(0.01)

        self.assertTrue(events[-1]["spoke"])
        self.assertEqual(len(esp32.wavs), 1)
        self.assertTrue(esp32.wavs[0].startswith(b"RIFF"))
        self.assertEqual(service.flash_writes, 0)

    def test_residual_subject_change_clears_pending_followup(self):
        class FakeBrain:
            def chat(self, text, **kwargs):
                return "Entropy grows! [surprised]"

        self.web.brain = FakeBrain()
        self.web.body = BodyController()
        self.web.convo_state = self.web.routines.ConvoState()
        self.web.convo_state.arm("game_choice")

        out = self.web.chat_turn("explain entropy", False)

        self.assertIn("Entropy grows!", out["reply"])
        self.assertIsNone(self.web.convo_state.pending())

    def test_brain_failure_keeps_delivered_local_result(self):
        class DeadBrain:
            def __init__(self):
                self.remembered = []

            def stream(self, text, on_sentence, **kwargs):
                raise RuntimeError("brain asleep")

            def remember(self, user, reply):
                self.remembered.append((user, reply))

        self.web.brain = DeadBrain()
        self.web.body = BodyController()

        events = list(self.web.chat_events(
            "what time is it and explain entropy", False))

        self.assertEqual([event["type"] for event in events], [
            "turn_started", "routine_result", "brain_started", "turn_error",
            "turn_completed",
        ])
        self.assertTrue(events[-1]["partial"])
        self.assertIn("It is", events[-1]["reply"])
        self.assertEqual(len(self.web.brain.remembered), 1)

    def test_brain_only_failure_does_not_record_empty_assistant_turn(self):
        class DeadBrain:
            def __init__(self):
                self.remembered = []

            def stream(self, text, on_sentence, **kwargs):
                raise RuntimeError("brain asleep")

            def remember(self, user, reply):
                self.remembered.append((user, reply))

        self.web.brain = DeadBrain()
        self.web.body = BodyController()

        events = list(self.web.chat_events("explain entropy", False))

        self.assertTrue(events[-1]["partial"])
        self.assertEqual(events[-1]["reply"], "")
        self.assertEqual(self.web.brain.remembered, [])

    def test_robot_stream_failure_after_output_is_not_retried_invisibly(self):
        class PartialBrain:
            def __init__(self):
                self.chat_calls = 0
                self.remembered = []

            def stream(self, text, on_sentence, **kwargs):
                on_sentence("Partial answer! [happy]")
                raise RuntimeError("stream stopped")

            def chat(self, text, **kwargs):
                self.chat_calls += 1
                return "Hidden retry answer"

            def remember(self, user, reply):
                self.remembered.append((user, reply))

        class FakeSpeech:
            def speak(self, text, units=None):
                return {"stop": threading.Event()}, None

        self.web.brain = PartialBrain()
        self.web.body = BodyController()
        self.web.speech = FakeSpeech()

        events = list(self.web.chat_events("explain entropy", True))

        self.assertEqual([event["type"] for event in events], [
            "turn_started", "brain_started", "brain_result", "turn_error",
            "turn_completed",
        ])
        self.assertEqual(self.web.brain.chat_calls, 0)
        self.assertIn("Partial answer!", events[-1]["reply"])
        self.assertTrue(events[-1]["partial"])

    def test_legacy_robot_turn_returns_and_remembers_streamed_partial(self):
        class PartialBrain:
            def __init__(self):
                self.remembered = []

            def stream(self, text, on_sentence, **kwargs):
                on_sentence("Partial answer! [happy]")
                raise RuntimeError("stream stopped")

            def chat(self, text, **kwargs):
                raise AssertionError("must not retry after delivery")

            def remember(self, user, reply):
                self.remembered.append((user, reply))

        class FakeSpeech:
            def speak(self, text, units=None):
                return {"stop": threading.Event()}, None

        self.web.brain = PartialBrain()
        self.web.body = BodyController()
        self.web.speech = FakeSpeech()

        out = self.web.chat_turn("explain entropy", True)

        self.assertIn("Partial answer!", out["reply"])
        self.assertTrue(out["partial"])
        self.assertIn("stream stopped", out["error"])
        self.assertEqual(self.web.brain.remembered, [
            ("explain entropy", "Partial answer! [happy]"),
        ])

    def test_deferred_residual_speech_can_be_cancelled_before_launch(self):
        class WaitingSpeech:
            def __init__(self):
                self.busy = True
                self.calls = []

            def active(self):
                return self.busy

            def speak(self, text, units=None):
                self.calls.append((text, units))
                return {"stop": threading.Event()}, None

        class OneSentenceBrain:
            def stream(self, text, on_sentence, **kwargs):
                on_sentence("Residual answer.")
                return "Residual answer."

        speech = WaitingSpeech()
        self.web.brain = OneSentenceBrain()
        self.web.body = BodyController()
        self.web.speech = speech

        reply, streamed, err = self.web._streamed_reply(
            "compound", defer_speech=True)
        cancelled = self.web._cancel_deferred_speech()
        speech.busy = False
        threading.Event().wait(0.1)

        self.assertEqual(reply, "Residual answer.")
        self.assertTrue(streamed)
        self.assertIsNone(err)
        self.assertTrue(cancelled)
        self.assertEqual(speech.calls, [])


if __name__ == "__main__":
    unittest.main()
