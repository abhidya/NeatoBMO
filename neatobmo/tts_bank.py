"""Guarded TTS-to-sound-bank pipeline for the XV-12.

Turns arbitrary text into a *temporary* PCM-only sound bank: TTS WAV ->
normalized 22050 Hz mono s16le PCM -> boundary-aware split across the ten
live slots -> byte-exact overlay on the persistent BMO bank -> validated
image -> confirmed burn -> duration-paced playback -> verified BMO restore.

Everything here is hardware-free and injectable: the ``BankBurner`` takes a
robot-shaped object and a sleep function, so unit tests never open a serial
port.  The only bytes that may ever change relative to the baseline image are
inside the original PCM payload fields; directory pages, the slot table, page
starts, record headers, declared lengths, and inter-record padding are proven
invariants (see SOUND_BANK_WRITE_GATES.md) and validation enforces them.
"""

from __future__ import annotations

import hashlib
import io
import re
import struct
import time
import wave
from array import array
from dataclasses import dataclass, field
from pathlib import Path

PAGE_SIZE = 512
AUDIO_START_PAGE = 8
RECORD_HEADER_SIZE = 16
RECORD_FLAGS = b"\x01\x01"
PCM_SAMPLE_RATE = 22_050
PCM_BYTES_PER_SAMPLE = 2
EXPECTED_BANK_BYTES = 770_048

# Playback order chosen for the speech stream: long slots first, the two
# 0.32 s reaction slots last.  Verified live map on WTD41611DD-0037829-P.
SLOT_SEQUENCE = (0, 1, 2, 6, 7, 8, 9, 10, 3, 19)
LIVE_SOUND_IDS = frozenset(SLOT_SEQUENCE)

BMO_BANK_SHA256 = "9d3d82d9275c03fa9f2abb163cdfd9393445737999916f6337d2d6b639b51159"
ORIGINAL_BANK_SHA256 = "d3969779a6a195812d72b6859454de004ea45beefdee6f1b5c50a2632564b64a"
VERSION_REQUIRED_SUBSTRINGS = ("WTD41611DD", "Software,2,4,15667")

# -1 dBFS peak target; validation additionally rejects any full-scale sample.
NORMALIZE_PEAK = int(32767 * 10 ** (-1.0 / 20.0))
TRIM_THRESHOLD = 330            # ~1% full scale
TRIM_KEEP_SECONDS = 0.012
FADE_SECONDS = 0.004
SPLIT_MIN_FILL = 0.55           # cut search window starts at 55% of capacity
SPLIT_RMS_WINDOW = 220          # ~10 ms energy window for boundary search
SPLIT_RMS_STRIDE = 55


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def additive_checksum_hex(data: bytes) -> str:
    return f"0x{(sum(data) & 0xFFFFFFFF):08x}"


class TtsBankError(ValueError):
    """A recoverable pipeline error (bad audio, over-capacity text, ...)."""


class BankValidationError(TtsBankError):
    """The built image failed byte-exact validation; it must not be burned."""


class RobotVerificationError(RuntimeError):
    """Post-write identity or slot-map verification failed."""


class RestoreArtifactError(RuntimeError):
    """The saved BMO artifact does not match its recorded size/hash."""


# ---------------------------------------------------------------------------
# Bank structure (bytes-based mirror of tools/neato_sound_bank.py, proven
# against the shipped artifact by tests/test_tts_bank.py)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SlotRecord:
    sound_id: int
    start_page: int
    record_offset: int
    record_capacity_bytes: int
    pcm_offset: int
    sample_count: int
    pcm_byte_count: int

    @property
    def duration_seconds(self) -> float:
        return round(self.sample_count / PCM_SAMPLE_RATE, 6)


def record_ranges_from_bytes(data: bytes) -> list[SlotRecord]:
    if len(data) % PAGE_SIZE or not data:
        raise TtsBankError("sound bank must be non-empty and 512-byte aligned")
    if data[:2] != b"KT":
        raise TtsBankError("sound bank does not begin with the KT marker")
    page_count = len(data) // PAGE_SIZE
    entry_count = struct.unpack_from("<H", data, 4)[0] + 1
    if 8 + entry_count * 2 > PAGE_SIZE:
        raise TtsBankError("declared sound-table length does not fit in header page")
    table = struct.unpack_from(f"<{entry_count}H", data, 8)
    slots = [(sound_id, page) for sound_id, page in enumerate(table)
             if AUDIO_START_PAGE <= page < page_count]
    records: list[SlotRecord] = []
    for (sound_id, start_page), (_, end_page) in zip(
            slots, slots[1:] + [(-1, page_count)]):
        record_offset = start_page * PAGE_SIZE
        capacity = (end_page - start_page) * PAGE_SIZE
        header = data[record_offset:record_offset + RECORD_HEADER_SIZE]
        if header[:2] != RECORD_FLAGS:
            raise TtsBankError(f"slot {sound_id}: unsupported record flags")
        sample_rate = struct.unpack_from("<H", header, 2)[0]
        sample_count = struct.unpack_from("<I", header, 4)[0]
        pcm_byte_count = struct.unpack_from("<I", header, 8)[0]
        if sample_rate != PCM_SAMPLE_RATE:
            raise TtsBankError(f"slot {sound_id}: unsupported sample rate {sample_rate}")
        if pcm_byte_count != sample_count * PCM_BYTES_PER_SAMPLE:
            raise TtsBankError(f"slot {sound_id}: PCM byte count mismatch")
        if RECORD_HEADER_SIZE + pcm_byte_count > capacity:
            raise TtsBankError(f"slot {sound_id}: PCM exceeds record capacity")
        records.append(SlotRecord(sound_id, start_page, record_offset, capacity,
                                  record_offset + RECORD_HEADER_SIZE,
                                  sample_count, pcm_byte_count))
    return records


# ---------------------------------------------------------------------------
# Audio processing
# ---------------------------------------------------------------------------

def decode_wav(wav_bytes: bytes) -> array:
    """Decode a WAV into 22050 Hz mono s16 samples (downmix + resample)."""
    with wave.open(io.BytesIO(wav_bytes), "rb") as wav:
        channels = wav.getnchannels()
        width = wav.getsampwidth()
        rate = wav.getframerate()
        if wav.getcomptype() != "NONE":
            raise TtsBankError("compressed WAV is not supported")
        if width != PCM_BYTES_PER_SAMPLE:
            raise TtsBankError(f"expected 16-bit WAV, got {width * 8}-bit")
        frames = wav.readframes(wav.getnframes())
    samples = array("h")
    samples.frombytes(frames[:len(frames) - len(frames) % (width * channels)])
    if channels > 1:
        mono = array("h", (0 for _ in range(len(samples) // channels)))
        for i in range(len(mono)):
            base = i * channels
            mono[i] = sum(samples[base:base + channels]) // channels
        samples = mono
    if rate != PCM_SAMPLE_RATE:
        samples = resample_linear(samples, rate, PCM_SAMPLE_RATE)
    return samples


def resample_linear(samples: array, src_rate: int, dst_rate: int) -> array:
    if src_rate <= 0:
        raise TtsBankError("invalid WAV sample rate")
    if not len(samples):
        return array("h")
    out_len = max(1, int(round(len(samples) * dst_rate / src_rate)))
    out = array("h", bytes(2 * out_len))
    scale = (len(samples) - 1) / max(1, out_len - 1)
    for i in range(out_len):
        pos = i * scale
        left = int(pos)
        frac = pos - left
        right = min(left + 1, len(samples) - 1)
        out[i] = int(samples[left] * (1.0 - frac) + samples[right] * frac)
    return out


def trim_silence(samples: array, threshold: int = TRIM_THRESHOLD,
                 keep_seconds: float = TRIM_KEEP_SECONDS) -> array:
    first = next((i for i, s in enumerate(samples) if abs(s) > threshold), None)
    if first is None:
        raise TtsBankError("synthesized audio is silent")
    last = next(i for i in range(len(samples) - 1, -1, -1)
                if abs(samples[i]) > threshold)
    keep = int(keep_seconds * PCM_SAMPLE_RATE)
    return samples[max(0, first - keep):min(len(samples), last + 1 + keep)]


def normalize_peak(samples: array, target: int = NORMALIZE_PEAK) -> array:
    peak = max(abs(s) for s in samples)
    if peak == 0:
        raise TtsBankError("synthesized audio is silent")
    gain = target / peak
    return array("h", (int(max(-32768, min(32767, round(s * gain))))
                       for s in samples))


def prepare_speech_pcm(wav_bytes: bytes) -> array:
    return normalize_peak(trim_silence(decode_wav(wav_bytes)))


# ---------------------------------------------------------------------------
# Slot planning
# ---------------------------------------------------------------------------

@dataclass
class SlotSegment:
    index: int
    sound_id: int
    capacity_samples: int
    pcm: bytes
    text_fragment: str = ""
    faded_out: bool = False
    faded_in: bool = False

    @property
    def sample_count(self) -> int:
        return len(self.pcm) // PCM_BYTES_PER_SAMPLE

    @property
    def content_seconds(self) -> float:
        return round(self.sample_count / PCM_SAMPLE_RATE, 6)

    @property
    def slot_seconds(self) -> float:
        return round(self.capacity_samples / PCM_SAMPLE_RATE, 6)

    @property
    def padding_seconds(self) -> float:
        return round((self.capacity_samples - self.sample_count)
                     / PCM_SAMPLE_RATE, 6)


def _quietest_cut(samples: array, lo: int, hi: int) -> int:
    """Index in [lo, hi] with the lowest short-window energy (split-friendly)."""
    best_pos, best_energy = hi, None
    for pos in range(hi, lo - 1, -SPLIT_RMS_STRIDE):
        window = samples[max(0, pos - SPLIT_RMS_WINDOW):pos]
        if not len(window):
            continue
        energy = sum(s * s for s in window) / len(window)
        if best_energy is None or energy < best_energy:
            best_pos, best_energy = pos, energy
    return best_pos


def _fade(samples: list[int], fade_in: bool) -> None:
    n = min(len(samples), int(FADE_SECONDS * PCM_SAMPLE_RATE))
    for i in range(n):
        j = i if fade_in else len(samples) - 1 - i
        samples[j] = int(samples[j] * i / n)


def plan_segments(samples: array,
                  slot_capacities: list[tuple[int, int]]) -> list[SlotSegment]:
    """Split speech across slots, cutting at the quietest point near capacity.

    ``slot_capacities`` is [(sound_id, capacity_samples), ...] in playback
    order.  Raises if the speech cannot fit; never truncates silently.
    """
    total_capacity = sum(cap for _, cap in slot_capacities)
    if len(samples) > total_capacity:
        raise TtsBankError(
            f"speech is {len(samples) / PCM_SAMPLE_RATE:.3f}s but the bank "
            f"holds at most {total_capacity / PCM_SAMPLE_RATE:.3f}s")
    segments: list[SlotSegment] = []
    pos = 0
    for index, (sound_id, capacity) in enumerate(slot_capacities):
        if pos >= len(samples):
            break
        remaining = len(samples) - pos
        if remaining <= capacity:
            cut = len(samples)
        else:
            cut = _quietest_cut(samples, pos + int(capacity * SPLIT_MIN_FILL),
                                pos + capacity)
        chunk = list(samples[pos:cut])
        faded_in = index > 0
        faded_out = cut < len(samples)
        if faded_in:
            _fade(chunk, fade_in=True)
        if faded_out:
            _fade(chunk, fade_in=False)
        segments.append(SlotSegment(index=index, sound_id=sound_id,
                                    capacity_samples=capacity,
                                    pcm=array("h", chunk).tobytes(),
                                    faded_in=faded_in, faded_out=faded_out))
        pos = cut
    if pos < len(samples):
        raise TtsBankError(
            "speech does not fit the slot sequence with clean split "
            "boundaries; shorten the text")
    return segments


def attach_text_fragments(segments: list[SlotSegment], text: str) -> None:
    """Attribute words to segments proportionally to their durations."""
    words = text.split()
    total = sum(seg.sample_count for seg in segments) or 1
    start = 0
    consumed = 0
    for i, seg in enumerate(segments):
        consumed += seg.sample_count
        end = len(words) if i == len(segments) - 1 else round(
            len(words) * consumed / total)
        seg.text_fragment = " ".join(words[start:end])
        start = end


# ---------------------------------------------------------------------------
# Bank build + validation
# ---------------------------------------------------------------------------

def pcm_to_wav_bytes(pcm: bytes) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(PCM_BYTES_PER_SAMPLE)
        wav.setframerate(PCM_SAMPLE_RATE)
        wav.writeframes(pcm)
    return buffer.getvalue()


def build_tts_bank(baseline: bytes, segments: list[SlotSegment],
                   unused_slots: str = "silence") -> tuple[bytes, dict]:
    """Overlay segment PCM onto the baseline image, PCM fields only.

    ``unused_slots`` is "silence" (zero the PCM of live slots that carry no
    speech) or "keep" (retain the baseline PCM).  Recorded in the manifest.
    """
    if unused_slots not in ("silence", "keep"):
        raise TtsBankError("unused_slots must be 'silence' or 'keep'")
    records = {r.sound_id: r for r in record_ranges_from_bytes(baseline)}
    by_id = {seg.sound_id: seg for seg in segments}
    unknown = sorted(set(by_id) - set(records))
    if unknown:
        raise TtsBankError(f"segments target absent sound IDs: {unknown}")
    output = bytearray(baseline)
    manifest_slots = []
    for sound_id in sorted(records):
        record = records[sound_id]
        seg = by_id.get(sound_id)
        if seg is not None:
            if len(seg.pcm) > record.pcm_byte_count:
                raise TtsBankError(
                    f"segment for slot {sound_id} is {len(seg.pcm)} bytes; "
                    f"slot PCM field holds {record.pcm_byte_count}")
            payload = seg.pcm + bytes(record.pcm_byte_count - len(seg.pcm))
            role = "speech"
        elif unused_slots == "silence":
            payload = bytes(record.pcm_byte_count)
            role = "silenced"
        else:
            payload = bytes(baseline[record.pcm_offset:
                                     record.pcm_offset + record.pcm_byte_count])
            role = "baseline-bmo"
        output[record.pcm_offset:record.pcm_offset + record.pcm_byte_count] = payload
        manifest_slots.append({
            "sound_id": sound_id,
            "role": role,
            "text_fragment": seg.text_fragment if seg else "",
            "content_seconds": seg.content_seconds if seg else 0.0,
            "slot_seconds": record.duration_seconds,
            "padding_seconds": seg.padding_seconds if seg else record.duration_seconds,
            "content_pcm_bytes": len(seg.pcm) if seg else 0,
            "slot_pcm_bytes": record.pcm_byte_count,
            "written_pcm_sha256": sha256(payload),
        })
    built = bytes(output)
    manifest = {
        "layout": "pcm-only-preserve-directory-headers-padding",
        "unused_slots": unused_slots,
        "file_size": len(built),
        "sha256": sha256(built),
        "transport_additive_checksum_hex": additive_checksum_hex(built),
        "slot_order": [seg.sound_id for seg in segments],
        "slots": manifest_slots,
    }
    return built, manifest


def validate_tts_bank(baseline: bytes, built: bytes,
                      segments: list[SlotSegment],
                      unused_slots: str) -> dict:
    """Prove the built image is a safe PCM-only overlay of the baseline."""
    checks: list[dict] = []

    def check(name: str, ok: bool, detail: str = "") -> bool:
        checks.append({"check": name, "ok": bool(ok), "detail": detail})
        return ok

    check("exact_file_size", len(built) == EXPECTED_BANK_BYTES,
          f"{len(built)} bytes")
    check("size_matches_baseline", len(built) == len(baseline))
    base_records = record_ranges_from_bytes(baseline)
    built_records = record_ranges_from_bytes(built)
    check("directory_and_headers_unchanged",
          built[:AUDIO_START_PAGE * PAGE_SIZE] == baseline[:AUDIO_START_PAGE * PAGE_SIZE]
          and all(built[r.record_offset:r.pcm_offset]
                  == baseline[r.record_offset:r.pcm_offset]
                  for r in base_records))
    check("record_table_identical", base_records == built_records)

    pcm_fields = [(r.pcm_offset, r.pcm_offset + r.pcm_byte_count)
                  for r in base_records]
    outside_ok = True
    cursor = 0
    for start, end in sorted(pcm_fields):
        if built[cursor:start] != baseline[cursor:start]:
            outside_ok = False
            break
        cursor = end
    if built[cursor:] != baseline[cursor:]:
        outside_ok = False
    check("zero_changes_outside_pcm_fields", outside_ok)

    by_id = {seg.sound_id: seg for seg in segments}
    records = {r.sound_id: r for r in base_records}
    for seg in segments:
        record = records.get(seg.sound_id)
        if record is None:
            check(f"slot_{seg.sound_id}_exists", False)
            continue
        check(f"slot_{seg.sound_id}_fits",
              len(seg.pcm) <= record.pcm_byte_count,
              f"{len(seg.pcm)} <= {record.pcm_byte_count}")
        expected = seg.pcm + bytes(record.pcm_byte_count - len(seg.pcm))
        check(f"slot_{seg.sound_id}_preview_matches_bank",
              built[record.pcm_offset:record.pcm_offset + record.pcm_byte_count]
              == expected)
        samples = array("h")
        samples.frombytes(seg.pcm)
        peak = max((abs(s) for s in samples), default=0)
        check(f"slot_{seg.sound_id}_no_clipping", peak < 32767, f"peak {peak}")
    for sound_id, record in records.items():
        if sound_id in by_id:
            continue
        field_ = built[record.pcm_offset:record.pcm_offset + record.pcm_byte_count]
        if unused_slots == "silence":
            check(f"slot_{sound_id}_unused_silenced", not any(field_))
        else:
            check(f"slot_{sound_id}_unused_baseline",
                  field_ == baseline[record.pcm_offset:
                                     record.pcm_offset + record.pcm_byte_count])

    report = {
        "ok": all(entry["ok"] for entry in checks),
        "checks": checks,
        "sha256": sha256(built),
        "transport_additive_checksum_hex": additive_checksum_hex(built),
        "file_size": len(built),
    }
    return report


def stitch_segments(segments: list[SlotSegment]) -> bytes:
    return b"".join(seg.pcm for seg in segments)


def required_confirmation(bank_sha256: str) -> str:
    return f"BURN TTS {bank_sha256}"


# ---------------------------------------------------------------------------
# Long-text chunking: pack sentences into ~17 s banks, never mid-word
# ---------------------------------------------------------------------------

_SENTENCE_END = re.compile(r"(?<=[.!?;:])\s+")


def split_text_units(text: str) -> list[str]:
    """Sentence-ish units for chunk packing; falls back to the whole text."""
    units = [u.strip() for u in _SENTENCE_END.split(text.strip()) if u.strip()]
    return units or [text.strip()]


def plan_speech_chunks(text: str, synthesize, capacities: list[tuple[int, int]],
                       max_chunks: int = 12) -> list[dict]:
    """Split ``text`` into bank-sized chunks at sentence/word boundaries.

    ``synthesize(chunk_text) -> array('h')`` returns prepared (trimmed,
    normalized) PCM.  Each returned chunk carries its planned slot segments,
    ready to build one temporary bank.  Greedy: grow a chunk sentence by
    sentence while its synthesized audio still fits; a single over-long
    sentence is split at word boundaries.  Raises instead of ever truncating.
    """
    total_capacity = sum(cap for _, cap in capacities)
    units = split_text_units(text)
    chunks: list[dict] = []
    i = 0
    while i < len(units):
        if len(chunks) >= max_chunks:
            raise TtsBankError(
                f"text needs more than {max_chunks} bank writes; shorten it")
        fitted = None
        j = i + 1
        while j <= len(units):
            candidate = " ".join(units[i:j])
            samples = synthesize(candidate)
            if len(samples) > total_capacity:
                break
            # The real fit test is slot planning itself: boundary-aware cuts
            # spend capacity, so a chunk that fits by total length can still
            # fail to split cleanly across the ten slots.
            try:
                segments = plan_segments(samples, capacities)
            except TtsBankError:
                break
            fitted = (j, candidate, samples, segments)
            j += 1
        if fitted is None:
            words = units[i].split()
            if len(words) < 2:
                raise TtsBankError(
                    f"a single word renders longer than one bank: {units[i]!r}")
            half = len(words) // 2
            units[i:i + 1] = [" ".join(words[:half]), " ".join(words[half:])]
            continue
        end, chunk_text, samples, segments = fitted
        attach_text_fragments(segments, chunk_text)
        chunks.append({"text": chunk_text, "samples": samples,
                       "segments": segments})
        i = end
    return chunks


# ---------------------------------------------------------------------------
# Robot-facing controller (fully injectable; no hardware in unit tests)
# ---------------------------------------------------------------------------

@dataclass
class PlaybackProgress:
    total_segments: int = 0
    segment_index: int | None = None
    current_slot: int | None = None
    elapsed_seconds: float = 0.0
    remaining_segments: int = 0
    chunk_index: int | None = None
    total_chunks: int = 0
    current_text: str = ""
    stopped: bool = False
    error: str | None = None
    state: str = "idle"

    def snapshot(self) -> dict:
        return {
            "state": self.state,
            "total_segments": self.total_segments,
            "segment_index": self.segment_index,
            "current_slot": self.current_slot,
            "elapsed_seconds": round(self.elapsed_seconds, 3),
            "remaining_segments": self.remaining_segments,
            "chunk_index": self.chunk_index,
            "total_chunks": self.total_chunks,
            "current_text": self.current_text,
            "stopped": self.stopped,
            "error": self.error,
        }


class BankBurner:
    """Burn/verify/play/restore against an injected robot object.

    ``robot`` must expose ``cmd(text, timeout=...) -> str`` and
    ``t.send_binary(command, payload, timeout=...) -> bytes`` (the existing
    ``neatobmo.Robot`` shape).  ``send_binary`` already enforces ACK and the
    normal terminator.  The caller is responsible for holding the robot lock
    around every method here.
    """

    def __init__(self, robot, sleep=time.sleep, stabilize_seconds: float = 5.0,
                 log=None):
        self.robot = robot
        self.sleep = sleep
        self.stabilize_seconds = stabilize_seconds
        self.log = log or (lambda message: None)

    def burn_and_verify(self, payload: bytes, expected_sha256: str,
                        label: str, sweep: bool = True) -> dict:
        """Write one pre-validated image and prove the robot survived it.

        The only accepted payload is the exact bytes whose hash the caller
        validated in this operation; there is no other allowlist entry.
        With ``sweep=False`` the audible PlaySound 0-20 sweep is skipped
        (identity is still checked); paced playback then verifies each live
        slot silently via its command reply.
        """
        digest = sha256(payload)
        if len(payload) != EXPECTED_BANK_BYTES or digest != expected_sha256:
            raise BankValidationError(
                f"refusing to burn {label}: payload does not match the "
                f"validated image for this operation")
        # Pre-burn identity check: never write to a robot we can't identify.
        # The benign command also flushes any stale parser state (a confused
        # line parser once prepended garbage to the burn header).
        before = self.robot.cmd("GetVersion", timeout=5)
        missing = [needle for needle in VERSION_REQUIRED_SUBSTRINGS
                   if needle not in before]
        if missing:
            raise RobotVerificationError(
                f"pre-burn GetVersion identity check failed for {label} "
                f"(missing {missing})")
        self.log(f"burn {label}: {len(payload)} bytes sha256={digest}")
        reply = self.robot.t.send_binary("Upload sound", payload, timeout=45.0)
        self.log(f"burn {label}: receiver replied {reply.hex()}")
        self.sleep(self.stabilize_seconds)
        version = self.robot.cmd("GetVersion", timeout=5)
        missing = [needle for needle in VERSION_REQUIRED_SUBSTRINGS
                   if needle not in version]
        if missing:
            raise RobotVerificationError(
                f"GetVersion identity check failed after {label} "
                f"(missing {missing})")
        accepted = None
        if sweep:
            accepted = self.sweep_sound_ids()
            if set(accepted) != LIVE_SOUND_IDS:
                raise RobotVerificationError(
                    f"post-write slot map mismatch after {label}: {accepted}")
            self.log(f"burn {label}: identity + slot map verified {accepted}")
        else:
            self.log(f"burn {label}: identity verified (silent mode; slots "
                     "verified during playback)")
        return {"sha256": digest, "receiver_hex": reply.hex(),
                "accepted_ids": accepted}

    def sweep_sound_ids(self) -> list[int]:
        accepted = []
        for sound_id in range(21):
            result = self.robot.cmd(f"PlaySound {sound_id}", timeout=4)
            if "out of range" not in result.lower():
                accepted.append(sound_id)
        return accepted

    def play_paced(self, segments: list[SlotSegment],
                   progress: PlaybackProgress, stop_event=None) -> dict:
        """PlaySound each segment, waiting its full declared slot duration.

        The firmware does not queue commands and a new PlaySound interrupts
        the current clip, so the wait between commands is mandatory.
        """
        progress.state = "speaking"
        progress.total_segments = len(segments)
        progress.remaining_segments = len(segments)
        replies = []
        for seg in segments:
            if stop_event is not None and stop_event.is_set():
                progress.stopped = True
                break
            progress.segment_index = seg.index
            progress.current_slot = seg.sound_id
            reply = self.robot.cmd(f"PlaySound {seg.sound_id}", timeout=4)
            if "out of range" in reply.lower():
                raise RobotVerificationError(
                    f"slot {seg.sound_id} rejected after burn: the installed "
                    f"bank does not expose the expected live map")
            replies.append(reply)
            self.log(f"play segment {seg.index}: slot {seg.sound_id}, "
                     f"waiting {seg.slot_seconds}s")
            self.sleep(seg.slot_seconds)
            progress.elapsed_seconds += seg.slot_seconds
            progress.remaining_segments -= 1
        return {"replies": replies, "stopped": progress.stopped}

    def restore_bank(self, artifact_path: Path, expected_sha256: str,
                     label: str) -> dict:
        """Re-read a saved artifact, re-prove its hash, burn, and verify."""
        try:
            payload = Path(artifact_path).read_bytes()
        except OSError as exc:
            raise RestoreArtifactError(f"{label} artifact unreadable: {exc}")
        if len(payload) != EXPECTED_BANK_BYTES:
            raise RestoreArtifactError(
                f"{label} artifact is {len(payload)} bytes, "
                f"expected {EXPECTED_BANK_BYTES}")
        digest = sha256(payload)
        if digest != expected_sha256:
            raise RestoreArtifactError(
                f"{label} artifact hash {digest} does not match the recorded "
                f"{expected_sha256}; refusing to burn it")
        return self.burn_and_verify(payload, expected_sha256, label)


def run_speech_operation(burner: BankBurner, bank_bytes: bytes,
                         bank_sha256: str, segments: list[SlotSegment],
                         bmo_artifact: Path,
                         bmo_sha256: str = BMO_BANK_SHA256,
                         auto_restore: bool = True,
                         progress: PlaybackProgress | None = None,
                         stop_event=None) -> dict:
    """Burn the temporary bank, speak, and (optionally) restore BMO.

    With ``auto_restore`` the BMO restore runs even when playback fails or is
    stopped, so a temporary TTS bank is never left installed silently.
    Without it, the report flags ``temporary_bank_installed`` and the caller
    must offer an explicit restore.
    """
    progress = progress or PlaybackProgress()
    report: dict = {"bank_sha256": bank_sha256, "auto_restore": auto_restore}
    progress.state = "burning"
    report["burn"] = burner.burn_and_verify(bank_bytes, bank_sha256,
                                            "temporary TTS bank")
    playback_error = None
    try:
        report["playback"] = burner.play_paced(segments, progress, stop_event)
    except Exception as exc:
        playback_error = exc
        progress.error = str(exc)
        report["playback"] = {"error": str(exc), "stopped": progress.stopped}
    if auto_restore:
        progress.state = "restoring"
        report["restore"] = burner.restore_bank(bmo_artifact, bmo_sha256,
                                                "persistent BMO bank")
        report["temporary_bank_installed"] = False
        progress.state = "error" if playback_error else "restored"
    else:
        report["temporary_bank_installed"] = True
        progress.state = "error" if playback_error else "temporary-installed"
    if playback_error and not auto_restore:
        raise playback_error
    if playback_error:
        report["playback_error"] = str(playback_error)
    return report


def speak_chunks_operation(burner: BankBurner, baseline: bytes,
                           chunks: list[dict],
                           unused_slots: str = "silence",
                           progress: PlaybackProgress | None = None,
                           stop_event=None) -> dict:
    """Fully automatic speech: build+validate+burn+play each chunk in order.

    The last chunk's bank stays installed (persistent mode — no restore
    write).  Validation still gates every burn internally: an image that
    fails byte-exact validation is never sent.  Playback verifies each slot
    via its command reply, so no audible sweep interrupts the speech.
    """
    progress = progress or PlaybackProgress()
    progress.total_chunks = len(chunks)
    report: dict = {"chunks": [], "persisted": True}
    for index, chunk in enumerate(chunks):
        if stop_event is not None and stop_event.is_set():
            progress.stopped = True
            break
        progress.chunk_index = index
        progress.current_text = chunk["text"]
        progress.state = "building"
        built, manifest = build_tts_bank(baseline, chunk["segments"],
                                         unused_slots)
        validation = validate_tts_bank(baseline, built, chunk["segments"],
                                       unused_slots)
        if not validation["ok"]:
            failed = [c["check"] for c in validation["checks"] if not c["ok"]]
            raise BankValidationError(
                f"chunk {index} failed validation ({failed}); burn blocked")
        progress.state = "burning"
        burn = burner.burn_and_verify(built, validation["sha256"],
                                      f"speech chunk {index + 1}/{len(chunks)}",
                                      sweep=False)
        playback = burner.play_paced(chunk["segments"], progress, stop_event)
        report["chunks"].append({
            "index": index,
            "text": chunk["text"],
            "sha256": validation["sha256"],
            "segments": len(chunk["segments"]),
            "burn": burn,
            "playback": playback,
        })
    progress.state = "stopped" if progress.stopped else "spoken"
    report["stopped"] = progress.stopped
    report["installed_bank_sha256"] = (
        report["chunks"][-1]["sha256"] if report["chunks"] else None)
    return report
