"""Routine layer: instant intents, follow-up state machine, cue integrity."""
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from neatobmo import cues, routines, tts_bank
import neatobmo.turns as turns


class TestIntentMatching(unittest.TestCase):
    def test_greeting(self):
        hit = routines.match("hey bmo!", routines.ConvoState(), {})
        self.assertIsNotNone(hit)
        self.assertEqual(hit.routine, "greet")

    def test_dance_has_choreography(self):
        hit = routines.match("can you dance for me?", routines.ConvoState(), {})
        self.assertEqual(hit.routine, "dance")
        kinds = {k for k, _ in cues.parse(hit.reply).steps}
        self.assertIn("move", kinds)
        self.assertIn("sound", kinds)

    def test_unmatched_falls_through_to_brain(self):
        hit = routines.match("what is the meaning of life?",
                             routines.ConvoState(), {})
        self.assertIsNone(hit)

    def test_dynamic_time_reply(self):
        hit = routines.match("what time is it?", routines.ConvoState(), {})
        self.assertEqual(hit.routine, "time")
        self.assertRegex(hit.reply, r"\d{1,2}:\d{2}")

    def test_battery_without_body(self):
        hit = routines.match("how is your battery?", routines.ConvoState(),
                             {"robot": None})
        self.assertEqual(hit.routine, "battery")
        self.assertIn("tummy", hit.reply.lower())


class TestFollowUpStateMachine(unittest.TestCase):
    def test_game_two_turn_flow(self):
        state = routines.ConvoState()
        first = routines.match("let's play a game!", state, {})
        self.assertEqual(first.routine, "game")
        self.assertEqual(state.pending(), "game_choice")
        second = routines.match("the racing one!", state, {})
        self.assertEqual(second.routine, "game_choice")
        self.assertIn("racing", second.reply.lower())
        self.assertIsNone(state.pending())

    def test_follow_up_decline(self):
        state = routines.ConvoState()
        routines.match("wanna play?", state, {})
        second = routines.match("nah, not now", state, {})
        self.assertEqual(second.routine, "game_choice")
        self.assertIn("next time", second.reply.lower())

    def test_subject_change_clears_expectation(self):
        state = routines.ConvoState()
        routines.match("let's play a game", state, {})
        hit = routines.match("tell me a joke instead", state, {})
        self.assertEqual(hit.routine, "joke")
        self.assertIsNone(state.pending())

    def test_expectation_expires(self):
        state = routines.ConvoState()
        state.arm("game_choice", ttl=0.01)
        time.sleep(0.05)
        self.assertIsNone(state.pending())


class TestCannedCorpusIntegrity(unittest.TestCase):
    def test_every_canned_reply_parses_to_speech_and_cues(self):
        for text in routines.canned_texts():
            plan = cues.parse(text)
            self.assertTrue(plan.speech, f"no speech in {text!r}")
            self.assertTrue(plan.steps, f"no cues in {text!r}")
            speakable = tts_bank.sanitize_speech_text(plan.speech)
            self.assertTrue(speakable, f"unspeakable: {text!r}")
            self.assertNotIn("[", plan.display, f"unparsed cue in {text!r}")

    def test_canned_sound_cues_are_playable(self):
        for text in routines.canned_texts():
            for kind, name in cues.parse(text).steps:
                if kind == "sound":
                    self.assertIn(name, cues.SOUND_CUES, text)


if __name__ == "__main__":
    unittest.main()
