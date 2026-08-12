import hashlib
import importlib.util
import json
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
NEATOOS = REPO / "neatoos"
PROBE = NEATOOS / "tools" / "probe_generator.py"

spec = importlib.util.spec_from_file_location("probe_generator", PROBE)
probe_generator = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules["probe_generator"] = probe_generator
spec.loader.exec_module(probe_generator)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def reference_image(path: Path) -> bytes:
    data = bytearray(512 + 512)
    struct.pack_into("<I", data, 0, 123456)
    data[4] = 2
    data[5:10] = b"neato"
    data[16:32] = bytes(range(0xA0, 0xB0))
    data[32:512] = bytes((index * 7 + 3) % 256 for index in range(480))
    data[512:] = b"reference ciphertext placeholder".ljust(512, b"\x5a")
    path.write_bytes(data)
    return bytes(data)


class NeatoOsPhaseATest(unittest.TestCase):
    def test_manifest_declares_offline_safety_constraints(self):
        manifest = json.loads((NEATOOS / "manifests" / "phase-a.json").read_text())

        self.assertEqual(manifest["schema"], "neatoos-phase-a/v1")
        self.assertEqual(manifest["raw_banner"], "NEATOOS RAW V0")
        self.assertIn("hypothesis", manifest["targets"]["default_link_address"])
        self.assertIn("no hardware access", manifest["safety_constraints"])
        self.assertIn("no upload commands", manifest["safety_constraints"])
        self.assertIn("no proprietary firmware blobs committed", manifest["safety_constraints"])
        self.assertEqual(
            manifest["probe_representations"],
            ["raw-structural", "reference-header"],
        )

    def test_reference_manifest_records_metadata_without_committing_blob(self):
        manifest = json.loads((NEATOOS / "manifests" / "reference-images.json").read_text())
        reference = manifest["references"][0]

        self.assertEqual(manifest["schema"], "neatoos-reference-images/v1")
        self.assertEqual(reference["label"], "cruz-p-2.5.15893-public-update")
        self.assertEqual(
            reference["sha256"],
            "e1a31ef56e2b617a4056d70c308aab26b7e2cd95e679cb982d697ff4f089c697",
        )
        self.assertTrue(reference["proprietary"])
        self.assertIn("do not copy", reference["repo_policy"])

    def test_canary_repeatedly_emits_exact_raw_banner_on_dbgu(self):
        main = (NEATOOS / "src" / "main.c").read_text()
        header = (NEATOOS / "include" / "at91sam9xe_min.h").read_text()
        makefile = (NEATOOS / "Makefile").read_text()

        self.assertIn('"NEATOOS RAW V0\\r\\n"', main)
        self.assertIn("for (;;) {\n        dbgu_puts(kBanner);", main)
        self.assertIn("AT91_DBGU_THR", main)
        self.assertIn("AT91_DBGU_CSR_TXRDY", header)
        self.assertIn("AT91_DBGU_BASE 0xFFFFF200u", header)
        self.assertNotIn("AT91_DBGU_BRGR", main)
        self.assertNotIn("AT91_DBGU_MR", main)
        self.assertIn("neatoos-raw", makefile)
        self.assertIn("neatoos-raw.bin", (NEATOOS / "README.md").read_text())

    def test_structural_probe_format_is_byte_exact_and_not_encrypted(self):
        raw = b"NEATOOS RAW V0\r\n"
        header = probe_generator.structural_header(raw)
        image = header + probe_generator.page_pad(raw)

        self.assertEqual(len(header), 512)
        self.assertEqual(struct.unpack_from("<I", header, 0)[0], len(raw))
        self.assertEqual(header[4], 2)
        self.assertEqual(header[5:10], b"neato")
        self.assertEqual(header[10:16], b"\0" * 6)
        self.assertEqual(header[16:32], probe_generator.experimental_field(raw))
        self.assertEqual(header[32:], b"\0" * (512 - 32))
        self.assertEqual(image[512 : 512 + len(raw)], raw)
        self.assertEqual(len(image), 1024)

    def test_generator_creates_two_probe_images_and_manifests(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw_path = root / "neatoos-raw.bin"
            reference_path = root / "XV11App.15893.P.bin.enc"
            out_dir = root / "out"
            raw = b"NEATOOS RAW V0\r\n"
            raw_path.write_bytes(raw)
            reference = reference_image(reference_path)

            subprocess.check_call(
                [
                    sys.executable,
                    str(PROBE),
                    str(raw_path),
                    "--reference",
                    str(reference_path),
                    "--reference-sha256",
                    sha256(reference),
                    "--out-dir",
                    str(out_dir),
                    "--stem",
                    "probe",
                ]
            )

            structural = (out_dir / "neatoos-structural-probe.bin.enc").read_bytes()
            copied_header = (out_dir / "neatoos-reference-header-probe.bin.enc").read_bytes()
            structural_manifest = json.loads(
                (out_dir / "neatoos-structural-probe.bin.enc.manifest.json").read_text()
            )
            reference_manifest = json.loads(
                (out_dir / "neatoos-reference-header-probe.bin.enc.manifest.json").read_text()
            )
            full_length = (
                out_dir / "neatoos-reference-header-full-length-probe.bin.enc"
            ).read_bytes()
            set_manifest = json.loads((out_dir / "probe.manifest.json").read_text())

        self.assertEqual(structural[:512], probe_generator.structural_header(raw))
        self.assertEqual(structural[512 : 512 + len(raw)], raw)
        self.assertEqual(structural[512 + len(raw) :], b"\0" * (512 - len(raw)))
        self.assertEqual(copied_header[:512], reference[:512])
        self.assertEqual(copied_header[512 : 512 + len(raw)], raw)
        self.assertNotEqual(copied_header[512:], reference[512:])

        for manifest in (structural_manifest, reference_manifest):
            self.assertIn("NOT ENCRYPTED", manifest["warnings"])
            self.assertIn("NOT AUTHENTICATED", manifest["warnings"])
            self.assertFalse(manifest["safety"]["encrypted"])
            self.assertFalse(manifest["safety"]["authenticated"])
            self.assertEqual(manifest["encryption_status"], "NOT ENCRYPTED")
            self.assertEqual(manifest["authentication_status"], "NOT AUTHENTICATED")
            self.assertEqual(manifest["payload"]["raw_sha256"], sha256(raw))
            self.assertEqual(manifest["payload"]["padding"], 512 - len(raw))
            self.assertEqual(manifest["output_sha256"], manifest["sha256"])
            self.assertIn("expected_experiment", manifest)

        self.assertEqual(structural_manifest["label"], "raw-structural")
        self.assertEqual(structural_manifest["header"]["declared_length"], len(raw))
        self.assertEqual(
            structural_manifest["header"]["unknown_0x10_0x1f_value"],
            probe_generator.experimental_field(raw).hex(),
        )
        self.assertTrue(structural_manifest["header"]["declared_length_matches_payload"])
        self.assertEqual(reference_manifest["label"], "reference-header")
        self.assertEqual(reference_manifest["header"]["declared_length"], 123456)
        self.assertEqual(
            reference_manifest["header"]["unknown_0x10_0x1f_value"],
            bytes(range(0xA0, 0xB0)).hex(),
        )
        self.assertFalse(reference_manifest["header"]["declared_length_matches_payload"])
        self.assertIn("vendor original", reference_manifest["expected_experiment"])
        self.assertEqual(full_length[: len(copied_header)], copied_header)
        self.assertEqual(len(full_length), len(reference))
        expected_tail = probe_generator.deterministic_filler(
            len(reference) - len(copied_header)
        )
        self.assertEqual(full_length[len(copied_header) :], expected_tail)
        full_manifest = next(
            record
            for record in set_manifest["outputs"]
            if record["label"] == "reference-header-full-length"
        )
        self.assertEqual(full_manifest["size"], len(reference))
        self.assertEqual(full_manifest["sha256"], sha256(full_length))
        self.assertIn("deterministic filler", full_manifest["expected_experiment"])
        self.assertEqual(len(set_manifest["outputs"]), 3)

    def test_probe_selftest_cli_succeeds(self):
        output = subprocess.check_output(
            [sys.executable, str(PROBE), "--self-test"],
            text=True,
        )
        manifest = json.loads(output)
        self.assertEqual(manifest["schema"], "neatoos-probe-self-test/v1")
        self.assertEqual(manifest["outputs"][0]["label"], "raw-structural")
        self.assertEqual(manifest["outputs"][1]["label"], "reference-header")
        self.assertEqual(
            manifest["outputs"][2]["label"], "reference-header-full-length"
        )


if __name__ == "__main__":
    unittest.main()
