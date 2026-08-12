import importlib.util
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "neatoos" / "tools" / "application_probe.py"
spec = importlib.util.spec_from_file_location("neatoos_application_probe", SCRIPT)
probe = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules["neatoos_application_probe"] = probe
spec.loader.exec_module(probe)


def test_command_and_no_retry_are_fixed():
    assert probe.COMMAND == "Upload code reboot"
    source = SCRIPT.read_text()
    assert '"automatic_retry": False' in source
    assert "Upload sound" not in source
    assert "Upload code noburn" not in source


def test_confirmation_is_bound_to_payload_hash():
    payload = b"canary"
    required = f"FLASH NEATOOS {probe.sha256(payload)}"
    assert required == (
        "FLASH NEATOOS "
        "e100fbce008c04ec40637af0af91fb2f05aeedc23f856a2d3c0b1580625d755e"
    )


def test_factory_and_installed_updaters_are_accepted_for_recovery_sequence():
    identity = "Serial Number,WTD41611DD,0037829,P\r\n"
    probe.require_recovery_target(identity + "Software,2,4,15667\r\n")
    probe.require_recovery_target(identity + "Software,2,5,15893\r\n")
