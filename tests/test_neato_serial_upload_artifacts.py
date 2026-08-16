from __future__ import annotations

import hashlib
import json
from pathlib import Path


SESSION = (
    Path(__file__).parents[1]
    / "captures"
    / "serial-upload"
    / "serial-upload-20260816T045102Z"
)


def load(name: str) -> dict:
    return json.loads((SESSION / name).read_text())


def sha256(name: str) -> str:
    return hashlib.sha256((SESSION / name).read_bytes()).hexdigest()


def test_manifest_indexes_the_complete_transition_and_control_sequence():
    manifest = load("manifest.json")
    assert manifest["collection_complete"] is True
    rows = {row["name"]: row for row in manifest["rows"]}
    assert {
        "stock-25-initial",
        "stock-25-size-qualified",
        "exact-stock-27-write",
        "stock-27-matrix",
        "exact-stock-31-write",
        "stock-31-first-receiver-fork",
        "stock-31-reordered-completion",
        "exact-stock-25-restore",
        "stock-25-final-matrix",
        "exact-vendor-default-sound-write",
        "stock-25-post-sound-matrix",
        "final-health",
    } == set(rows)
    for row in rows.values():
        assert sha256(row["result"]) == row["result_sha256"]


def test_exact_stock_transitions_are_acknowledged_once_and_end_on_25():
    expected = {
        "firmware-27-write-result.json": (
            "2e6033b1ef5440bed949de20e89563d7cb3dda41e0eb5e371c9d86dceeb1633f",
            "Software,2,7,16621",
        ),
        "firmware-31-write-result.json": (
            "03396329a1a47a7358d09bd414d01eddaa5806a50a18f4d9ce2f96edc2d5fab7",
            None,
        ),
        "firmware-25-restore-result.json": (
            "e1a31ef56e2b617a4056d70c308aab26b7e2cd95e679cb982d697ff4f089c697",
            "Software,2,5,15893",
        ),
    }
    for name, (image_hash, post_version) in expected.items():
        result = load(name)
        assert result["command"] == "Upload code reboot"
        assert result["image_sha256"] == image_hash
        assert result["closing_hex"] == "06"
        assert result["auto_retries"] == 0
        assert result["writes_performed"] is True
        if post_version:
            assert post_version in result["version_after"]

    assert "Software,3,1,17844" in load("stock-31-result.json")["records"][0][
        "escaped_text"
    ]
    final = load("final-health-result.json")
    assert final["target_identity_verified"] is True
    assert final["installed_application"] == "2.5.15893"
    assert final["factory_fallback_observed"] is False


def test_stock_matrices_do_not_claim_readback_or_p6_output():
    for name in (
        "stock-25-result.json",
        "stock-25-size-result.json",
        "stock-27-result.json",
        "stock-25-final-result.json",
        "stock-25-post-sound-result.json",
    ):
        result = load(name)
        assert result["p6_bytes"] == 0
        assert all(not row.get("xmodem_payload_start") for row in result["records"])
        assert all(
            row["classification"] != "non-text-private-review-required"
            for row in result["records"]
        )


def test_31_receiver_forks_are_fail_closed_not_readback():
    for name, command in (
        ("stock-31-result.json", "Upload code dump Size 260"),
        ("stock-31-completion-result.json", "Upload sound dump Size 260"),
    ):
        result = load(name)
        assert result["stopped_reason"] == (
            "size query selected upload receiver; cancel not confirmed"
        )
        row = next(row for row in result["records"] if row["command"] == command)
        assert row["target_requested_upload_bytes"] is True
        assert row["upload_cancel_confirmed"] is False
        assert row["classification"] == "upload-receiver-cancel-unconfirmed-private"
        assert "escaped_text" not in row


def test_vendor_sound_write_matches_known_slot_map():
    result = load("sound-default-write-result.json")
    assert result["image_sha256"] == (
        "d3969779a6a195812d72b6859454de004ea45beefdee6f1b5c50a2632564b64a"
    )
    assert result["closing_hex"] == "061a"
    assert result["version_before_ok"] is True
    assert result["version_after_ok"] is True
    accepted = {row["sound_id"] for row in result["sound_sweep"] if row["accepted"]}
    assert accepted == {0, 1, 2, 3, 6, 7, 8, 9, 10, 19}


def test_manifest_preserves_the_destructive_boundaries():
    actions = set(load("manifest.json")["actions_not_performed"])
    assert {
        "arbitrary or unknown-image write",
        "bare serial erase",
        "J3/AT91 ERASE",
        "GPNVM change",
        "3.2 image transfer",
        "full firmware or NAND readback",
    } <= actions
