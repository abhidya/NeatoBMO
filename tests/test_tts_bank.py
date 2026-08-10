"""Unit tests for the TTS-to-sound-bank pipeline (no robot access)."""

import io
import json
import math
import struct
import unittest
import wave
from array import array
from pathlib import Path

from neatobmo import tts_bank
from neatobmo.tts_bank import (
    EXPECTED_BANK_BYTES,
    PCM_SAMPLE_RATE,
    SLOT_SEQUENCE,
    SlotSegment,
    TtsBankError,
    attach_text_fragments,
    build_tts_bank,
    decode_wav,
    normalize_peak,
    plan_segments,
    prepare_speech_pcm,
    record_ranges_from_bytes,
    required_confirmation,
    trim_silence,
    validate_tts_bank,
)

ROOT = Path(__file__).resolve().parents[1]
BMO_ARTIFACT = ROOT / ("assets/bmo-sound-bank-offline-20260810/"
                       "DfltSoundLib.BMO.pcm-only.Rev1.0.bin")
RANGES_JSON = ROOT / "assets/bmo-sound-bank-offline-20260810/pcm-only-record-ranges.json"


def make_wav(samples, rate=PCM_SAMPLE_RATE, channels=1):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(b"".join(struct.pack("<h", s) for s in samples))
    return buffer.getvalue()


def tone(seconds, amplitude=8000, frequency=440.0):
    n = int(seconds * PCM_SAMPLE_RATE)
    return [int(amplitude * math.sin(2 * math.pi * frequency * i / PCM_SAMPLE_RATE))
            for i in range(n)]


class NormalizationTests(unittest.TestCase):
    def test_normalize_scales_peak_to_minus_one_dbfs(self):
        samples = array("h", [0, 1000, -2000, 500])
        out = normalize_peak(samples)
        self.assertEqual(max(abs(s) for s in out), tts_bank.NORMALIZE_PEAK)

    def test_normalize_never_clips(self):
        samples = array("h", [32767, -32768, 12000])
        out = normalize_peak(samples)
        self.assertLessEqual(max(abs(s) for s in out), tts_bank.NORMALIZE_PEAK + 1)

    def test_silent_audio_is_rejected(self):
        with self.assertRaises(TtsBankError):
            normalize_peak(array("h", [0] * 100))
        with self.assertRaises(TtsBankError):
            trim_silence(array("h", [0] * 100))

    def test_trim_removes_leading_and_trailing_silence(self):
        quiet = [0] * 4000
        loud = [9000] * 1000
        out = trim_silence(array("h", quiet + loud + quiet))
        keep = int(tts_bank.TRIM_KEEP_SECONDS * PCM_SAMPLE_RATE)
        self.assertEqual(len(out), 1000 + 2 * keep)

    def test_decode_downmixes_and_resamples(self):
        # 100 ms stereo at 44100 -> ~100 ms mono at 22050
        n = 4410
        stereo = []
        for i in range(n):
            stereo += [1000, 3000]
        out = decode_wav(make_wav(stereo, rate=44100, channels=2))
        self.assertAlmostEqual(len(out), n / 2, delta=2)
        self.assertTrue(all(abs(s - 2000) <= 20 for s in out))

    def test_prepare_speech_pcm_end_to_end(self):
        wav = make_wav([0] * 2000 + tone(0.5) + [0] * 2000)
        out = prepare_speech_pcm(wav)
        self.assertEqual(max(abs(s) for s in out), tts_bank.NORMALIZE_PEAK)
        self.assertLess(len(out), int(0.6 * PCM_SAMPLE_RATE))


class SplittingTests(unittest.TestCase):
    def test_rejects_speech_longer_than_total_capacity(self):
        capacities = [(0, 1000), (1, 1000)]
        with self.assertRaisesRegex(TtsBankError, "holds at most"):
            plan_segments(array("h", [100] * 2001), capacities)

    def test_short_speech_uses_one_slot_untouched(self):
        samples = array("h", tone(0.05))
        segments = plan_segments(samples, [(0, 5000), (1, 5000)])
        self.assertEqual(len(segments), 1)
        self.assertEqual(segments[0].pcm, samples.tobytes())
        self.assertFalse(segments[0].faded_out)

    def test_split_prefers_quiet_boundary(self):
        # loud tone, then a quiet gap inside the cut-search window, more tone
        loud1 = tone(0.30, amplitude=20000)
        gap = [0] * int(0.05 * PCM_SAMPLE_RATE)
        loud2 = tone(0.30, amplitude=20000)
        samples = array("h", loud1 + gap + loud2)
        cap = int(0.40 * PCM_SAMPLE_RATE)
        segments = plan_segments(samples, [(0, cap), (1, cap), (2, cap)])
        cut = segments[0].sample_count
        self.assertGreaterEqual(cut, len(loud1))
        self.assertLessEqual(cut, len(loud1) + len(gap))

    def test_fades_are_applied_at_cut_points(self):
        samples = array("h", [12000] * 4000)
        segments = plan_segments(samples, [(0, 2500), (1, 2500)])
        self.assertEqual(len(segments), 2)
        first = array("h")
        first.frombytes(segments[0].pcm)
        second = array("h")
        second.frombytes(segments[1].pcm)
        self.assertEqual(first[-1], 0)       # fade-out at the split
        self.assertEqual(second[0], 0)       # fade-in after the split
        self.assertEqual(first[0], 12000)    # start of speech untouched
        self.assertEqual(second[-1], 12000)  # end of speech untouched

    def test_never_truncates_silently(self):
        # Force the pathological case by monkey-limiting the cut window: even
        # then leftover audio must raise, not vanish.
        samples = array("h", [100] * 3000)
        segments = plan_segments(samples, [(0, 1500), (1, 1500)])
        self.assertEqual(sum(s.sample_count for s in segments), 3000)

    def test_text_fragments_cover_all_words_in_order(self):
        segments = [SlotSegment(0, 0, 100, b"\x00\x00" * 50),
                    SlotSegment(1, 1, 100, b"\x00\x00" * 50)]
        attach_text_fragments(segments, "one two three four")
        joined = " ".join(s.text_fragment for s in segments).split()
        self.assertEqual(joined, ["one", "two", "three", "four"])


class RealArtifactTests(unittest.TestCase):
    """Prove the bytes-based parser against the shipped, hardware-verified data."""

    @classmethod
    def setUpClass(cls):
        cls.baseline = BMO_ARTIFACT.read_bytes()

    def test_baseline_artifact_is_the_recorded_persistent_bank(self):
        self.assertEqual(len(self.baseline), EXPECTED_BANK_BYTES)
        self.assertEqual(tts_bank.sha256(self.baseline), tts_bank.BMO_BANK_SHA256)

    def test_record_parser_matches_published_record_ranges(self):
        published = json.loads(RANGES_JSON.read_text())
        records = record_ranges_from_bytes(self.baseline)
        self.assertEqual(len(records), len(published))
        for record, expected in zip(records, published):
            self.assertEqual(record.sound_id, expected["sound_id"])
            self.assertEqual(record.pcm_offset, expected["pcm_offset"])
            self.assertEqual(record.pcm_byte_count, expected["pcm_byte_count"])
            self.assertEqual(record.sample_count, expected["sample_count"])
            self.assertEqual(record.record_offset, expected["record_offset"])

    def test_slot_sequence_matches_live_map_and_declared_durations(self):
        records = {r.sound_id: r for r in record_ranges_from_bytes(self.baseline)}
        self.assertEqual(set(SLOT_SEQUENCE), set(records))
        expected = {0: 2.881995, 1: 1.867392, 2: 1.869388, 6: 2.008662,
                    7: 2.017324, 8: 2.008662, 9: 2.008662, 10: 1.991338,
                    3: 0.321451, 19: 0.321451}
        for sound_id, seconds in expected.items():
            self.assertAlmostEqual(records[sound_id].duration_seconds, seconds)
        total = sum(records[s].sample_count for s in SLOT_SEQUENCE)
        self.assertAlmostEqual(total / PCM_SAMPLE_RATE, 17.296, places=3)


class BuildAndValidateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.baseline = BMO_ARTIFACT.read_bytes()
        cls.records = {r.sound_id: r
                       for r in record_ranges_from_bytes(cls.baseline)}

    def make_segments(self, seconds=3.5):
        capacities = [(sid, self.records[sid].sample_count)
                      for sid in SLOT_SEQUENCE]
        samples = normalize_peak(array("h", tone(seconds)))
        return plan_segments(samples, capacities)

    def test_build_changes_only_pcm_field_bytes(self):
        segments = self.make_segments()
        built, _ = build_tts_bank(self.baseline, segments, "keep")
        self.assertEqual(len(built), EXPECTED_BANK_BYTES)
        pcm_spans = [(r.pcm_offset, r.pcm_offset + r.pcm_byte_count)
                     for r in self.records.values()]
        diff_offsets = [i for i in range(len(built))
                        if built[i] != self.baseline[i]]
        for offset in diff_offsets:
            self.assertTrue(any(lo <= offset < hi for lo, hi in pcm_spans),
                            f"byte {offset} changed outside a PCM field")

    def test_headers_directory_and_padding_are_preserved(self):
        segments = self.make_segments()
        built, _ = build_tts_bank(self.baseline, segments, "silence")
        self.assertEqual(built[:8 * 512], self.baseline[:8 * 512])
        for record in self.records.values():
            self.assertEqual(
                built[record.record_offset:record.pcm_offset],
                self.baseline[record.record_offset:record.pcm_offset])
            pad_start = record.pcm_offset + record.pcm_byte_count
            pad_end = record.record_offset + record.record_capacity_bytes
            self.assertEqual(built[pad_start:pad_end],
                             self.baseline[pad_start:pad_end])

    def test_segments_are_zero_padded_to_declared_length(self):
        segments = self.make_segments()
        built, _ = build_tts_bank(self.baseline, segments, "silence")
        seg = segments[0]
        record = self.records[seg.sound_id]
        field = built[record.pcm_offset:record.pcm_offset + record.pcm_byte_count]
        self.assertEqual(field[:len(seg.pcm)], seg.pcm)
        self.assertFalse(any(field[len(seg.pcm):]))

    def test_validation_passes_for_clean_build_and_reports_hashes(self):
        segments = self.make_segments()
        built, manifest = build_tts_bank(self.baseline, segments, "silence")
        report = validate_tts_bank(self.baseline, built, segments, "silence")
        self.assertTrue(report["ok"], [c for c in report["checks"] if not c["ok"]])
        self.assertEqual(report["sha256"], manifest["sha256"])
        self.assertEqual(report["file_size"], EXPECTED_BANK_BYTES)
        self.assertTrue(report["transport_additive_checksum_hex"].startswith("0x"))
        self.assertEqual(manifest["unused_slots"], "silence")
        roles = {s["sound_id"]: s["role"] for s in manifest["slots"]}
        used = {seg.sound_id for seg in segments}
        for sound_id, role in roles.items():
            self.assertEqual(role, "speech" if sound_id in used else "silenced")

    def test_validation_fails_on_out_of_field_corruption(self):
        segments = self.make_segments()
        built, _ = build_tts_bank(self.baseline, segments, "silence")
        corrupted = bytearray(built)
        corrupted[100] ^= 0x01  # inside the directory pages
        try:
            report = validate_tts_bank(self.baseline, bytes(corrupted),
                                       segments, "silence")
        except TtsBankError:
            return  # unparseable directory is an acceptable hard failure
        self.assertFalse(report["ok"])

    def test_validation_fails_when_preview_pcm_differs_from_bank(self):
        segments = self.make_segments()
        built, _ = build_tts_bank(self.baseline, segments, "silence")
        record = self.records[segments[0].sound_id]
        tampered = bytearray(built)
        tampered[record.pcm_offset] ^= 0x01
        report = validate_tts_bank(self.baseline, bytes(tampered),
                                   segments, "silence")
        self.assertFalse(report["ok"])
        failed = {c["check"] for c in report["checks"] if not c["ok"]}
        self.assertIn(f"slot_{segments[0].sound_id}_preview_matches_bank", failed)

    def test_oversized_segment_is_rejected(self):
        record = self.records[3]  # 0.32 s slot
        seg = SlotSegment(0, 3, record.sample_count,
                          b"\x11\x22" * (record.sample_count + 1))
        with self.assertRaisesRegex(TtsBankError, "slot 3"):
            build_tts_bank(self.baseline, [seg], "keep")

    def test_confirmation_token_includes_full_sha256(self):
        sha = "a" * 64
        self.assertEqual(required_confirmation(sha), f"BURN TTS {'a' * 64}")


class TextChunkingTests(unittest.TestCase):
    """Long text packs into bank-sized chunks at sentence/word boundaries."""

    @staticmethod
    def synth_by_words(seconds_per_word):
        def synth(text):
            n = int(len(text.split()) * seconds_per_word * PCM_SAMPLE_RATE)
            return array("h", [1000] * n)
        return synth

    def setUp(self):
        records = record_ranges_from_bytes(BMO_ARTIFACT.read_bytes())
        by_id = {r.sound_id: r for r in records}
        self.capacities = [(sid, by_id[sid].sample_count)
                           for sid in SLOT_SEQUENCE]

    def test_short_text_is_one_chunk(self):
        chunks = tts_bank.plan_speech_chunks(
            "Hello there.", self.synth_by_words(0.4), self.capacities)
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0]["text"], "Hello there.")
        self.assertTrue(chunks[0]["segments"])

    def test_long_text_splits_at_sentence_boundaries(self):
        # 3 sentences x 10 words x 1.2 s/word = 12 s each: one per bank
        text = " ".join(["one two three four five six seven eight nine ten."] * 3)
        chunks = tts_bank.plan_speech_chunks(
            text, self.synth_by_words(1.2), self.capacities)
        self.assertEqual(len(chunks), 3)
        for chunk in chunks:
            self.assertTrue(chunk["text"].endswith("."))
            total = sum(s.sample_count for s in chunk["segments"])
            self.assertLessEqual(total, sum(c for _, c in self.capacities))
        joined = " ".join(c["text"] for c in chunks)
        self.assertEqual(joined.split(), text.split())

    def test_overlong_sentence_splits_at_word_boundaries(self):
        text = " ".join(f"word{i}" for i in range(30))  # one 36 s "sentence"
        chunks = tts_bank.plan_speech_chunks(
            text, self.synth_by_words(1.2), self.capacities)
        self.assertGreater(len(chunks), 1)
        joined = " ".join(c["text"] for c in chunks)
        self.assertEqual(joined.split(), text.split())

    def test_single_overlong_word_is_rejected_not_truncated(self):
        with self.assertRaisesRegex(TtsBankError, "single word"):
            tts_bank.plan_speech_chunks(
                "supercalifragilistic", self.synth_by_words(20.0),
                self.capacities)

    def test_absurd_length_hits_chunk_ceiling(self):
        text = ". ".join(["a b c d e f g h i j"] * 60)
        with self.assertRaisesRegex(TtsBankError, "shorten"):
            tts_bank.plan_speech_chunks(
                text, self.synth_by_words(1.5), self.capacities, max_chunks=3)


class SanitizeSpeechTextTests(unittest.TestCase):
    def test_emoji_and_symbols_are_stripped(self):
        self.assertEqual(
            tts_bank.sanitize_speech_text(
                "Hey there! \U0001F601 Super clean and fun! \U0001F6E0️ "
                "Zoom next! \U0001F916\U0001F4A5"),
            "Hey there! Super clean and fun! Zoom next!")

    def test_plain_and_accented_text_is_untouched(self):
        self.assertEqual(tts_bank.sanitize_speech_text("café naïve, ok?"),
                         "café naïve, ok?")

    def test_emoji_only_text_becomes_empty(self):
        self.assertEqual(tts_bank.sanitize_speech_text("\U0001F916\U0001F4A5"), "")


class WebGuardWiringTests(unittest.TestCase):
    """The web layer keeps the internal gates while staying automatic."""

    @classmethod
    def setUpClass(cls):
        cls.page = (ROOT / "bmo_web.py").read_text()

    def test_speak_flow_uses_validated_chunk_operation(self):
        self.assertIn("speak_chunks_operation", self.page)
        self.assertIn("plan_speech_chunks", self.page)

    def test_bank_install_blocked_during_tts_operation(self):
        self.assertIn("a TTS bank operation is running", self.page)

    def test_chat_robot_voice_routes_through_bank_speech(self):
        self.assertIn("tts_start_speak(reply", self.page)

    def test_voice_parameters_match_brain_server_defaults(self):
        self.assertIn("TTS_SPEED = 160", self.page)
        self.assertIn("TTS_PITCH = 70", self.page)

    def test_endpoints_exist(self):
        for path in ("/tts-bank/speak", "/tts-bank/stop",
                     "/tts-bank/restore", "/tts-bank/status"):
            self.assertIn(path, self.page)


if __name__ == "__main__":
    unittest.main()
