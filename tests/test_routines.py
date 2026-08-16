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


class TestTurnPlanning(unittest.TestCase):
    def test_single_local_request_has_no_residual(self):
        plan = turns.plan_turn("what time is it")
        self.assertEqual([step.routine for step in plan.routines], ["time"])
        self.assertEqual(plan.residual, "")
        self.assertFalse(plan.requires_brain)

    def test_two_local_requests_keep_spoken_order(self):
        plan = turns.plan_turn("check your battery and what time is it")
        self.assertEqual([step.routine for step in plan.routines],
                         ["battery", "time"])
        self.assertFalse(plan.requires_brain)

    def test_local_plus_open_question_preserves_residual(self):
        plan = turns.plan_turn("what time is it and why is the sky blue")
        self.assertEqual([step.routine for step in plan.routines], ["time"])
        self.assertEqual(plan.residual, "why is the sky blue")
        self.assertTrue(plan.requires_brain)

    def test_punctuation_connector_does_not_pollute_residual(self):
        plan = turns.plan_turn("what time is it, and why is the sky blue?")
        self.assertEqual([step.routine for step in plan.routines], ["time"])
        self.assertEqual(plan.residual, "why is the sky blue")

    def test_open_question_before_local_preserves_residual(self):
        plan = turns.plan_turn("why is the sky blue and what time is it")
        self.assertEqual([step.routine for step in plan.routines], ["time"])
        self.assertEqual(plan.residual, "why is the sky blue")

    def test_wake_phrase_and_fillers_do_not_create_residual(self):
        plan = turns.plan_turn("hey BMO, please tell me the time")
        self.assertEqual([step.routine for step in plan.routines], ["time"])
        self.assertEqual(plan.residual, "")

    def test_ambiguous_and_phrase_escalates_losslessly(self):
        plan = turns.plan_turn("sing rock and roll")
        self.assertEqual(plan.routines, ())
        self.assertEqual(plan.residual, "sing rock and roll")

    def test_planning_does_not_execute_or_consume_followup_state(self):
        rotation_before = dict(routines._rotation)
        state = routines.ConvoState()
        state.arm("game_choice")

        plan = turns.plan_turn("what time is it", state)

        self.assertEqual([step.routine for step in plan.routines], ["time"])
        self.assertEqual(state.pending(), "game_choice")
        self.assertEqual(routines._rotation, rotation_before)

    def test_existing_game_phrase_remains_an_instant_routine(self):
        plan = turns.plan_turn("let's play a game!")
        self.assertEqual([step.routine for step in plan.routines], ["game"])
        self.assertFalse(plan.requires_brain)

    def test_existing_routine_variants_remain_local(self):
        cases = {
            "miss you": "love",
            "tell me a joke instead": "joke",
        }
        for utterance, expected in cases.items():
            with self.subTest(utterance=utterance):
                plan = turns.plan_turn(utterance)
                self.assertEqual([step.routine for step in plan.routines],
                                 [expected])
                self.assertFalse(plan.requires_brain)

    def test_pending_followup_is_planned_purely_then_executed_once(self):
        state = routines.ConvoState()
        state.arm("game_choice")

        plan = turns.plan_turn("the racing one!", state)

        self.assertEqual([step.routine for step in plan.routines],
                         ["game_choice:0"])
        self.assertEqual(state.pending(), "game_choice")
        hit = routines.run(plan.routines[0].routine, state, {})
        self.assertEqual(hit.routine, "game_choice")
        self.assertIn("racing", hit.reply.lower())
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
