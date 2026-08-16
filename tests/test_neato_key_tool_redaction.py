from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_dump_verifier_redacts_recovered_key() -> None:
    module = load("verify_firmware_dump", "tools/jtag/verify_firmware_dump.py")
    raw = "00112233445566778899aabbccddeeff"
    public = module.public_hits([{"offset": 64, "key_hex": raw}])
    assert public[0]["offset"] == 64
    assert len(public[0]["key_sha256"]) == 64
    assert raw not in repr(public)


def test_candidate_sweep_redacts_key_and_seed() -> None:
    module = load("neato_key_candidates", "tools/neato_key_candidates.py")
    raw = "00112233445566778899aabbccddeeff"
    hits = [{
        "seed": "secret seed",
        "variant": "secret variant",
        "derivation": "md5",
        "iv": "zeros",
        "key_hex": raw,
        "score": {"looks_like_arm_firmware": True},
    }]
    public = module.public_hits(hits)
    rendered = repr(public)
    assert len(public[0]["key_sha256"]) == 64
    assert raw not in rendered
    assert "secret seed" not in rendered
    assert "secret variant" not in rendered
