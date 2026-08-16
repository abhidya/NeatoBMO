from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SESSION = ROOT / "captures" / "jtag" / "jtag-p10-20260813T061756Z"
COMPARISON = SESSION / "usb-surface-comparison.json"


def load_snapshot(version: str) -> dict[str, object]:
    matches = list(SESSION.glob(f"usb-snapshot-{version}/**/snapshot.json"))
    assert len(matches) == 1
    return json.loads(matches[0].read_text(encoding="utf-8"))


def digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def test_usb_snapshots_cover_clean_room_targets_without_firmware_bytes():
    comparison = json.loads(COMPARISON.read_text(encoding="utf-8"))
    assert set(comparison["snapshots"]) == {"2.5.15893", "2.7.16621", "3.1.17844"}
    assert comparison["usb_identity"]["vid_pid"] == "2108:780B"
    assert not list(SESSION.rglob("*.enc"))
    assert not list(SESSION.rglob("*.bin"))


def test_25_and_27_expose_identical_probed_help_surfaces():
    snapshot_25 = load_snapshot("25")
    snapshot_27 = load_snapshot("27")
    for command in (
        "Help",
        "Help Upload",
        "Help PlaySound",
        "Help SetConfig",
        "Help SetSystemMode",
    ):
        assert snapshot_25["commands"][command] == snapshot_27["commands"][command]


def test_31_surface_delta_is_exactly_preserved_for_rewrite_and_patching():
    snapshot_25 = load_snapshot("25")
    snapshot_31 = load_snapshot("31")
    help_25 = snapshot_25["commands"]["Help"]
    help_31 = snapshot_31["commands"]["Help"]
    upload_25 = snapshot_25["commands"]["Help Upload"]
    upload_31 = snapshot_31["commands"]["Help Upload"]

    for command in ("GetLifeStatLog", "GetSysLog", "SetDistanceCal", "SetWallFollower"):
        assert command in help_25
        assert command not in help_31
    for option in ("dump", "xmodem"):
        assert option in upload_25
        assert option not in upload_31


def test_recorded_reply_hashes_match_raw_snapshot_text():
    comparison = json.loads(COMPARISON.read_text(encoding="utf-8"))
    snapshots = {
        "2.5.15893": load_snapshot("25"),
        "2.7.16621": load_snapshot("27"),
        "3.1.17844": load_snapshot("31"),
    }
    for command, versions in comparison["reply_sha256"].items():
        for version, expected in versions.items():
            assert digest(snapshots[version]["commands"][command]) == expected


def test_controller_recovery_contract_handles_device_disappearance():
    comparison = json.loads(COMPARISON.read_text(encoding="utf-8"))
    requirement = comparison["reenumeration"]["controller_requirement"].lower()
    assert "rediscover" in requirement
    assert "vid:pid" in requirement
    assert "vbus" in requirement
    assert "serial pathname alone is insufficient" in requirement


def test_esp32_controller_binds_only_the_observed_neato_usb_identity():
    header = (ROOT / "esp32-body" / "src" / "neato_usb.h").read_text()
    source = (ROOT / "esp32-body" / "src" / "neato_usb.c").read_text()
    assert "NEATO_USB_VID 0x2108U" in header
    assert "NEATO_USB_PID 0x780BU" in header
    assert "cdc_acm_host_open(NEATO_USB_VID, NEATO_USB_PID" in source
    assert "CDC_HOST_ANY_VID" not in source
    assert "CDC_HOST_ANY_PID" not in source
