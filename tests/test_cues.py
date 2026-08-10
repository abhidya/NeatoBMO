"""Stage-cue parser: sloppy small-model outputs must land on real actions."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from neatobmo import cues


class TestParseCleanCues(unittest.TestCase):
    def test_prompt_example(self):
        p = cues.parse("Yay, you are back! [happy] [wiggle] "
                       "Who wants to play video games? [sound:videogames] [party]")
        self.assertEqual(p.steps, [("face", "happy"), ("move", "wiggle"),
                                   ("sound", "videogames"), ("face", "party")])
        self.assertEqual(p.speech, "Yay, you are back! "
                                   "Who wants to play video games?")
        self.assertIn("\U0001F600", p.display)   # happy emoji planted
        self.assertNotIn("[", p.display)

    def test_actions_excludes_faces(self):
        p = cues.parse("[happy] [wiggle] [sound:hello]")
        self.assertEqual(p.actions(), [("move", "wiggle"), ("sound", "hello")])


class TestSloppyModelOutput(unittest.TestCase):
    def test_asterisk_stage_directions(self):
        p = cues.parse("*wiggles* I love you!! *happy*")
        self.assertIn(("move", "wiggle"), p.steps)
        self.assertIn(("face", "happy"), p.steps)
        self.assertEqual(p.speech, "I love you!!")

    def test_fuzzy_and_alias_names(self):
        p = cues.parse("[laughs] That tickles! [dancing]")
        self.assertIn(("face", "laugh"), p.steps)
        self.assertIn(("move", "dance"), p.steps)

    def test_namespace_variants(self):
        p = cues.parse("[sfx: hello] hi! [face=happy]".replace("=", ":"))
        self.assertIn(("sound", "hello"), p.steps)
        self.assertIn(("face", "happy"), p.steps)

    def test_multi_cue_token(self):
        p = cues.parse("[happy, wiggle] let's go!")
        self.assertEqual(p.steps, [("face", "happy"), ("move", "wiggle")])

    def test_unknown_bracket_chatter_stripped(self):
        p = cues.parse("[thinks very hard] hmm, maybe!")
        # no token resolved; only the mood fallback ("!") adds a face
        self.assertEqual(p.steps, [("face", "happy")])
        self.assertEqual(p.speech, "hmm, maybe!")

    def test_multi_word_stage_direction_resolves_by_word(self):
        p = cues.parse("*wiggles excitedly* hi!")
        self.assertEqual(p.steps, [("move", "wiggle"), ("face", "happy")])
        self.assertEqual(p.speech, "hi!")

    def test_parentheses_never_resolve_by_word(self):
        # the parenthetical must not resolve as cues (words stay in speech);
        # only the mood fallback reads "love" off the sentence as a face
        p = cues.parse("I made it (with love and care) for you")
        self.assertEqual(p.steps, [("face", "love")])
        self.assertEqual(p.speech, "I made it (with love and care) for you")

    def test_real_parenthetical_speech_kept(self):
        p = cues.parse("I made this (just for you) today")
        self.assertEqual(p.steps, [])
        self.assertEqual(p.speech, "I made this (just for you) today")

    def test_sound_fuzzy(self):
        p = cues.parse("[sound:video games] [sound:bmo time]")
        self.assertEqual(p.steps, [("sound", "videogames"), ("sound", "bmotime")])

    def test_invented_sound_names_map_to_real_slots(self):
        # every invention observed in the live profiling run must now land
        p = cues.parse("[sound:welcome] [sound:party] [sound:music] "
                       "[sound:surprise] [startled]")
        self.assertEqual(p.steps[:3], [("sound", "hello"), ("sound", "yeah"),
                                       ("sound", "videogames")])
        self.assertIn(("face", "surprised"), p.steps)
        for name, target in cues.SOUND_ALIASES.items():
            self.assertIn(target, cues.SOUND_CUES, name)


class TestFallbacksAndCaps(unittest.TestCase):
    def test_emoji_fallback_when_no_face_cues(self):
        p = cues.parse("I love you! \U0001F60D\U0001F389 [wiggle]")
        self.assertIn(("face", "love"), p.steps)
        self.assertIn(("face", "party"), p.steps)
        self.assertIn(("move", "wiggle"), p.steps)

    def test_cue_faces_suppress_emoji_double_count(self):
        p = cues.parse("[happy] \U0001F622")
        # emoji fallback only fires when no face cues at all
        self.assertEqual([s for s in p.steps if s[0] == "face"],
                         [("face", "happy")])

    def test_move_cap(self):
        p = cues.parse("[wiggle] [dance] [spin] [wiggle]")
        self.assertEqual(len([s for s in p.steps if s[0] == "move"]),
                         cues.MAX_MOVES)

    def test_plain_text_gets_a_mood_face(self):
        # no cues, no emojis: the mood fallback keeps the face alive
        p = cues.parse("Hello friend, how are you today?")
        self.assertEqual(p.steps, [("face", "love")])
        self.assertEqual(p.speech, "Hello friend, how are you today?")

    def test_flat_text_stays_stepless(self):
        p = cues.parse("The weather report said rain.")
        self.assertEqual(p.steps, [])

    def test_consecutive_duplicate_faces_collapse(self):
        p = cues.parse("[sleepy] [sleepy] so cozy")
        self.assertEqual(p.steps, [("face", "sleepy")])

    def test_distinct_faces_do_not_collapse(self):
        p = cues.parse("[happy] [sad] mood swings")
        self.assertEqual(p.steps, [("face", "happy"), ("face", "sad")])


class TestVocabularyIntegrity(unittest.TestCase):
    def test_all_faces_have_emoji(self):
        for name in cues.FACE_NAMES:
            self.assertIn(name, cues.FACE_EMOJI)

    def test_face_emojis_round_trip_through_emote(self):
        from neatobmo import emote
        for name, e in cues.FACE_EMOJI.items():
            self.assertEqual(emote.EMOJI_FACES.get(e), name)

    def test_sound_cues_resolve_to_playable(self):
        from neatobmo.sounds import BMO_SEQUENCES, SOUNDS
        for key in cues.SOUND_CUES.values():
            if key.startswith("seq:"):
                self.assertIn(key[4:], BMO_SEQUENCES)
            else:
                self.assertIn(key, SOUNDS)

    def test_moves_resolve_to_behaviors(self):
        from neatobmo import behaviors
        for fn in cues.MOVES.values():
            self.assertTrue(callable(getattr(behaviors, fn)))


if __name__ == "__main__":
    unittest.main()
