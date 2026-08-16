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

    def test_only_individually_reviewed_soundboard_imports_are_enabled(self):
        board = SoundboardVoice(ROOT / "docs/bmo-soundboard/catalog.json")
        self.assertEqual(board.count, 230)
        self.assertEqual(board.trusted_count, 228)
        self.assertEqual(board.quarantined_count, 2)
        self.assertEqual(board.pending_review_count, 0)
        self.assertEqual(board.rejected_count, 2)
        self.assertIsNotNone(board.resolve("I love you"))
        self.assertTrue(board.render_for_review("101-28062487-i-love-you")
                        .startswith(b"RIFF"))

    def test_vacuum_storage_context_retrieves_the_reviewed_butt_line(self):
        board = SoundboardVoice(ROOT / "docs/bmo-soundboard/catalog.json")
        sound = board.resolve("It goes in my butt")
        self.assertEqual(sound["key"], "101-24061869-bmobutt")
        self.assertIn("it goes in my butt",
                      board.suggestions("Where is your SSD brain?"))

    def test_sleepy_context_retrieves_reviewed_battery_shutdown_line(self):
        board = SoundboardVoice(ROOT / "docs/bmo-soundboard/catalog.json")
        self.assertIn("battery low shut down",
                      board.suggestions("BMO is sleepy and tired"))

    def test_rejected_qa_clips_cannot_resolve(self):
        board = SoundboardVoice(ROOT / "docs/bmo-soundboard/catalog.json")
        self.assertIsNone(board.resolve("BMO BMO"))
        self.assertEqual(board.rejected_keys, {
            "101-24002778-bmo", "101-24101202-bmo-bmo",
        })

    def test_official_clip_wins_over_unreviewed_transcript_collision(self):
        board = SoundboardVoice(ROOT / "docs/bmo-soundboard/catalog.json")
        sound = board.resolve("Who wants to play video games")
        self.assertEqual(sound["verification"],
                         "official-cartoon-network-beemo-app")
        self.assertTrue(sound["key"].startswith("official-"))

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

    def test_approved_module_is_prebuilt_verified_and_cached(self):
        board = SoundboardVoice(ROOT / "docs/bmo-soundboard/catalog.json")
        sound = board.resolve("Hello there")
        artifact = board.module_artifact(sound["key"])
        self.assertEqual(artifact["sha256"], sound["module_sha256"])
        self.assertEqual(artifact["payload"], artifact["path"].read_bytes())
        with mock.patch.object(Path, "read_bytes",
                               side_effect=AssertionError("cache miss")):
            self.assertIs(board.module_artifact(sound["key"])["payload"],
                          artifact["payload"])

    def test_rejected_module_cannot_be_downloaded_or_installed(self):
        board = SoundboardVoice(ROOT / "docs/bmo-soundboard/catalog.json")
        self.assertIsNone(board.module_artifact("101-24002778-bmo"))


if __name__ == "__main__":
    unittest.main()
