import struct
import tempfile
import unittest
from pathlib import Path

from backup_neato import field, safe_component
from neato_firmware import EncryptedImage, catalog, validate_plaintext


def fake_image(path: Path, plaintext_size: int = 700) -> bytes:
    header = bytearray(512)
    struct.pack_into("<I", header, 0, plaintext_size)
    header[4] = 2
    header[5:10] = b"neato"
    header[16:32] = bytes(range(16))
    payload = bytes((index * 73 + 41) % 256 for index in range(1024))
    data = bytes(header) + payload
    path.write_bytes(data)
    return data


class BackupHelpersTest(unittest.TestCase):
    def test_extracts_comma_separated_version_fields(self):
        response = "GetVersion\r\nSoftware,2,4,15667\r\n"
        self.assertEqual(field(response, "Software"), "2-4-15667")

    def test_sanitizes_snapshot_path_components(self):
        self.assertEqual(safe_component("A/B C", "fallback"), "A-B-C")


class FirmwareImageTest(unittest.TestCase):
    def test_parses_neato_image_envelope(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "XV11App.test.P.bin.enc"
            data = fake_image(path)
            image = EncryptedImage.read(path)
            self.assertEqual(image.file_size, len(data))
            self.assertEqual(image.declared_plaintext_size, 700)
            self.assertEqual(image.format_version, 2)
            self.assertEqual(image.marker, "neato")
            self.assertEqual(image.payload_size, 1024)
            self.assertEqual(image.page_padding_size, 324)

    def test_catalog_groups_identical_content(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = fake_image(root / "a.bin.enc")
            (root / "b.bin.enc").write_bytes(data)
            result = catalog(root)
            self.assertEqual(result["file_count"], 2)
            self.assertEqual(len(result["duplicate_content_groups"]), 1)

    def test_unlock_validation_rejects_random_plaintext(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "a.bin.enc"
            fake_image(path)
            image = EncryptedImage.read(path)
            result = validate_plaintext(image, bytes(range(256)) * 3)
            self.assertFalse(result["passed"])
            self.assertFalse(result["checks"]["has_neato_code_markers"])

    def test_unlock_validation_accepts_marker_rich_low_entropy_candidate(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "a.bin.enc"
            fake_image(path)
            image = EncryptedImage.read(path)
            strings = b"\0".join(
                f"Neato diagnostic string number {index:02d}".encode()
                for index in range(24)
            )
            plaintext = (
                b"Neato Robotics\0PlaySound\0GetVersion\0SetMotor\0" + strings
            )[:700].ljust(700, b"\0")

            result = validate_plaintext(image, plaintext)

            self.assertTrue(result["passed"])
            self.assertGreaterEqual(result["printable_string_count"], 20)
            self.assertIn("arm32_screening_signals", result)


if __name__ == "__main__":
    unittest.main()
