import os
import subprocess
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
FIRMWARE_SRC = REPO / "esp32-body" / "src"


def test_usb_health_policy(tmp_path: Path) -> None:
    executable = tmp_path / "neato-usb-health-test"
    subprocess.check_call(
        [
            os.environ.get("CC", "cc"),
            "-std=c11",
            "-Wall",
            "-Wextra",
            "-Werror",
            f"-I{FIRMWARE_SRC}",
            str(REPO / "tests" / "test_neato_usb_health.c"),
            str(FIRMWARE_SRC / "neato_usb_health.c"),
            "-o",
            str(executable),
        ]
    )
    subprocess.check_call([str(executable)])
