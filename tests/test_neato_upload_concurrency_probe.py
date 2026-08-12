import struct
import unittest
from unittest.mock import patch

from tools.neato_upload_concurrency_probe import (
    ALLOWED_SOFTWARE,
    BANK_BYTES,
    CONFIRMATION,
    FRAMED_BYTES,
    P6_COMMAND,
    TARGET_SERIAL,
    USB_COMMAND,
    ProbeSafetyError,
    build_framed_payload,
    build_upload_header,
    classify_p6_capture,
    require_confirmation,
    require_target,
    stream_with_injection,
    validate_bank,
    validate_injection_offset,
)


class _FakeConnection:
    def __init__(self):
        self.written = bytearray()
        self.flushes = 0

    def write(self, data):
        self.written.extend(data)
        return len(data)

    def flush(self):
        self.flushes += 1


class _FakeCollector:
    def __init__(self, *, fail_send=False):
        self.commands = 0
        self.fail_send = fail_send

    def mark(self):
        return 123

    def send_getversion(self):
        self.commands += 1
        if self.fail_send:
            raise OSError("simulated P6 disconnect")


class UploadConcurrencyProbeSafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # hashlib is patched only for the exact-size synthetic fixture because
        # the real 770 KB vendor artifact is intentionally not duplicated here.
        cls.fake_bank = b"\x00" * BANK_BYTES

    def test_commands_are_fixed_to_noburn_and_readonly(self):
        self.assertEqual(USB_COMMAND, "Upload sound noburn")
        self.assertEqual(P6_COMMAND, b"GetVersion\r")
        self.assertNotIn("code", USB_COMMAND.lower())
        self.assertIn("noburn", build_upload_header().decode("ascii").lower())
        self.assertEqual(
            build_upload_header(),
            f"Upload sound noburn Size {FRAMED_BYTES}\r".encode("ascii"),
        )

    def test_bank_validator_rejects_wrong_size_and_hash(self):
        with self.assertRaisesRegex(ProbeSafetyError, "unexpected bank"):
            validate_bank(b"too short")
        with self.assertRaisesRegex(ProbeSafetyError, "unexpected bank"):
            validate_bank(self.fake_bank)

    def test_framing_uses_little_endian_additive_checksum(self):
        payload = b"\x01\x02\xff"
        with patch(
            "tools.neato_upload_concurrency_probe.validate_bank",
            return_value="fixture",
        ):
            framed, checksum = build_framed_payload(payload)
        self.assertEqual(checksum, 0x102)
        self.assertEqual(framed, payload + struct.pack("<I", checksum))
        self.assertEqual(framed[-4:], b"\x02\x01\x00\x00")

    def test_exact_confirmation_is_required(self):
        require_confirmation(CONFIRMATION)
        with self.assertRaisesRegex(ProbeSafetyError, "confirmation must exactly equal"):
            require_confirmation(CONFIRMATION.lower())

    def test_target_is_pinned_to_known_installed_or_factory_software(self):
        for software in ALLOWED_SOFTWARE:
            reply = b"Serial Number," + TARGET_SERIAL + b"\n" + software
            self.assertEqual(require_target(reply, channel="test"), software.decode())
        with self.assertRaisesRegex(ProbeSafetyError, "approved robot"):
            require_target(b"Serial Number,WTD00000,0000000,P\nSoftware,2,5,15893", channel="test")
        with self.assertRaisesRegex(ProbeSafetyError, "unapproved software"):
            require_target(b"Serial Number," + TARGET_SERIAL + b"\nSoftware,3,2,18755", channel="test")

    def test_injection_offset_stays_inside_payload_body(self):
        validate_injection_offset(BANK_BYTES // 2)
        with self.assertRaisesRegex(ProbeSafetyError, "injection offset"):
            validate_injection_offset(0)
        with self.assertRaisesRegex(ProbeSafetyError, "injection offset"):
            validate_injection_offset(BANK_BYTES)

    def test_stream_completes_exact_frame_and_injects_once(self):
        connection = _FakeConnection()
        collector = _FakeCollector()
        frame = bytes(range(32))
        sent, mark, error = stream_with_injection(
            connection,
            frame,
            collector,
            injection_offset=13,
            chunk_size=7,
            chunk_delay_ms=0,
        )
        self.assertEqual(bytes(connection.written), frame)
        self.assertEqual(sent, len(frame))
        self.assertEqual(mark, 123)
        self.assertIsNone(error)
        self.assertEqual(collector.commands, 1)

    def test_p6_send_failure_does_not_abandon_usb_frame(self):
        connection = _FakeConnection()
        collector = _FakeCollector(fail_send=True)
        frame = bytes(range(32))
        sent, _, error = stream_with_injection(
            connection,
            frame,
            collector,
            injection_offset=13,
            chunk_size=7,
            chunk_delay_ms=0,
        )
        self.assertEqual(bytes(connection.written), frame)
        self.assertEqual(sent, len(frame))
        self.assertIn("simulated P6 disconnect", error)
        self.assertEqual(collector.commands, 1)

    def test_capture_classifier_separates_concurrency_from_destination(self):
        capture = (
            b"Serial Number,WTD41611DD,0037829,P\r\n"
            b"Software,2,5,15893\r\n"
            b"Type : SOUND\r\nOptions : NoWrite\r\n"
            b"Upload complete - 770052 bytes received\r\n"
            b"nandflashWrite() - region=0x400000 offset=0 bytes=770052\r\n"
            b"Upload fail - nandflashWrite() fail - -1\r\n"
        )
        flags = classify_p6_capture(capture)
        self.assertTrue(flags["target_getversion"])
        self.assertTrue(flags["no_write_option"])
        self.assertTrue(flags["full_frame_received"])
        self.assertTrue(flags["nand_write_blocked"])
        self.assertTrue(flags["sound_region_seen"])
        self.assertFalse(flags["application_region_seen"])
        self.assertFalse(flags["nand_write_ok"])

    def test_capture_classifier_flags_any_unexpected_real_write(self):
        flags = classify_p6_capture(
            b"nandflashWrite() - region=0x10000 offset=0 bytes=770052\r\n"
            b"Upload - nandFlashWrite() OK\r\n"
        )
        self.assertTrue(flags["application_region_seen"])
        self.assertTrue(flags["nand_write_ok"])
        self.assertFalse(flags["nand_write_blocked"])


if __name__ == "__main__":
    unittest.main()
