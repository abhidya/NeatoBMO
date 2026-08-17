"""BMO "decrypt my software": the real Cruz .enc unlock attempt, honestly gated.

The robot's application images are a ``neato`` format-2 envelope: a 512-byte
plaintext header followed by one AES-128-CBC stream (fixed key, fixed IV) to
EOF.  The key is not public, so the only bounded attacks are:

  * a low-entropy key-derivation sweep (``tools/neato_key_candidates.py``:
    raw-pad / MD5 / SHA1 / SHA256 over human seeds), and
  * a key recovered out-of-band — the JTAG/SRAM AES key-schedule search in
    ``tools/jtag/verify_firmware_dump.py``.

This module is the persona's "tool call" for that work.  It never flashes,
uploads, patches, or erases anything: it decrypts a caller-supplied ``.enc``
image in memory and only accepts a result that passes the *same* structural
validation the archive uses (``neato_firmware.validate_plaintext``), never a
heuristic.  On a genuine pass it:

  1. writes the recovered plaintext + a redacted JSON report to local storage
     (BMO's SSD archive first, an in-repo scratch dir as fallback),
  2. pushes both to the ESP32 body's web portal (``Esp32Client.put_file``),
  3. returns a stage-cue reply so the celebration — the party face, the dance,
     the "yeah" — rides the ordinary ``cues`` pipeline.

No hit -> a humble cue reply, and a report that says what was tried.  The
recovered key is never written anywhere; only its SHA-256 appears in reports,
matching the archive's redaction policy.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
import struct
import sys
import time

REPO_ROOT = Path(__file__).resolve().parent.parent
_TOOLS = REPO_ROOT / "tools"
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

HEADER_SIZE = 512
DEFAULT_OUTPUT_DIR = REPO_ROOT / "tmp" / "bmo-decrypt"
DEFAULT_SSD_DIR = Path("/Volumes/2TB/neato-firmware-archive/work/plaintext-candidates")

SUCCESS_REPLY = "BMO cracked it! [party] [dance] [sound:yeah]"
LOCKED_REPLY = "Still locked! [sad] [look] [sound:beep]"
BROKEN_REPLY = "BMO brain jammed! [sad] [sound:beep]"


@dataclass
class DecryptResult:
    succeeded: bool
    image_path: str
    plaintext: bytes | None
    report: dict
    reply: str
    written: dict = field(default_factory=dict)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def key_bytes(key) -> bytes:
    """Coerce a provided AES-128 key to 16 bytes (bytes, or a hex string)."""
    if isinstance(key, (bytes, bytearray)):
        raw = bytes(key)
    elif isinstance(key, str):
        raw = bytes.fromhex(key.replace(" ", "").replace(":", ""))
    else:
        raise TypeError("key must be 16 bytes or a hex string")
    if len(raw) != 16:
        raise ValueError(f"AES-128 key must be 16 bytes, got {len(raw)}")
    return raw


def _resolve_output_dir(output_dir) -> Path | None:
    """Pick a writable place for the recovered artifacts.

    Prefer the caller's choice, then BMO's SSD archive, then the in-repo
    scratch dir (git-ignored).  Return None if nothing is writable — the
    attempt still succeeds; only the on-disk save is skipped.
    """
    candidates = []
    if output_dir is not None:
        candidates.append(Path(output_dir))
    candidates.append(DEFAULT_SSD_DIR)
    candidates.append(DEFAULT_OUTPUT_DIR)
    for path in candidates:
        try:
            path.mkdir(parents=True, exist_ok=True)
            probe = path / ".bmo-decrypt-write-probe"
            probe.write_bytes(b"")
            probe.unlink()
            return path
        except OSError:
            continue
    return None


def _write_artifacts(output_dir: Path, image: Path, plaintext: bytes,
                     report: dict) -> dict:
    written = {}
    stem = image.stem or "firmware"
    plaintext_name = f"{stem}.decrypted.bin"
    report_name = f"{stem}.decrypt-report.json"
    (output_dir / plaintext_name).write_bytes(plaintext)
    (output_dir / report_name).write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n")
    written["local_plaintext"] = str(output_dir / plaintext_name)
    written["local_report"] = str(output_dir / report_name)
    return written


def _upload_artifacts(esp32, plaintext: bytes, report: dict) -> dict:
    written = {}
    esp32.put_file("bmo-decrypted.bin", plaintext)
    written["esp32_plaintext"] = "bmo-decrypted.bin"
    esp32.put_file("bmo-decrypt-report.json",
                   json.dumps(report, indent=2, sort_keys=True).encode())
    written["esp32_report"] = "bmo-decrypt-report.json"
    return written


def _load_crypto():
    """Return (AES, neato_firmware, neato_key_candidates) or None.

    Import is lazy + forgiving: the tools/ scripts raise SystemExit when
    pycryptodome is missing, so this must never run at module import time.
    """
    from Crypto.Cipher import AES  # noqa: F401
    import neato_firmware
    import neato_key_candidates
    return AES, neato_firmware, neato_key_candidates


def attempt(image_path, *, key=None, esp32=None, output_dir=None,
            extra_seeds=None) -> DecryptResult:
    """Run the real decrypt attempt and (on a genuine hit) celebrate + save.

    Returns a DecryptResult whose ``reply`` is stage-cue text.  ``esp32`` is
    any object with ``put_file(name, bytes)``; pass the real Esp32Client to
    publish the recovery on the body's web portal, or None to stay local.
    """
    started = time.time()
    image = Path(image_path) if image_path is not None else None
    base_report = {
        "attempted_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "image": str(image) if image is not None else None,
        "succeeded": False,
        "elapsed_seconds": 0.0,
    }

    def fail(report, reply):
        report["elapsed_seconds"] = round(time.time() - started, 3)
        return DecryptResult(False, str(image), None, report, reply)

    if image is None or not image.is_file():
        base_report["error"] = "no firmware image to decrypt"
        return fail(base_report, LOCKED_REPLY)

    try:
        data = image.read_bytes()
    except OSError as exc:
        base_report["error"] = f"image unreadable: {exc}"
        return fail(base_report, LOCKED_REPLY)

    if len(data) <= HEADER_SIZE:
        base_report["error"] = "image shorter than the 512-byte header"
        return fail(base_report, LOCKED_REPLY)

    header = data[:HEADER_SIZE]
    payload = data[HEADER_SIZE:]
    declared = struct.unpack_from("<I", header)[0]
    base_report.update(
        image_sha256=_sha256(data),
        declared_plaintext_size=declared,
        payload_size=len(payload),
    )

    try:
        AES, neato_firmware, neato_key_candidates = _load_crypto()
    except BaseException as exc:
        base_report["error"] = f"crypto tools unavailable: {exc}"
        return fail(base_report, BROKEN_REPLY)

    ivs = {
        "zeros": b"\x00" * 16,
        "header_16_32": header[16:32],
        "header_0_16": header[0:16],
        "ff": b"\xff" * 16,
    }

    # Candidate keys: an explicit recovered key, then the low-entropy sweep.
    candidates: list[tuple[bytes, str]] = []
    if key is not None:
        try:
            candidates.append((key_bytes(key), "provided"))
        except (TypeError, ValueError) as exc:
            base_report["error"] = f"bad provided key: {exc}"
            return fail(base_report, LOCKED_REPLY)

    try:
        seeds = list(neato_key_candidates.DEFAULT_SEEDS) + list(extra_seeds or [])
        hits, checked = neato_key_candidates.run(image, seeds, fast=True)
        base_report["low_entropy_candidates_checked"] = checked
        for hit in hits:
            candidates.append(
                (bytes.fromhex(hit["key_hex"]),
                 f"low-entropy:{hit.get('derivation', '?')}:{hit.get('iv', '?')}"))
    except BaseException as exc:
        base_report["low_entropy_sweep_error"] = str(exc)

    image_obj = neato_firmware.EncryptedImage.read(image)
    base_report["candidate_keys"] = len(candidates)

    for candidate_key, provenance in candidates:
        for iv_name, iv in ivs.items():
            plaintext = AES.new(candidate_key, AES.MODE_CBC, iv).decrypt(payload)
            validation = neato_firmware.validate_plaintext(image_obj, plaintext)
            if not validation["passed"]:
                continue

            # Genuine, structurally-validated unlock. Recover, save, publish.
            recovered = plaintext[:declared]
            report = dict(base_report)
            report["succeeded"] = True
            report["key_sha256"] = _sha256(candidate_key)   # redacted: no key
            report["key_provenance"] = provenance
            report["iv"] = iv_name
            report["plaintext_sha256"] = validation["plaintext_sha256"]
            report["validation"] = validation

            written = {}
            out_dir = _resolve_output_dir(output_dir)
            if out_dir is not None:
                written.update(_write_artifacts(out_dir, image, recovered, report))
            if esp32 is not None:
                try:
                    written.update(_upload_artifacts(esp32, recovered, report))
                except Exception as exc:  # portal must never fail the recovery
                    report["esp32_upload_error"] = str(exc)
            report["written"] = written
            report["elapsed_seconds"] = round(time.time() - started, 3)
            return DecryptResult(True, str(image), recovered, report, SUCCESS_REPLY,
                                 written)

    report = dict(base_report)
    report["candidate_keys"] = len(candidates)
    report["reason"] = "no candidate key produced structurally valid plaintext"
    return fail(report, LOCKED_REPLY)
