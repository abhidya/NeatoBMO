import struct
import tempfile
import unittest
from pathlib import Path

from neato_sound_bank import PAGE_SIZE, candidate_boundaries


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


if __name__ == "__main__":
    unittest.main()
