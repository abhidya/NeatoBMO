import unittest
from pathlib import Path
from unittest import mock

from neatobmo.soundboard_voice import SoundboardVoice, normalize_phrase
from neatobmo.voice import VoiceSynth


ROOT = Path(__file__).resolve().parents[1]


class SoundboardVoiceTests(unittest.TestCase):
    def test_repository_catalog_contains_hundreds_of_prepared_clips(self):
        board = SoundboardVoice(ROOT / "docs/bmo-soundboard/catalog.json")
        self.assertGreaterEqual(board.count, 200)
        wav = board.synth("Hello there!")
        self.assertIsNotNone(wav)
        self.assertTrue(wav.startswith(b"RIFF"))

    def test_contraction_alias_and_intent_suggestions_reach_real_lines(self):
        board = SoundboardVoice(ROOT / "docs/bmo-soundboard/catalog.json")
        self.assertIsNotNone(board.synth("It is BMO time"))
        suggestions = board.suggestions("Tell me that you love me")
        self.assertIn("i love you", suggestions)

    def test_normalization_handles_bmo_catalog_spelling(self):
        self.assertEqual(normalize_phrase("Hello, BEEMO!"), "hello bmo")

    def test_catalog_clip_precedes_every_synthetic_engine(self):
        board = SoundboardVoice(ROOT / "docs/bmo-soundboard/catalog.json")
        synth = VoiceSynth("http://unused", soundboard=board)
        with mock.patch.object(synth, "_neural") as neural, \
                mock.patch.object(synth, "_colibri") as colibri, \
                mock.patch.object(synth, "_espeak") as espeak:
            wav = synth.synth("Hello there!")
            self.assertTrue(wav.startswith(b"RIFF"))
            neural.assert_not_called()
            colibri.assert_not_called()
            espeak.assert_not_called()

    def test_unknown_phrase_falls_through_to_neural_bmo(self):
        board = SoundboardVoice(ROOT / "docs/bmo-soundboard/catalog.json")
        synth = VoiceSynth("http://unused", soundboard=board)
        with mock.patch.object(synth, "_neural",
                               return_value=b"RIFFneural") as neural:
            self.assertEqual(synth.synth("A brand new sentence"),
                             b"RIFFneural")
            neural.assert_called_once()


if __name__ == "__main__":
    unittest.main()
