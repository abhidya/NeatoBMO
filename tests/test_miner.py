"""Bitcoin lottery miner: block math (vs genesis), settings, and the worker."""
import os
import queue
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from neatobmo.miner import (
    DIFF1, LotteryMiner, MinerSettings, build_header, build_merkle_root,
    dsha256, format_hashrate, share_target, target_from_nbits, validate_address,
    _hash_worker,
)

GENESIS_HEADER = (
    "01000000"
    "0000000000000000000000000000000000000000000000000000000000000000"
    "3ba3edfd7a7b12b27ac72c3e67768f617fc81bc3888a51323a9fb8aa4b1e5e4a"
    "29ab5f49"
    "ffff001d"
    "1dac2b7c"
)
GENESIS_HASH = "000000000019d6689c085ae165831e934ff763ae46a2a6c172b3f1b60a8ce26f"
GENESIS_MERKLE = "3ba3edfd7a7b12b27ac72c3e67768f617fc81bc3888a51323a9fb8aa4b1e5e4a"
GENESIS_COINBASE = (
    "01000000010000000000000000000000000000000000000000000000000000000000000000"
    "ffffffff4d04ffff001d0104455468652054696d65732030332f4a616e2f323030392043"
    "68616e63656c6c6f72206f6e206272696e6b206f66207365636f6e64206261696c6f7574"
    "20666f722062616e6b73ffffffff0100f2052a01000000434104678afdb0fe5548271967"
    "f1a67130b7105cd6a828e03909a67962e0ea1f61deb649f6bc3f4cef38c4f35504e51ec1"
    "12de5c384df7ba0b8d578a4c702b6bf11d5fac00000000"
)


class BlockMathTests(unittest.TestCase):
    def test_double_sha256_genesis(self):
        self.assertEqual(dsha256(bytes.fromhex(GENESIS_HEADER))[::-1].hex(),
                         GENESIS_HASH)

    def test_merkle_root_matches_genesis_coinbase(self):
        self.assertEqual(build_merkle_root(GENESIS_COINBASE, []), GENESIS_MERKLE)

    def test_target_from_nbits_decodes_difficulty_one(self):
        self.assertEqual(target_from_nbits("ffff001d"), DIFF1)

    def test_build_header_reconstructs_genesis(self):
        header = build_header("01000000", "00" * 32, GENESIS_MERKLE,
                              0x495FAB29, "ffff001d", 0x7C2BAC1D)
        self.assertEqual(header.hex(), GENESIS_HEADER)
        self.assertEqual(dsha256(header)[::-1].hex(), GENESIS_HASH)

    def test_share_target_scales_with_difficulty(self):
        self.assertEqual(share_target(1), DIFF1)
        self.assertEqual(share_target(4), DIFF1 // 4)

    def test_format_hashrate(self):
        self.assertEqual(format_hashrate(0), "0 H/s")
        self.assertEqual(format_hashrate(950), "950 H/s")
        self.assertEqual(format_hashrate(1234), "1.23 KH/s")
        self.assertEqual(format_hashrate(2.3e6), "2.30 MH/s")


class AddressValidationTests(unittest.TestCase):
    def test_valid_base58(self):
        ok, message = validate_address("1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa")
        self.assertTrue(ok, message)

    def test_valid_bech32(self):
        ok, message = validate_address(
            "bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4")
        self.assertTrue(ok, message)

    def test_rejects_bad_checksum(self):
        ok, _ = validate_address("1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNb")
        self.assertFalse(ok)

    def test_rejects_empty_and_unknown_prefix(self):
        self.assertFalse(validate_address("")[0])
        self.assertFalse(validate_address("xinvalidaddress")[0])


class _Counter:
    def __init__(self):
        self.value = 0


class WorkerTests(unittest.TestCase):
    def _job(self, share_target_value, network_target=0):
        return {
            "job_id": "job-1",
            "version": "20000000",
            "prevhash": "00" * 32,
            "coinb1": "01000000",
            "coinb2": "00000000",
            "merkle_branches": [],
            "nbits": "ffff001d",
            "ntime": 0x495FAB29,
            "extranonce1": "00",
            "extranonce2_size": 4,
            "network_target": network_target,
            "share_target": share_target_value,
        }

    def test_worker_hashes_and_finds_share(self):
        job_queue, result_queue = queue.Queue(), queue.Queue()
        hashes = _Counter()
        job_queue.put(self._job((1 << 256) - 1))   # every hash is a share
        thread = threading.Thread(
            target=_hash_worker, args=(job_queue, result_queue, hashes, 0),
            daemon=True)
        thread.start()
        try:
            share = result_queue.get(timeout=10)
        finally:
            job_queue.put(None)
            thread.join(timeout=5)
        self.assertEqual(share["type"], "share")
        self.assertEqual(share["job_id"], "job-1")
        self.assertGreater(share["difficulty"], 0)
        self.assertFalse(share["block"])
        self.assertGreater(hashes.value, 0)

    def test_worker_stops_on_sentinel(self):
        job_queue, result_queue = queue.Queue(), queue.Queue()
        thread = threading.Thread(
            target=_hash_worker,
            args=(job_queue, result_queue, _Counter(), 1), daemon=True)
        thread.start()
        job_queue.put(None)
        thread.join(timeout=5)
        self.assertFalse(thread.is_alive())


class ServiceTests(unittest.TestCase):
    def test_unconfigured_miner_reports_no_address(self):
        # An explicit settings_path keeps the test off the developer's real
        # ~/.neatobmo/miner.json, which __init__ would otherwise load and
        # which would silently supply an address this test says it lacks.
        with tempfile.TemporaryDirectory() as tmp:
            miner = LotteryMiner(MinerSettings(address=""),
                                 settings_path=Path(tmp) / "miner.json")
            self.assertEqual(miner.status()["state"], "no_address")
            miner.start()
            self.assertEqual(miner.status()["state"], "no_address")
            miner.stop()

    def test_invalid_address_blocks_start(self):
        with tempfile.TemporaryDirectory() as tmp:
            miner = LotteryMiner(MinerSettings(address="not-an-address"),
                                 settings_path=Path(tmp) / "miner.json")
            miner.start()
            self.assertEqual(miner.status()["state"], "error")
            miner.stop()

    def test_configure_disabled_persists_without_starting(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "miner.json"
            miner = LotteryMiner(MinerSettings(address=""),
                                 settings_path=path)
            status = miner.configure({
                "address": "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa",
                "enabled": False,
            })
            self.assertEqual(status["state"], "disabled")
            self.assertTrue(path.exists())
            loaded = MinerSettings.load(path)
            self.assertEqual(loaded.address, "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa")
            self.assertFalse(loaded.enabled)

    def test_configure_rejects_bad_address_without_persisting(self):
        """A typo'd address must not reach disk, or it reloads broken on boot."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "miner.json"
            miner = LotteryMiner(MinerSettings(address=""),
                                 settings_path=path)
            status = miner.configure({"address": "not-an-address",
                                      "enabled": True})
            self.assertEqual(status["state"], "error")
            self.assertIn("address", (status["error"] or ""))
            self.assertEqual(status["address"], "")
            self.assertFalse(path.exists())

    def test_settings_roundtrip(self):
        settings = MinerSettings(address="bc1q", pool_host="solo.ckpool.org",
                                 pool_port=3333, tls=True, threads=4,
                                 enabled=False)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "miner.json"
            settings.save(path)
            loaded = MinerSettings.load(path)
            self.assertEqual(loaded.to_dict(), settings.to_dict())

    def test_settings_validation(self):
        self.assertFalse(MinerSettings(pool_port=3333, threads=2).validate())
        self.assertTrue(MinerSettings(pool_port=70000).validate())
        self.assertTrue(MinerSettings(threads=0).validate())


if __name__ == "__main__":
    unittest.main()


class MinerRoutineTests(unittest.TestCase):
    """BMO's spoken status must distinguish 'no wallet' from 'switched off'."""

    def _reply(self, status):
        from neatobmo.routines import _miner_reply
        return _miner_reply({"miner": type("M", (), {"status": lambda s: status})()})

    def test_missing_wallet_asks_for_an_address(self):
        reply = self._reply({"state": "no_address", "address": ""})
        self.assertIn("coin wallet", reply)

    def test_configured_but_off_does_not_ask_again(self):
        reply = self._reply({"state": "disabled",
                             "address": "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa"})
        self.assertNotIn("coin wallet", reply)
        self.assertIn("off", reply)

    def test_mining_reports_hashrate(self):
        reply = self._reply({"state": "mining", "address": "bc1qxyz",
                             "hashrate_label": "1.23 KH/s",
                             "best_seen_difficulty": 4.2})
        self.assertIn("1.23 KH/s", reply)
