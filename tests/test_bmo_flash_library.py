import hashlib
import json
import unittest
from pathlib import Path

from neatobmo import tts_bank
from tools import build_bmo_flash_library as builder
from tools import esp32_web_simulator as simulator


ROOT = Path(__file__).resolve().parents[1]
PUBLISH = ROOT / "docs" / "bmo-soundboard"
BASELINE = (ROOT / "assets" / "bmo-sound-bank-offline-20260810"
            / "DfltSoundLib.BMO.pcm-only.Rev1.0.bin")


class PublishedFlashLibraryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = json.loads((PUBLISH / "catalog.json").read_text())
        cls.baseline = BASELINE.read_bytes()
        cls.records = {record.sound_id: record for record in
                       tts_bank.record_ranges_from_bytes(cls.baseline)}

    def test_catalog_has_every_imported_sound(self):
        self.assertEqual(self.catalog["sound_count"], 230)
        self.assertEqual(self.catalog["unique_sound_count"], 224)
        self.assertEqual(len(self.catalog["sounds"]), 230)
        self.assertEqual(len({sound["key"] for sound in self.catalog["sounds"]}), 230)

    def test_publication_contains_only_catalog_and_precompiled_modules(self):
        files = [path for path in PUBLISH.rglob("*") if path.is_file()]
        self.assertEqual(sum(path.name == "catalog.json" for path in files), 1)
        self.assertEqual(sum(path.suffix == ".bin" for path in files), 36)
        self.assertTrue(all(path.name == "catalog.json" or path.suffix == ".bin"
                            for path in files))

    def test_every_module_is_exact_hashed_neato_image(self):
        for page in self.catalog["pages"]:
            payload = (PUBLISH / page["file"]).read_bytes()
            self.assertEqual(len(payload), tts_bank.EXPECTED_BANK_BYTES)
            self.assertEqual(payload[:2], b"KT")
            self.assertEqual(hashlib.sha256(payload).hexdigest(), page["sha256"])

    def test_modules_change_only_pcm_fields(self):
        pcm_ranges = sorted((record.pcm_offset,
                             record.pcm_offset + record.pcm_byte_count)
                            for record in self.records.values())
        for page in self.catalog["pages"]:
            payload = (PUBLISH / page["file"]).read_bytes()
            cursor = 0
            for start, end in pcm_ranges:
                self.assertEqual(payload[cursor:start], self.baseline[cursor:start])
                cursor = end
            self.assertEqual(payload[cursor:], self.baseline[cursor:])

    def test_sound_segments_stay_inside_their_slots(self):
        for sound in self.catalog["sounds"]:
            self.assertEqual(sound["slots"], [s["slot"] for s in sound["segments"]])
            for segment in sound["segments"]:
                record = self.records[segment["slot"]]
                self.assertEqual(segment["pcm_offset"], record.pcm_offset)
                self.assertLessEqual(segment["content_bytes"], record.pcm_byte_count)
                self.assertEqual(segment["slot_bytes"], record.pcm_byte_count)

    def test_reviewed_board_clips_exclude_the_reported_lead_ins(self):
        sounds = {sound["key"]: sound for sound in self.catalog["sounds"]}
        games = sounds["101-28055268-who-wants-to-play-video-games"]
        love = sounds["101-28062487-i-love-you"]
        hello = sounds["101-24061886-bmo-hello"]
        bounce = sounds["101-28054910-bmo-always-bounces-back"]
        butt = sounds["101-24061869-bmobutt"]
        self.assertEqual(games["editorial_trim_start_seconds"], 2.25)
        self.assertLess(games["content_seconds"], 3.5)
        self.assertEqual(love["editorial_trim_start_seconds"], 1.57)
        self.assertLess(love["content_seconds"], 1.8)
        self.assertEqual(hello["editorial_trim_start_seconds"], 1.48)
        self.assertLess(hello["content_seconds"], 1.5)
        self.assertEqual(bounce["editorial_trim_start_seconds"], 2.02)
        self.assertLess(bounce["content_seconds"], 2.0)
        self.assertEqual(butt["label"], "It goes in my butt")

    def test_automatic_leadin_audit_edits_are_embedded_in_modules(self):
        sounds = {sound["key"]: sound for sound in self.catalog["sounds"]}
        expected = {
            "101-28055080-bmo-stop": 1.975,
            "101-28055166-mmm-hmm": 1.795,
            "101-28055317-bmo-chop": 1.86,
            "101-28055358-yaay-bmo": 1.933,
            "101-28055646-funnymoney": 1.175,
            "101-28069416-hello-family": 1.175,
        }
        for key, trim_start in expected.items():
            self.assertEqual(sounds[key]["editorial_trim_start_seconds"],
                             trim_start)


class BuilderTests(unittest.TestCase):
    def test_best_slots_prefers_fewest_then_least_waste(self):
        free = {0: 100, 1: 70, 2: 40}
        self.assertEqual(builder.best_slots(free, 65), (1,))
        self.assertEqual(builder.best_slots(free, 105), (1, 2))


class SimulatorValidationTests(unittest.TestCase):
    def test_simulator_renders_embedded_ui_with_local_config(self):
        page = simulator.render_index("127.0.0.1:8765").decode()
        self.assertIn("window.BMO_SIMULATOR=true", page)
        self.assertIn(
            "http://127.0.0.1:8765/bmo-soundboard/catalog.json", page)
        self.assertIn("function playRobotSound", page)

    def test_valid_catalog_request(self):
        sound = json.loads((PUBLISH / "catalog.json").read_text())["sounds"][0]
        simulator.validate_request({
            "url": "http://127.0.0.1/bmo-soundboard/modules/module.bin",
            "sha256": sound["module_sha256"], "key": sound["key"],
            "slots": sound["slots"], "slot_seconds": sound["slot_seconds"],
        })

    def test_rejects_non_http_module_url(self):
        with self.assertRaisesRegex(ValueError, "HTTP"):
            simulator.validate_request({
                "url": "file:///tmp/module.bin", "sha256": "a" * 64,
                "key": "x", "slots": [0], "slot_seconds": [1.0],
            })

    def test_rejects_remote_module_by_default(self):
        with self.assertRaisesRegex(ValueError, "remote module"):
            simulator.validate_request({
                "url": "https://example.com/bmo-soundboard/modules/module.bin",
                "sha256": "a" * 64, "key": "x", "slots": [0],
                "slot_seconds": [1.0],
            })


if __name__ == "__main__":
    unittest.main()
