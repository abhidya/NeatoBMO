from __future__ import annotations

from pathlib import Path
import struct

import pytest

import tools.neato_upload_save_area_probe as probe


def test_matrix_is_fixed_to_read_or_dump_options():
    assert probe.RAW_COMMANDS
    assert probe.XMODEM_COMMANDS
    for command in probe.RAW_COMMANDS + probe.XMODEM_COMMANDS:
        probe.require_fixed_read_command(command)
        lowered = command.lower()
        assert "size" not in lowered
        assert "reboot" not in lowered
        assert "erase" not in lowered
        assert "noburn" not in lowered


@pytest.mark.parametrize(
    "command",
    ["Upload code reboot", "Upload erase", "Upload code Size 4", "Upload sound"],
)
def test_arbitrary_or_persistent_commands_are_rejected(command: str):
    with pytest.raises(probe.ProbeSafetyError):
        probe.require_fixed_read_command(command)


def test_identity_gate_accepts_only_the_expected_robot_and_supported_versions():
    base = (
        b"Serial Number,WTD41611DD,0037829,P\r\n"
        b"MainBoard Version,7,1,\r\n"
    )
    for version in probe.ALLOWED_SOFTWARE:
        probe.require_identity(base + version)
    with pytest.raises(probe.ProbeSafetyError):
        probe.require_identity(base + b"Software,3,2,18755")


def test_sentinel_is_fixed_printable_project_owned_data():
    assert len(probe.SENTINEL) == 256
    assert probe.SENTINEL.startswith(b"NEATOBMO-UPLOAD-SAVE-AREA-PROBE-V1|")
    assert probe.classify(probe.SENTINEL) == "project-sentinel-returned"
    assert probe.classify(probe.SENTINEL + b"\x00private") == (
        "non-text-private-review-required"
    )


def test_send_sentinel_uses_one_noburn_frame(monkeypatch):
    class Connection:
        def __init__(self):
            self.writes = []

        def reset_input_buffer(self):
            pass

        def write(self, data: bytes):
            self.writes.append(data)

        def flush(self):
            pass

    connection = Connection()
    replies = iter((probe.ENQ, probe.ACK + probe.TERM))
    monkeypatch.setattr(probe, "read_until_quiet", lambda *_args, **_kwargs: next(replies))
    probe.send_sentinel(connection)

    checksum = struct.pack("<I", sum(probe.SENTINEL) & 0xFFFFFFFF)
    assert connection.writes == [
        b"Upload code noburn Size 260\r",
        probe.SENTINEL + checksum,
    ]


def test_result_paths_are_exclusive_in_cli_source():
    source = Path(probe.__file__).read_text()
    assert "refusing to overwrite an existing result" in source
    assert '"auto_retries": 0' in source
    assert '"persistent_write_requested": False' in source


def test_private_binary_or_xmodem_start_stops_and_is_not_embedded():
    records = []
    private = []
    assert probe.preserve_or_publish(
        records, private, "Upload code readflash", b"\x01binary", payload_start=True
    )
    assert private == [("breakthrough-01.bin", b"\x01binary")]
    assert "escaped_text" not in records[0]
    assert records[0]["private_artifact"] == "breakthrough-01.bin"


def test_p6_abort_markers_exclude_expected_noburn_failure():
    assert b"nandflashWrite() fail - -1" not in probe.P6_ABORT_MARKERS
    assert b"nandFlashWrite() OK" in probe.P6_ABORT_MARKERS


def test_abort_is_rechecked_before_post_matrix_queries():
    source = Path(probe.__file__).read_text()
    marker = 'if stopped_reason is None and abort.is_set():'
    assert source.count(marker) >= 2
    assert source.index(marker) < source.index('"GetErr after"')
