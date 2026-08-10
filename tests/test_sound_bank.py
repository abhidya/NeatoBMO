import struct
import tempfile
import unittest
from pathlib import Path
import wave

from neato_sound_bank import (
    PAGE_SIZE,
    build_from_wavs,
    candidate_boundaries,
    record_ranges,
)


def write_wav(path: Path, samples: list[int]) -> None:
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(22050)
        wav.writeframes(b"".join(struct.pack("<h", sample) for sample in samples))


def minimal_bank() -> bytes:
    data = bytearray(PAGE_SIZE * 12)
    for page in range(8):
        offset = page * PAGE_SIZE
        data[offset:offset + 2] = b"KT"
        struct.pack_into("<H", data, offset + 2, page)
    struct.pack_into("<H", data, 4, 1)
    struct.pack_into("<2H", data, 8, 8, 10)
    for page, samples in ((8, [100, -100, 50]), (10, [200, -200])):
        offset = page * PAGE_SIZE
        pcm = b"".join(struct.pack("<h", sample) for sample in samples)
        data[offset:offset + 2] = b"\x01\x01"
        struct.pack_into("<H", data, offset + 2, 22050)
        struct.pack_into("<I", data, offset + 4, len(samples))
        struct.pack_into("<I", data, offset + 8, len(pcm))
        data[offset + 16:offset + 16 + len(pcm)] = pcm
    return bytes(data)


class SoundBankTableTest(unittest.TestCase):
    def test_preserves_nonempty_sound_ids_from_header_table(self):
        data = bytearray(PAGE_SIZE * 16)
        for page in range(8):
            offset = page * PAGE_SIZE
            data[offset:offset + 2] = b"KT"
        struct.pack_into("<H", data, 4, 2)  # three sound IDs: 0, 1, and 2
        struct.pack_into("<3H", data, 8, 8, 10, 12)

        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / "sound.bin"
            image.write_bytes(data)
            entries = candidate_boundaries(image)

        self.assertEqual([entry["inferred_sound_id"] for entry in entries], [0, 1, 2])
        self.assertEqual([entry["end_page_exclusive"] for entry in entries], [10, 12, 16])

    def test_parses_record_headers_and_pcm_hashes(self):
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / "sound.bin"
            image.write_bytes(minimal_bank())

            records = record_ranges(image)

        self.assertEqual([record["sound_id"] for record in records], [0, 1])
        self.assertEqual(records[0]["sample_count"], 3)
        self.assertEqual(records[0]["pcm_byte_count"], 6)
        self.assertEqual(records[0]["zero_padding_bytes"], PAGE_SIZE * 2 - 16 - 6)

    def test_build_from_wavs_preserves_size_and_replaces_one_record(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image = root / "sound.bin"
            clip = root / "replacement.wav"
            output = root / "built.bin"
            image.write_bytes(minimal_bank())
            write_wav(clip, [1, 2, 3, 4])

            result = build_from_wavs(image, {1: clip}, output)
            records = record_ranges(output)

        self.assertEqual(result["file_size"], PAGE_SIZE * 12)
        self.assertEqual(result["replaced_sound_ids"], [1])
        self.assertEqual(records[0]["sample_count"], 3)
        self.assertEqual(records[1]["sample_count"], 4)

    def test_build_from_wavs_rejects_oversized_clip(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image = root / "sound.bin"
            clip = root / "too-big.wav"
            output = root / "built.bin"
            image.write_bytes(minimal_bank())
            write_wav(clip, [0] * (PAGE_SIZE + 1))

            with self.assertRaisesRegex(ValueError, "exceeds slot 1 capacity"):
                build_from_wavs(image, {1: clip}, output)


if __name__ == "__main__":
    unittest.main()
