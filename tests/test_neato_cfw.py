import contextlib
import io
import json
import os
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from neato_cfw import (
    CfwPatchError,
    main,
    inspect_raw,
    patch_version,
    publish_pair,
    verify_patch,
)
from neato_firmware import sha256


def raw_image(version: bytes = b"15667") -> bytes:
    return (
        b"\x00" * 64
        + b"Neato Robotics\x00PlaySound\x00GetVersion\x00SetMotor\x00"
        + b"Software,2,4,"
        + version
        + b"\x00"
        + b"\xff" * 128
    )


class RawInspectionTests(unittest.TestCase):
    def test_reports_markers_hash_and_requested_searches(self):
        data = raw_image()

        result = inspect_raw(data, (b"15667",))

        self.assertEqual(result["file_size"], len(data))
        self.assertEqual(result["sha256"], sha256(data))
        self.assertEqual(len(result["known_marker_offsets"]["GetVersion"]), 1)
        self.assertEqual(len(result["search_offsets"]["15667"]), 1)


class VersionPatchTests(unittest.TestCase):
    def test_builds_same_size_deterministic_ascii_patch(self):
        base = raw_image()
        kwargs = {
            "expected_base_sha256": sha256(base),
            "old_value": "15667",
            "new_value": "95667",
        }

        first, first_manifest = patch_version(base, **kwargs)
        second, second_manifest = patch_version(base, **kwargs)

        self.assertEqual(first, second)
        self.assertEqual(first_manifest, second_manifest)
        self.assertEqual(len(first), len(base))
        self.assertIn(b"Software,2,4,95667", first)
        self.assertTrue(verify_patch(base, first, first_manifest)["passed"])

    def test_rejects_wrong_base_hash(self):
        with self.assertRaisesRegex(CfwPatchError, "base SHA-256 mismatch"):
            patch_version(
                raw_image(),
                expected_base_sha256="0" * 64,
                old_value="15667",
                new_value="95667",
            )

    def test_rejects_multiple_matches_without_reviewed_offset(self):
        base = raw_image() + b"15667"
        with self.assertRaisesRegex(CfwPatchError, "exactly one"):
            patch_version(
                base,
                expected_base_sha256=sha256(base),
                old_value="15667",
                new_value="95667",
            )

    def test_reviewed_offset_can_select_one_of_multiple_matches(self):
        base = raw_image() + b"15667"
        selected = base.rfind(b"15667")

        patched, manifest = patch_version(
            base,
            expected_base_sha256=sha256(base),
            old_value="15667",
            new_value="95667",
            offset=selected,
        )

        self.assertEqual(patched[selected : selected + 5], b"95667")
        self.assertIn(b"Software,2,4,15667", patched)
        self.assertTrue(verify_patch(base, patched, manifest)["passed"])

    def test_rejects_size_changing_ascii_patch(self):
        base = raw_image()
        with self.assertRaisesRegex(CfwPatchError, "identical length"):
            patch_version(
                base,
                expected_base_sha256=sha256(base),
                old_value="15667",
                new_value="CFW-95667",
            )

    def test_supports_little_endian_integer_constant(self):
        base = b"prefix" + (15667).to_bytes(4, "little") + b"suffix"

        patched, manifest = patch_version(
            base,
            expected_base_sha256=sha256(base),
            old_value="15667",
            new_value="95667",
            encoding="u32le",
        )

        self.assertIn((95667).to_bytes(4, "little"), patched)
        self.assertTrue(verify_patch(base, patched, manifest)["passed"])

    def test_verifier_rejects_an_extra_change(self):
        base = raw_image()
        patched, manifest = patch_version(
            base,
            expected_base_sha256=sha256(base),
            old_value="15667",
            new_value="95667",
        )
        tampered = bytes([patched[0] ^ 1]) + patched[1:]

        result = verify_patch(base, tampered, manifest)

        self.assertFalse(result["passed"])
        self.assertFalse(result["checks"]["output_sha256"])
        self.assertFalse(result["checks"]["only_approved_replacement"])

    def test_verifier_rejects_a_noop_manifest(self):
        base = raw_image()
        manifest = {
            "schema": "neato-cfw-version-patch/v1",
            "tool_version": 1,
            "operation": "version-only",
            "base": {"size": len(base), "sha256": sha256(base)},
            "output": {"size": len(base), "sha256": sha256(base)},
            "patch": {
                "offset": 0,
                "size": 0,
                "encoding": "ascii",
                "old_value": "",
                "new_value": "",
                "old_hex": "",
                "new_hex": "",
                "all_old_value_matches": [],
            },
            "integrity_updates": [],
        }

        with self.assertRaises(CfwPatchError):
            verify_patch(base, base, manifest)

    def test_cli_writes_and_verifies_paired_artifacts(self):
        base = raw_image()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base_path = root / "base.bin"
            patched_path = root / "patched.bin"
            manifest_path = root / "custom-manifest.json"
            base_path.write_bytes(base)

            with contextlib.redirect_stdout(io.StringIO()):
                patch_exit = main(
                    [
                        "patch-version",
                        str(base_path),
                        str(patched_path),
                        "--base-sha256",
                        sha256(base),
                        "--old",
                        "15667",
                        "--new",
                        "95667",
                        "--manifest",
                        str(manifest_path),
                    ]
                )
                verify_exit = main(
                    [
                        "verify-patch",
                        str(base_path),
                        str(patched_path),
                        str(manifest_path),
                    ]
                )

            manifest = json.loads(manifest_path.read_text())
            self.assertEqual(patch_exit, 0)
            self.assertEqual(verify_exit, 0)
            self.assertEqual(manifest["output"]["sha256"], sha256(patched_path.read_bytes()))

    def test_cli_refuses_to_overwrite_existing_output(self):
        base = raw_image()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base_path = root / "base.bin"
            output_path = root / "patched.bin"
            base_path.write_bytes(base)
            output_path.write_bytes(b"existing")

            with contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaisesRegex(SystemExit, "2"):
                    main(
                        [
                            "patch-version",
                            str(base_path),
                            str(output_path),
                            "--base-sha256",
                            sha256(base),
                            "--old",
                            "15667",
                            "--new",
                            "95667",
                        ]
                    )

            self.assertEqual(output_path.read_bytes(), b"existing")

    def test_paired_publish_cleans_up_when_second_publish_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "patched.bin"
            second = root / "manifest.json"
            real_link = os.link
            call_count = 0

            def fail_second_link(source, destination):
                nonlocal call_count
                call_count += 1
                if call_count == 2:
                    raise OSError("simulated manifest publish failure")
                return real_link(source, destination)

            with mock.patch("neato_cfw.os.link", side_effect=fail_second_link):
                with self.assertRaisesRegex(OSError, "simulated"):
                    publish_pair(first, b"firmware", second, b"manifest")

            self.assertFalse(first.exists())
            self.assertFalse(second.exists())
            self.assertEqual(list(root.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
