import importlib.util
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "neatoos" / "tools" / "header_field_probe.py"
spec = importlib.util.spec_from_file_location("neatoos_header_field_probe", SCRIPT)
probe = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules["neatoos_header_field_probe"] = probe
spec.loader.exec_module(probe)


class FakeTransport:
    def __init__(self, port):
        self.port = port
        self.binary_calls = []
        self.closed = False

    def send(self, command, timeout):
        assert command == "GetVersion"
        return (
            "Serial Number,WTD41611DD,0037829,P\r\n"
            "Software,2,5,15893\r\n"
        )

    def send_binary(self, command, payload, timeout, allow_terminator):
        self.binary_calls.append((command, payload, timeout, allow_terminator))
        return b"\x1a"

    def close(self):
        self.closed = True


def test_mutation_changes_exactly_one_fixed_header_bit():
    base = bytes(range(64))
    mutated = probe.mutate_header_field(base)
    differences = [index for index, pair in enumerate(zip(base, mutated)) if pair[0] != pair[1]]
    assert differences == [0x18]
    assert base[0x18] ^ mutated[0x18] == 0x01
    assert mutated[:0x18] == base[:0x18]
    assert mutated[0x19:] == base[0x19:]


def test_wrong_base_is_rejected_before_probe_construction():
    try:
        probe.build_probe(b"not stock")
    except probe.HeaderFieldProbeError as error:
        assert "refusing base" in str(error)
    else:
        raise AssertionError("wrong base was accepted")


def test_noburn_uses_only_transport_path_and_preserves_payload():
    created = []

    def factory(port):
        transport = FakeTransport(port)
        created.append(transport)
        return transport

    payload = bytes(range(64))
    result = probe.run_noburn(port="fake", payload=payload, transport_factory=factory)
    assert result["command"] == "Upload code noburn"
    assert result["writes_requested"] is False
    assert result["automatic_retry"] is False
    assert created[0].binary_calls == [("Upload code noburn", payload, 30.0, True)]
    assert created[0].closed is True


def test_confirmation_is_bound_to_exact_mutated_payload():
    assert probe.confirmation_for(b"probe") == (
        "FLASH NEATO HEADER FIELD PROBE "
        "ba9c736f19e7f60b7f6764adb0b7908c0a2b394e09b6c09863528c7f2bc86095"
    )


def test_script_has_no_retry_loop_or_factory_region_command():
    source = SCRIPT.read_text()
    assert '"automatic_retry": False' in source
    assert "while True" not in source
    assert "Upload sound" not in source
