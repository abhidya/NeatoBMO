"""BMO's Bitcoin lottery: a real solo miner over the Stratum protocol.

Solo ("lottery") mining means hashing real block-candidate headers against a
real solo pool's work. With toy hashrate the odds of finding a block are
astronomically small, so it is a lottery ticket, not an income stream — and
that is the honest framing this module gives BMO.

The module is layered so the correctness-critical parts stay pure and unit
testable (against the genesis block), while the service part is thin:

  * block-math helpers — double-SHA256, merkle root, header assembly, and
    target handling. Pure functions, verified against genesis constants.
  * MinerSettings — the config (address, pool, threads, enabled) with JSON
    persistence under ~/.neatobmo/miner.json.
  * LotteryMiner — the service. A Stratum protocol thread owns the socket and
    fans each job out to a pool of hashing *worker processes* (multiprocessing,
    so Python's GIL does not cap the hashrate). start()/stop()/configure()/
    status() are the whole public surface.

    from neatobmo.miner import LotteryMiner, MinerSettings

    miner = LotteryMiner(MinerSettings(address="bc1q..."))
    miner.start()            # no-op until an address is set (state: no_address)
    miner.status()           # JSON-ready stats for the console
"""
from __future__ import annotations

import hashlib
import json
import multiprocessing
import os
import queue
import re
import socket
import ssl
import threading
import time
from dataclasses import dataclass, asdict, fields
from pathlib import Path

# --------------------------------------------------------------------------
# Block-math helpers (pure)
# --------------------------------------------------------------------------

# Difficulty-1 target: 0x00000000FFFF0000... (mantissa 0xffff at exponent 29).
DIFF1 = 0xFFFF << 208
# A batch of nonces hashed per worker loop before touching the shared counter.
BATCH = 4096
# How often a worker reports its closest-so-far hash, in seconds.
_BEST_REPORT_INTERVAL = 2.0


def dsha256(data: bytes) -> bytes:
    """Bitcoin's double-SHA256."""
    return hashlib.sha256(hashlib.sha256(data).digest()).digest()


def build_merkle_root(coinbase_hex: str, branches) -> str:
    """Merkle root for the header, from the coinbase and Stratum's branches.

    Stratum hands every value (coinb1/coinb2/extranonce/branches) in the
    little-endian "header byte order". The computation therefore concatenates
    raw double-SHA256 digests and returns the raw digest hex — no reversal —
    which is exactly the byte order the 80-byte header expects.
    """
    current = dsha256(bytes.fromhex(coinbase_hex))
    for branch in branches:
        current = dsha256(bytes.fromhex(branch) + current)
    return current.hex()


def target_from_nbits(nbits_hex: str) -> int:
    """Decode a compact target (nBits) into its 256-bit integer."""
    compact = int.from_bytes(bytes.fromhex(nbits_hex), "little")
    exponent = compact >> 24
    mantissa = compact & 0xFFFFFF
    if exponent <= 3:
        return mantissa >> (8 * (3 - exponent))
    return mantissa << (8 * (exponent - 3))


def share_target(difficulty) -> int:
    """The hash ceiling that counts as a share at ``difficulty``."""
    difficulty = max(1, int(difficulty))
    return max(1, DIFF1 // difficulty)


def hash_int(digest: bytes) -> int:
    """A 256-bit hash digest as a plain integer (big-endian, like targets)."""
    return int.from_bytes(digest, "big")


def difficulty_of(digest: bytes) -> float:
    """Share difficulty a hash represents (higher = closer to a block)."""
    value = hash_int(digest)
    return float("inf") if value == 0 else DIFF1 / value


def build_header(version_hex, prevhash_hex, merkle_root_hex, ntime, nbits_hex,
                 nonce) -> bytes:
    """The 80-byte block header: 4+32+32+4+4+4 bytes, all little-endian."""
    return (bytes.fromhex(version_hex) +
            bytes.fromhex(prevhash_hex) +
            bytes.fromhex(merkle_root_hex) +
            int(ntime).to_bytes(4, "little") +
            bytes.fromhex(nbits_hex) +
            int(nonce).to_bytes(4, "little"))


def format_hashrate(hashes_per_second: float) -> str:
    """Human hashrate label: '412.3 KH/s'."""
    h = max(0.0, hashes_per_second)
    for scale, unit in ((1e12, "TH/s"), (1e9, "GH/s"), (1e6, "MH/s"),
                        (1e3, "KH/s")):
        if h >= scale:
            return f"{h / scale:.2f} {unit}"
    return f"{h:.0f} H/s"


_B58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def _b58decode(text: str) -> bytes:
    value = 0
    for char in text:
        value = value * 58 + _B58_ALPHABET.index(char)
    raw = value.to_bytes((value.bit_length() + 7) // 8, "big") if value else b""
    pad = 0
    for char in text:
        if char == "1":
            pad += 1
        else:
            break
    return b"\x00" * pad + raw


def validate_address(address) -> tuple[bool, str]:
    """Lightweight Bitcoin address validation: (ok, message)."""
    address = (address or "").strip()
    if not address:
        return False, "empty address"
    if address[:3] in ("bc1", "tb1"):
        if re.fullmatch(r"(bc1|tb1)[02-9ac-hj-np-z]{11,71}", address):
            return True, ""
        return False, "invalid bech32 address"
    if address[0] not in "123":
        return False, "unsupported address prefix (use 1, 3, or bc1)"
    if not re.fullmatch(r"[1-9A-HJ-NP-Za-km-z]{26,35}", address):
        return False, "invalid base58 characters or length"
    try:
        decoded = _b58decode(address)
    except ValueError:
        return False, "invalid base58 address"
    if len(decoded) != 25:
        return False, "invalid base58 address"
    if decoded[-4:] != dsha256(decoded[:-4])[:4]:
        return False, "bad base58 checksum"
    return True, ""


# --------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------

@dataclass
class MinerSettings:
    address: str = ""
    pool_host: str = "public-pool.io"
    pool_port: int = 3333
    tls: bool = False
    threads: int = 2
    enabled: bool = True

    @classmethod
    def from_config(cls, cfg):
        """Build from the Config dataclass's NEATOBMO_MINER_* knobs."""
        host, _, port = cfg.miner_pool.partition(":")
        return cls(address=cfg.miner_address, pool_host=host,
                   pool_port=int(port or 3333), tls=cfg.miner_tls,
                   threads=cfg.miner_threads, enabled=cfg.miner_enabled)

    def pool_label(self) -> str:
        return f"{self.pool_host}:{self.pool_port}"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict):
        data = {k: v for k, v in (data or {}).items()
                if k in {f.name for f in fields(cls)}}
        settings = cls(**data)
        settings.pool_port = int(settings.pool_port)
        settings.threads = int(settings.threads)
        settings.enabled = bool(settings.enabled)
        settings.tls = bool(settings.tls)
        return settings

    @classmethod
    def load(cls, path: Path):
        try:
            data = json.loads(Path(path).read_text())
        except (OSError, ValueError):
            return None
        return cls.from_dict(data)

    def save(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2))

    def validate(self) -> list[str]:
        errors = []
        if not self.pool_host.strip():
            errors.append("pool host is empty")
        if not (1 <= self.pool_port <= 65535):
            errors.append("pool port out of range")
        if self.threads < 1:
            errors.append("threads must be at least 1")
        # An address that cannot be paid out is a config error, not just a
        # start-time one: without this, configure() happily persists a typo'd
        # address and the miner reloads it broken on the next boot. Empty
        # stays legal — that is the unconfigured state, not a mistake.
        if self.address.strip():
            ok, message = validate_address(self.address)
            if not ok:
                errors.append(f"bad address: {message}")
        return errors


# --------------------------------------------------------------------------
# Stratum transport
# --------------------------------------------------------------------------

class StratumClient:
    """Line-delimited JSON-RPC over TCP (or TLS) to a Stratum server."""

    def __init__(self, host, port, tls=False, timeout=0.5):
        self.host = host
        self.port = port
        self.tls = tls
        self.timeout = timeout
        self._sock = None
        self._file = None

    def connect(self):
        raw = socket.create_connection((self.host, self.port), timeout=10)
        if self.tls:
            context = ssl.create_default_context()
            raw = context.wrap_socket(raw, server_hostname=self.host)
        raw.settimeout(self.timeout)
        self._sock = raw
        self._file = raw.makefile("rb")

    def close(self):
        try:
            if self._file is not None:
                self._file.close()
        except OSError:
            pass
        try:
            if self._sock is not None:
                self._sock.close()
        except OSError:
            pass
        self._sock = None
        self._file = None

    def send(self, obj):
        self._sock.sendall((json.dumps(obj) + "\n").encode())

    def read_message(self, timeout=None):
        """One JSON message, or None on timeout / clean EOF."""
        if timeout is not None:
            self._sock.settimeout(timeout)
        try:
            line = self._file.readline()
        except socket.timeout:
            return None
        finally:
            if timeout is not None:
                self._sock.settimeout(self.timeout)
        if not line:
            return None
        try:
            return json.loads(line.decode())
        except ValueError:
            return None

    def subscribe_authorize(self, address, worker_password="x"):
        """subscribe + authorize; returns (extranonce1, extranonce2_size)."""
        self.send({"id": 1, "method": "mining.subscribe",
                   "params": ["BMO/1.0"]})
        extranonce1, extranonce2_size = "", 4
        while True:
            message = self.read_message(timeout=10)
            if message is None:
                raise ConnectionError("no mining.subscribe response")
            if message.get("id") == 1:
                result = message.get("result") or [[], "", 4]
                extranonce1 = result[1]
                extranonce2_size = result[2]
                break
        self.send({"id": 2, "method": "mining.authorize",
                   "params": [address, worker_password]})
        while True:
            message = self.read_message(timeout=10)
            if message is None:
                raise ConnectionError("no mining.authorize response")
            if message.get("id") == 2:
                break
        return extranonce1, extranonce2_size


# --------------------------------------------------------------------------
# Hashing worker process
# --------------------------------------------------------------------------

def _job_prefix(job: dict, extranonce2: str, ntime=None) -> bytes:
    """The first 76 bytes of the header for one job/worker (merkle included)."""
    coinbase = (job["coinb1"] + job["extranonce1"] + extranonce2 +
                job["coinb2"])
    merkle = build_merkle_root(coinbase, job["merkle_branches"])
    return (bytes.fromhex(job["version"]) +
            bytes.fromhex(job["prevhash"]) +
            bytes.fromhex(merkle) +
            int(job["ntime"] if ntime is None else ntime).to_bytes(4, "little") +
            bytes.fromhex(job["nbits"]))


def _hash_worker(job_queue, result_queue, hashes, worker_id):
    """Mine nonces for Stratum jobs; report shares and closest-so-far hashes.

    Runs in its own process (spawn). ``job_queue``/``result_queue`` are
    multiprocessing queues; ``hashes`` is a shared Value('Q') counter.
    A None job is the stop sentinel.
    """
    extranonce2 = f"{worker_id & 0xFFFFFFFF:08x}"
    job = None
    prefix = None
    base = None
    nonce = int.from_bytes(os.urandom(4), "big")
    best = None
    best_at = time.time()

    while True:
        got_job = False
        try:
            incoming = job_queue.get(timeout=0.2)
            got_job = True
        except queue.Empty:
            pass
        if got_job:
            if incoming is None:
                return
            job = incoming
            prefix = _job_prefix(job, extranonce2)
            base = hashlib.sha256(prefix)
            nonce = int.from_bytes(os.urandom(4), "big")
            best = None
            best_at = time.time()
        if job is None:
            continue

        share_target_ = job["share_target"]
        network_target = job["network_target"]
        ntime = job["ntime"]

        for _ in range(BATCH):
            digest = _finish(base, nonce)
            nonce = (nonce + 1) & 0xFFFFFFFF
            value = hash_int(digest)
            if value <= share_target_:
                result_queue.put({
                    "type": "share",
                    "job_id": job["job_id"],
                    "extranonce2": extranonce2,
                    "ntime": ntime.to_bytes(4, "little").hex(),
                    "nonce": ((nonce - 1) & 0xFFFFFFFF).to_bytes(4, "little").hex(),
                    "difficulty": difficulty_of(digest),
                    "block": value <= network_target,
                })
            if best is None or value < best:
                best = value
            if nonce == 0:
                ntime += 1
                prefix = _job_prefix(job, extranonce2, ntime=ntime)
                base = hashlib.sha256(prefix)

        hashes.value += BATCH
        if best is not None and time.time() - best_at >= _BEST_REPORT_INTERVAL:
            result_queue.put({"type": "best", "difficulty": DIFF1 / best})
            best = None
            best_at = time.time()


def _finish(base, nonce):
    """SHA256d of the 76-byte prefix + a 4-byte nonce (the fast path)."""
    hasher = base.copy()
    hasher.update(nonce.to_bytes(4, "little"))
    return hashlib.sha256(hasher.digest()).digest()


# --------------------------------------------------------------------------
# The service
# --------------------------------------------------------------------------

class LotteryMiner:
    """Owns the Stratum protocol thread and the hashing worker processes."""

    def __init__(self, settings: MinerSettings, settings_path=None):
        self.settings = settings
        self.settings_path = (Path(settings_path)
                              if settings_path is not None
                              else Path.home() / ".neatobmo" / "miner.json")
        # persisted UI config wins over env defaults
        persisted = MinerSettings.load(self.settings_path)
        if persisted is not None:
            self.settings = persisted

        # Reentrant: start()/stop()/configure() hold the lock and then build a
        # response via status(), which locks again. A plain Lock deadlocks the
        # moment any of them returns self.status() from inside its critical
        # section — which every early-return path does.
        self._lock = threading.RLock()
        self._state = "no_address" if not self.settings.address else "idle"
        self._error = None
        self._running = False
        self._started_at = None
        self._stop_event = None
        self._hashes = None
        self._job_queue = None
        self._result_queue = None
        self._workers = []
        self._protocol_thread = None
        self._current_job = None
        self._extranonce1 = ""
        self._extranonce2_size = 4
        self._share_difficulty = 1
        self._submit_id = 0
        self._shares_submitted = 0
        self._shares_accepted = 0
        self._shares_rejected = 0
        self._best_share_difficulty = 0.0
        self._best_seen_difficulty = 0.0
        self._block_found = False
        self._reconnects = 0
        self._hashrate_hs = 0.0
        self._hr_last_ts = None
        self._hr_last_hashes = 0

    # -- public surface ----------------------------------------------------

    def start(self):
        with self._lock:
            if self._running:
                return self.status()
            if not self.settings.enabled:
                self._state = "disabled"
                return self.status()
            if not self.settings.address.strip():
                self._state = "no_address"
                return self.status()
            ok, message = validate_address(self.settings.address)
            if not ok:
                self._state = "error"
                self._error = f"bad address: {message}"
                return self.status()
            for error in self.settings.validate():
                self._state = "error"
                self._error = error
                return self.status()

            context = multiprocessing.get_context("spawn")
            self._stop_event = threading.Event()
            self._hashes = context.Value("Q", 0)
            self._job_queue = context.Queue()
            self._result_queue = context.Queue()
            self._workers = []
            worker_count = min(max(1, self.settings.threads),
                               os.cpu_count() or 1)
            for index in range(worker_count):
                process = context.Process(
                    target=_hash_worker,
                    args=(self._job_queue, self._result_queue, self._hashes,
                          index),
                    daemon=True)
                process.start()
                self._workers.append(process)

            self._error = None
            self._running = True
            self._started_at = time.time()
            self._state = "starting"
            self._protocol_thread = threading.Thread(target=self._run,
                                                     daemon=True)
            self._protocol_thread.start()
            return self.status()

    def stop(self):
        with self._lock:
            was_running = self._running
            self._running = False
            if self._stop_event is not None:
                self._stop_event.set()
            workers = list(self._workers)
            job_queue = self._job_queue
        if was_running:
            for _ in workers:
                try:
                    job_queue.put(None)
                except Exception:
                    pass
            for process in workers:
                process.join(timeout=5)
                if process.is_alive():
                    process.terminate()
            if self._protocol_thread is not None:
                self._protocol_thread.join(timeout=2)
        with self._lock:
            for process in workers:
                if process.is_alive():
                    process.terminate()
            self._workers = []
            self._close_queues()
            self._state = "stopped"
            return self.status()

    def configure(self, updates: dict):
        """Apply + persist config updates, restarting if the connection changed."""
        allowed = {"address", "pool_host", "pool_port", "tls", "threads",
                   "enabled"}
        data = self.settings.to_dict()
        for key in allowed:
            if key in updates:
                data[key] = updates[key]
        if "pool" in updates:  # convenience: "host:port" in one field
            host, _, port = str(updates["pool"]).partition(":")
            data["pool_host"] = host
            data["pool_port"] = int(port or 3333)

        with self._lock:
            before = self.settings
            self.settings = MinerSettings.from_dict(data)
            for error in self.settings.validate():
                self.settings = before
                self._error = error
                self._state = "error"
                return self.status()
            try:
                self.settings.save(self.settings_path)
            except OSError as exc:
                self._error = f"could not save config: {exc}"
            connection_changed = (before.address != self.settings.address or
                                  before.pool_host != self.settings.pool_host or
                                  before.pool_port != self.settings.pool_port or
                                  before.tls != self.settings.tls)
            threads_changed = before.threads != self.settings.threads
            restart = connection_changed or threads_changed

        if self._running:
            if not self.settings.enabled:
                self.stop()
            elif restart:
                self.stop()
                self.start()
        elif self.settings.enabled:
            self.start()
        else:
            with self._lock:
                self._state = "disabled"
        return self.status()

    def autostart(self):
        """Start on boot when enabled (and an address is set)."""
        if self.settings.enabled:
            self.start()
        else:
            with self._lock:
                self._state = "disabled"

    def status(self) -> dict:
        with self._lock:
            total = self._hashes.value if self._hashes is not None else 0
            now = time.time()
            if self._hr_last_ts is not None:
                delta = now - self._hr_last_ts
                if delta > 0:
                    self._hashrate_hs = max(
                        0.0, (total - self._hr_last_hashes) / delta)
            self._hr_last_ts, self._hr_last_hashes = now, total
            uptime = (now - self._started_at) if self._started_at else 0.0
            return {
                "state": self._state,
                "enabled": self.settings.enabled,
                "address": self.settings.address,
                "pool": self.settings.pool_label(),
                "tls": self.settings.tls,
                "threads": self.settings.threads,
                "workers": len(self._workers),
                "uptime_s": round(uptime, 1),
                "hashrate_hs": round(self._hashrate_hs, 2),
                "hashrate_label": format_hashrate(self._hashrate_hs),
                "total_hashes": total,
                "shares_submitted": self._shares_submitted,
                "shares_accepted": self._shares_accepted,
                "shares_rejected": self._shares_rejected,
                "best_share_difficulty": round(self._best_share_difficulty, 2),
                "best_seen_difficulty": round(self._best_seen_difficulty, 2),
                "block_found": self._block_found,
                "reconnects": self._reconnects,
                "last_job": (self._current_job and {
                    "version": self._current_job["version"],
                    "prevhash": self._current_job["prevhash"][:16] + "…",
                    "nbits": self._current_job["nbits"],
                    "ntime": self._current_job["ntime"],
                }) or None,
                "error": self._error,
            }

    # -- internals ---------------------------------------------------------

    def _close_queues(self):
        for name in ("_job_queue", "_result_queue"):
            q = getattr(self, name)
            if q is not None:
                try:
                    q.close()
                except Exception:
                    pass
                setattr(self, name, None)
        self._hashes = None

    def _run(self):
        """The Stratum protocol loop: connect, feed jobs, submit shares."""
        while not self._stop_event.is_set():
            client = StratumClient(self.settings.pool_host,
                                   self.settings.pool_port,
                                   tls=self.settings.tls)
            try:
                with self._lock:
                    self._state = "connecting"
                    self._error = None
                client.connect()
                extranonce1, extranonce2_size = client.subscribe_authorize(
                    self.settings.address)
                with self._lock:
                    self._extranonce1 = extranonce1
                    self._extranonce2_size = extranonce2_size
                while not self._stop_event.is_set():
                    self._drain_results(client)
                    message = client.read_message(timeout=0.5)
                    if message is None:
                        continue
                    method = message.get("method")
                    if method == "mining.notify":
                        self._on_notify(message.get("params") or [])
                    elif method == "mining.set_difficulty":
                        self._on_difficulty(message.get("params") or [])
                    elif method == "mining.set_extranonce":
                        self._on_extranonce(message.get("params") or [])
                    elif method is None and "id" in message:
                        self._on_submit_response(message)
            except Exception as exc:
                with self._lock:
                    self._state = "error"
                    self._error = str(exc)
                    self._reconnects += 1
            finally:
                client.close()
            self._stop_event.wait(min(30.0, 2 ** min(self._reconnects, 5)))

    def _on_notify(self, params):
        if len(params) < 9:
            return
        (job_id, prevhash, coinb1, coinb2, branches, version, nbits, ntime,
         clean) = params[:9]
        with self._lock:
            self._extranonce1 = getattr(self, "_extranonce1", "") or ""
            job = {
                "job_id": job_id,
                "version": version,
                "prevhash": prevhash,
                "coinb1": coinb1,
                "coinb2": coinb2,
                "merkle_branches": branches,
                "nbits": nbits,
                "ntime": int.from_bytes(bytes.fromhex(ntime), "little"),
                "extranonce1": self._extranonce1,
                "extranonce2_size": self._extranonce2_size,
                "network_target": target_from_nbits(nbits),
                "share_target": share_target(self._share_difficulty),
                "clean": bool(clean),
            }
            self._current_job = job
            self._state = "mining"
        self._job_queue.put(job)

    def _on_difficulty(self, params):
        if not params:
            return
        with self._lock:
            self._share_difficulty = max(1, int(params[0]))
            if self._current_job is not None:
                self._current_job["share_target"] = share_target(
                    self._share_difficulty)
                self._job_queue.put(self._current_job)

    def _on_extranonce(self, params):
        if len(params) < 2:
            return
        with self._lock:
            self._extranonce1 = params[0]
            self._extranonce2_size = int(params[1])
            if self._current_job is not None:
                self._current_job["extranonce1"] = self._extranonce1
                self._current_job["extranonce2_size"] = self._extranonce2_size
                self._job_queue.put(self._current_job)

    def _drain_results(self, client):
        while True:
            try:
                item = self._result_queue.get_nowait()
            except queue.Empty:
                return
            if item.get("type") == "share":
                self._submit_share(client, item)
            elif item.get("type") == "best":
                with self._lock:
                    self._best_seen_difficulty = max(
                        self._best_seen_difficulty, item.get("difficulty", 0.0))

    def _submit_share(self, client, item):
        with self._lock:
            self._submit_id += 1
            submit_id = self._submit_id
            self._shares_submitted += 1
            self._best_share_difficulty = max(
                self._best_share_difficulty, item.get("difficulty", 0.0))
            if item.get("block"):
                self._block_found = True
        try:
            client.send({
                "id": submit_id, "method": "mining.submit",
                "params": [self.settings.address, item["job_id"],
                           item["extranonce2"], item["ntime"], item["nonce"]],
            })
        except OSError as exc:
            with self._lock:
                self._error = f"submit failed: {exc}"

    def _on_submit_response(self, message):
        error = message.get("error")
        result = message.get("result")
        with self._lock:
            if error or result is False:
                self._shares_rejected += 1
            else:
                self._shares_accepted += 1
