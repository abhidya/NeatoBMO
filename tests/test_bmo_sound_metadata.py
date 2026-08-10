import json
import unittest
from pathlib import Path

from neatobmo.sounds import BMO_BANK, BMO_SEQUENCES, BMO_SOUND_SLOTS, LIVE_SOUND_IDS, SOUNDS
from neatobmo.tts_bank import BANK_PROFILES, bank_profiles_payload


class BmoSoundMetadataTests(unittest.TestCase):
    def test_catalog_exactly_covers_live_installed_slots(self):
        self.assertEqual(set(BMO_SOUND_SLOTS), LIVE_SOUND_IDS)
        self.assertEqual(BMO_BANK["sha256"],
                         "9d3d82d9275c03fa9f2abb163cdfd9393445737999916f6337d2d6b639b51159")
        json.dumps({"bank": BMO_BANK, "slots": BMO_SOUND_SLOTS,
                    "sequences": BMO_SEQUENCES})

    def test_sequences_only_use_live_slots(self):
        for sequence in BMO_SEQUENCES.values():
            self.assertTrue(sequence["ids"])
            self.assertLessEqual(set(sequence["ids"]), LIVE_SOUND_IDS)

    def test_runtime_names_and_website_use_bmo_catalog(self):
        self.assertEqual(SOUNDS["hello"], 7)
        router = Path("bmo_web.py").read_text()
        self.assertIn('self.path == "/sounds"', router)
        self.assertIn('self.path == "/sound-sequence"', router)
        self.assertIn('self.path == "/sound-bank-install"', router)
        self.assertNotIn(">Beep</button>", Path("static/console.html").read_text())

    def test_web_flash_profiles_are_exact_hash_allowlist(self):
        self.assertEqual(set(BANK_PROFILES), {"bmo", "original"})
        profiles = bank_profiles_payload()
        self.assertTrue(all(profile["available"] for profile in profiles.values()))
        self.assertEqual(profiles["bmo"]["confirmation"], "INSTALL BMO")
        self.assertEqual(profiles["original"]["confirmation"], "RESTORE ORIGINAL")
        self.assertEqual(profiles["bmo"]["bytes"], 770048)
        self.assertEqual(profiles["original"]["bytes"], 770048)


if __name__ == "__main__":
    unittest.main()
