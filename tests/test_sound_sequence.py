import json
import tempfile
import unittest
from pathlib import Path

from neato_sound_sequence import commands_for_sequence, delays_for_sequence, load_sequences


class SoundSequenceTest(unittest.TestCase):
    def test_loads_sequence_manifest_and_emits_single_id_commands(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / "sequences.json"
            manifest.write_text(json.dumps({"sequences": {"video_games": {"ids": [0, 1, 2]}}}))

            sequences = load_sequences(manifest)

        self.assertEqual(sequences["video_games"], [0, 1, 2])
        self.assertEqual(
            commands_for_sequence(sequences["video_games"]),
            ["PlaySound 0", "PlaySound 1", "PlaySound 2"],
        )
        self.assertEqual(delays_for_sequence([0, 1, 2]), [2.881995, 1.867392])
        self.assertEqual(delays_for_sequence([0, 1, 2], 0.25), [0.25, 0.25])


if __name__ == "__main__":
    unittest.main()
