import struct
import unittest

from bmoq_config import (
    CONFIG_HEADER_BYTES,
    TYPE_BOOL,
    TYPE_F32,
    TYPE_U32,
    TYPE_U32_ARRAY,
    encode_config,
)


class BmoqConfigTests(unittest.TestCase):
    def test_encoding_is_sorted_bounded_and_little_endian(self) -> None:
        encoded = encode_config({
            1003: (TYPE_U32, 512),
            1: (TYPE_U32_ARRAY, [151329, 151336, 151338]),
            2: (TYPE_F32, 1.0e-6),
            1010: (TYPE_BOOL, True),
        })
        magic, version, header_bytes, entries, total = struct.unpack_from(
            "<4sHHII", encoded
        )
        self.assertEqual(magic, b"BCFG")
        self.assertEqual((version, header_bytes, entries, total),
                         (1, CONFIG_HEADER_BYTES, 4, len(encoded)))
        self.assertEqual(struct.unpack_from("<I", encoded,
                                            CONFIG_HEADER_BYTES)[0], 1)
        self.assertEqual(encoded, encode_config({
            1010: (TYPE_BOOL, True),
            2: (TYPE_F32, 1.0e-6),
            1: (TYPE_U32_ARRAY, [151329, 151336, 151338]),
            1003: (TYPE_U32, 512),
        }))

    def test_rejects_invalid_shapes_and_types(self) -> None:
        with self.assertRaises(ValueError):
            encode_config({1: (TYPE_U32, [1, 2])})
        with self.assertRaises(ValueError):
            encode_config({1: (99, 1)})
        with self.assertRaises(TypeError):
            encode_config({1: (TYPE_U32, "1")})


if __name__ == "__main__":
    unittest.main()
