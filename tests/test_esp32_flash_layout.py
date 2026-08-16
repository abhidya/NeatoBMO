"""Deployment invariants for the custom dual-OTA ESP32 partition layout."""
import configparser
import csv
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
ESP32 = ROOT / "esp32-body"


class Esp32FlashLayoutTests(unittest.TestCase):
    def test_platformio_upload_offset_matches_first_ota_partition(self):
        config = configparser.ConfigParser()
        config.read(ESP32 / "platformio.ini")
        upload_offset = int(
            config["env:esp32s3"]["board_upload.offset_address"], 0)

        with (ESP32 / "partitions.csv").open(newline="") as handle:
            rows = [row for row in csv.reader(handle)
                    if row and not row[0].lstrip().startswith("#")]
        ota_0 = next(row for row in rows
                     if row[1].strip() == "app" and
                     row[2].strip() == "ota_0")

        self.assertEqual(upload_offset, int(ota_0[3].strip(), 0))


if __name__ == "__main__":
    unittest.main()
