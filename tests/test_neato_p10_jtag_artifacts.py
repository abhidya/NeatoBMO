from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CFG = ROOT / "tools" / "jtag" / "neato_p10_autoprobe.cfg"
RUNNER = ROOT / "tools" / "jtag" / "run_neato_p10_autoprobe.sh"
P6_TRIGGER = ROOT / "tools" / "jtag" / "neato_p10_p6_trigger.py"
EDGE_WATCH = ROOT / "tools" / "jtag" / "neato_p10_boot_edge_watch.py"
TRANSITION_CAPTURE = ROOT / "tools" / "jtag" / "neato_firmware_transition_capture.py"
SESSION = ROOT / "captures" / "jtag" / "jtag-p10-20260813T061756Z"

FORBIDDEN_OPENOCD_PATTERNS = (
    r"\berase\b",
    r"\bprogram\b",
    r"\bflash\s+write_image\b",
    r"\bflash\s+erase\b",
    r"\bnand\s+write\b",
    r"\bload_image\b",
    r"\bmww\b",
    r"\bmwh\b",
    r"\bmwb\b",
    r"\bwrite_memory\b",
    r"\bat91sam9\s+gpnvm\b",
    r"\breset\s+init\b",
    r"\bhalt\b",
    r"\btarget\s+create\b",
    r"\bjtag\s+newtap\b",
)


def uncommented_text(path: Path) -> str:
    lines = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        lines.append(line)
    return "\n".join(lines)


def test_autoprobe_config_is_adapter_only_for_openocd_012() -> None:
    text = uncommented_text(CFG)
    assert "adapter driver cmsis-dap" in text
    assert "transport select jtag" in text
    assert "adapter speed 10" in text
    assert "cmsis-dap backend usb_bulk" not in text
    assert "init" not in text
    assert "scan_chain" not in text
    for pattern in FORBIDDEN_OPENOCD_PATTERNS:
        assert re.search(pattern, text) is None


def test_capture_helpers_only_run_scan_chain_openocd_sequence() -> None:
    for path in (RUNNER, P6_TRIGGER, EDGE_WATCH):
        text = uncommented_text(path)
        assert "init; scan_chain; shutdown" in text
        assert "cmsis-dap backend usb_bulk" not in text
        for pattern in FORBIDDEN_OPENOCD_PATTERNS:
            assert re.search(pattern, text) is None, f"{pattern} present in {path}"


def test_transition_capture_helper_cannot_initiate_neato_transfer() -> None:
    text = TRANSITION_CAPTURE.read_text(encoding="utf-8")
    assert "Upload code reboot" not in text
    assert "send_binary" not in text
    assert "SerialTransport" not in text
    assert "execute_destructive" not in text
    assert "cmsis-dap backend usb_bulk" not in text

    import importlib.util
    import sys

    spec = importlib.util.spec_from_file_location(
        "neato_firmware_transition_capture", TRANSITION_CAPTURE
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    command = module.openocd_command(10)
    assert command == [
        "openocd",
        "-c",
        "adapter driver cmsis-dap",
        "-c",
        "transport select jtag",
        "-c",
        "adapter speed 10",
        "-c",
        "init; scan_chain; shutdown",
    ]


def test_phase_runner_supports_before_during_after_labels_without_overwrite() -> None:
    text = RUNNER.read_text(encoding="utf-8")
    assert "--label pre-update" in text
    assert "--label during-update" in text
    assert "[[ -e \"$log\" ]]" in text
    assert "refusing to overwrite existing log" in text
    assert "openocd-${label}-${speed}khz-run${run}.log" in text


def test_session_contains_repeated_p10_and_p6_evidence() -> None:
    assert SESSION.is_dir()
    logs = sorted(path.name for path in SESSION.glob("openocd-*.log"))
    raws = sorted(path.name for path in SESSION.glob("p6-trigger-*.raw"))
    assert len(logs) >= 20
    assert any("normal-coldboot" in name for name in logs)
    assert any("factory" in name for name in logs)
    assert any("tdo-pullup-target-powered" in name for name in logs)
    assert any("tdo-pullup-target-off" in name for name in logs)
    assert len(raws) >= 4


def test_existing_openocd_logs_do_not_claim_a_detected_tap() -> None:
    stable_idcode_recommendations = []
    for log in SESSION.glob("openocd-*.log"):
        text = log.read_text(encoding="utf-8", errors="replace")
        assert "flash write_image" not in text
        assert "Info : TAP auto" not in text
        if "-expected-id" in text:
            stable_idcode_recommendations.append(log.name)
    assert stable_idcode_recommendations == []
