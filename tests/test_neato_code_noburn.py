import unittest

from tools.neato_code_noburn import (
    BLOCKED_IMAGES,
    COMMAND,
    SAFE_IMAGES,
    classify_digest,
    require_target,
)


class NeatoCodeNoBurnSafetyTests(unittest.TestCase):
    def test_command_cannot_request_a_flash_burn(self):
        self.assertEqual(COMMAND, "Upload code noburn")

    def test_only_verified_2_5_2_7_and_3_1_images_are_allowed(self):
        builds = {
            classify_digest(image["size"], digest)["build"]
            for digest, image in SAFE_IMAGES.items()
        }
        self.assertEqual(builds, {15893, 16621, 17844})

    def test_known_3_2_image_is_explicitly_blocked(self):
        digest = next(iter(BLOCKED_IMAGES))
        with self.assertRaisesRegex(ValueError, "FORBIDDEN.*3.2.*brick risk"):
            classify_digest(852_992, digest)

    def test_unknown_image_is_blocked(self):
        with self.assertRaisesRegex(ValueError, "refusing unknown application image"):
            classify_digest(123, "0" * 64)

    def test_allowlisted_hash_with_wrong_size_is_blocked(self):
        digest, image = next(iter(SAFE_IMAGES.items()))
        with self.assertRaisesRegex(ValueError, "refusing size mismatch"):
            classify_digest(image["size"] - 1, digest)

    def test_wrong_robot_identity_is_blocked(self):
        with self.assertRaisesRegex(RuntimeError, "identity/version did not match"):
            require_target("Software,2,4,15667\nSerial Number,not-this-robot")


if __name__ == "__main__":
    unittest.main()
