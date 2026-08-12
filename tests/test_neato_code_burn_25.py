import unittest

from tools.neato_code_burn_25 import (
    COMMAND,
    CONFIRMATION,
    EXPECTED_SOFTWARE,
    IMAGE_BYTES,
    IMAGE_SHA256,
    BurnSafetyError,
    classify_image,
    expected_post_version,
    neato_usb_ports,
    require_confirmation,
    require_target,
)


class NeatoCodeBurn25SafetyTests(unittest.TestCase):
    def test_command_is_exact_code_reboot_path(self):
        self.assertEqual(COMMAND, "Upload code reboot")

    def test_only_exact_25_image_is_allowed(self):
        classify_image(IMAGE_BYTES, IMAGE_SHA256)
        with self.assertRaisesRegex(BurnSafetyError, "refusing image"):
            classify_image(IMAGE_BYTES - 1, IMAGE_SHA256)
        with self.assertRaisesRegex(BurnSafetyError, "refusing image"):
            classify_image(IMAGE_BYTES, "0" * 64)

    def test_confirmation_is_exact(self):
        require_confirmation(CONFIRMATION)
        with self.assertRaisesRegex(BurnSafetyError, "confirmation must exactly equal"):
            require_confirmation(CONFIRMATION.lower())

    def test_target_must_be_exact_24_robot(self):
        require_target("Serial Number,WTD41611DD,0037829,P\nSoftware,2,4,15667")
        with self.assertRaisesRegex(BurnSafetyError, "approved target"):
            require_target("Serial Number,WTD00000,0000000,P\nSoftware,2,4,15667")

    def test_expected_post_version_covers_archive_discrepancy(self):
        self.assertEqual(
            EXPECTED_SOFTWARE,
            ("Software,2,5,15893", "Software,2,5,15894"),
        )
        self.assertTrue(
            expected_post_version(
                "Serial Number,WTD41611DD,0037829,P\nSoftware,2,5,15893"
            )
        )
        self.assertTrue(
            expected_post_version(
                "Serial Number,WTD41611DD,0037829,P\nSoftware,2,5,15894"
            )
        )
        self.assertFalse(
            expected_post_version(
                "Serial Number,WTD41611DD,0037829,P\nSoftware,3,2,18755"
            )
        )

    def test_port_discovery_returns_strings(self):
        self.assertTrue(all(isinstance(port, str) for port in neato_usb_ports()))


if __name__ == "__main__":
    unittest.main()
