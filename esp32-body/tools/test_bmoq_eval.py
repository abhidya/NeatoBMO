#!/usr/bin/env python3

from __future__ import annotations

import json
import math
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))

from bmoq_eval.compare_quantization import build_report
from bmoq_eval.eval_bmoq import copy_validated_jsonl
from bmoq_eval.eval_hf_olmoe import logit_payload, router_topk
from bmoq_eval.make_fixture import records
from bmoq_eval.schema import load_eval_records, validate_eval_record, write_jsonl


class BmoqEvalTests(unittest.TestCase):
    def test_fixture_records_match_schema(self) -> None:
        bf16 = records("bf16")
        bmoq = records("bmoq")
        self.assertEqual(len(bf16), 6)
        self.assertEqual(len(bmoq), 6)
        validate_eval_record(bf16[0])
        self.assertEqual(bf16[0]["router"][0]["top_experts"], list(range(8)))

    def test_compare_report_has_primary_metrics_and_strata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reference = root / "bf16.jsonl"
            candidate = root / "bmoq.jsonl"
            write_jsonl(reference, records("bf16"))
            write_jsonl(candidate, records("bmoq"))
            report = build_report(reference, candidate)

            self.assertEqual(report["overall"]["count"], 6)
            self.assertGreater(report["overall"]["delta_nll"], 0.0)
            self.assertIn("layer", report["by"])
            self.assertIn("prompt_category", report["by"])
            self.assertIn("tensor_precision", report["by"])
            self.assertIn("expert", report["by"])
            self.assertLess(report["overall"]["router_overlap"], 1.0)

    def test_compare_rejects_misaligned_records(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reference = root / "bf16.jsonl"
            candidate = root / "bmoq.jsonl"
            candidate_records = records("bmoq")
            candidate_records[0]["position"] = 99
            write_jsonl(reference, records("bf16"))
            write_jsonl(candidate, candidate_records)
            with self.assertRaisesRegex(ValueError, "misaligned"):
                build_report(reference, candidate)

    def test_compare_rejects_duplicate_record_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reference = root / "bf16.jsonl"
            candidate = root / "bmoq.jsonl"
            reference_records = records("bf16")
            candidate_records = records("bmoq")
            reference_records[1] = dict(reference_records[0])
            candidate_records[1] = dict(candidate_records[0])
            write_jsonl(reference, reference_records)
            write_jsonl(candidate, candidate_records)
            with self.assertRaisesRegex(ValueError, "duplicate"):
                build_report(reference, candidate)

    def test_compare_rejects_inconsistent_variants(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reference = root / "bf16.jsonl"
            candidate = root / "bmoq.jsonl"
            candidate_records = records("bmoq")
            candidate_records[1] = dict(candidate_records[1])
            candidate_records[1]["variant"] = dict(candidate_records[1]["variant"])
            candidate_records[1]["variant"]["tensor_precision"] = "Q8_ROUTER"
            write_jsonl(reference, records("bf16"))
            write_jsonl(candidate, candidate_records)
            with self.assertRaisesRegex(ValueError, "variant changed"):
                build_report(reference, candidate)

    def test_compare_rejects_extra_candidate_records(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reference = root / "bf16.jsonl"
            candidate = root / "bmoq.jsonl"
            candidate_records = records("bmoq")
            extra = dict(candidate_records[-1])
            extra["sample_id"] = "extra"
            candidate_records.append(extra)
            write_jsonl(reference, records("bf16"))
            write_jsonl(candidate, candidate_records)
            with self.assertRaisesRegex(ValueError, "extra record"):
                build_report(reference, candidate)

    def test_adapter_copies_only_valid_eval_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.jsonl"
            output = root / "output.jsonl"
            write_jsonl(source, records("bmoq"))
            copy_validated_jsonl(source, output)
            self.assertEqual(len(load_eval_records(output)), 6)

    def test_hf_router_topk_softmaxes_gate_logits_into_runtime_units(self) -> None:
        # output_router_logits=True returns raw pre-softmax gate logits, while
        # coli_mcu select_routes reports post-softmax routing weights. Without
        # this softmax the router-weight error measures a units mismatch.
        route = router_topk([[[2.0, 1.0, 0.0]]], token_index=0, top_k=2)
        self.assertEqual(route[0]["top_experts"], [0, 1])
        expected = [math.exp(2.0), math.exp(1.0), math.exp(0.0)]
        total = sum(expected)
        self.assertAlmostEqual(route[0]["weights"][0], expected[0] / total, places=9)
        self.assertAlmostEqual(route[0]["weights"][1], expected[1] / total, places=9)
        self.assertAlmostEqual(sum(route[0]["weights"]), 0.9096, places=3)

    def test_hf_router_topk_renormalizes_only_when_model_asks(self) -> None:
        # OLMoE-1B-7B-0924 ships norm_topk_prob=false, so the default must not
        # renormalize; the flag exists to mirror models that do.
        route = router_topk([[[2.0, 1.0, 0.0]]], token_index=0, top_k=2, norm_topk_prob=True)
        self.assertAlmostEqual(sum(route[0]["weights"]), 1.0, places=9)

    def test_hf_sparse_logit_payload_keeps_full_nll_without_dense_infinity(self) -> None:
        nll, dense, sparse = logit_payload([10.0, 9.0, 0.0], token_id=2, top_k=2)
        self.assertGreater(nll, 0.0)
        self.assertEqual(dense, [])
        self.assertEqual([entry["token_id"] for entry in sparse], [0, 1])

    def test_sparse_logit_comparison_disables_distribution_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reference = root / "bf16.jsonl"
            candidate = root / "bmoq.jsonl"
            reference_records = records("bf16")
            candidate_records = records("bmoq")
            for row in reference_records + candidate_records:
                top = sorted(range(len(row["logits"])), key=lambda index: (-row["logits"][index], index))[:5]
                row["logit_top_k"] = [{"token_id": index, "logit": row["logits"][index]} for index in top]
                row["logits"] = []
            write_jsonl(reference, reference_records)
            write_jsonl(candidate, candidate_records)
            report = build_report(reference, candidate)
            self.assertIsNone(report["overall"]["kl"])
            self.assertIsNone(report["overall"]["cosine"])
            self.assertEqual(report["overall"]["top1_agreement"], 1.0)

    def test_make_fixture_cli_persists_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "fixture"
            subprocess.run(
                [
                    sys.executable,
                    str(TOOLS / "bmoq_eval" / "make_fixture.py"),
                    "--output-dir",
                    str(out),
                ],
                check=True,
            )
            report = json.loads((out / "bmoq-vs-bf16.json").read_text(encoding="utf-8"))
            self.assertEqual(report["comparison"], "bf16_reference_vs_bmoq_candidate")
            self.assertEqual(report["paired_categorical"]["tokens_compared"], 6)

    def test_persisted_tiny_report_matches_regenerated_structure(self) -> None:
        fixture = TOOLS / "bmoq_eval" / "fixtures" / "tiny"
        with tempfile.TemporaryDirectory() as tmp:
            regenerated = Path(tmp) / "bmoq-vs-bf16.json"
            subprocess.run(
                [
                    sys.executable,
                    str(TOOLS / "bmoq_eval" / "compare_quantization.py"),
                    "--reference",
                    "esp32-body/tools/bmoq_eval/fixtures/tiny/bf16-results.jsonl",
                    "--candidate",
                    "esp32-body/tools/bmoq_eval/fixtures/tiny/bmoq-results.jsonl",
                    "--report",
                    str(regenerated),
                ],
                check=True,
            )
            expected = json.loads((fixture / "bmoq-vs-bf16.json").read_text(encoding="utf-8"))
            actual = json.loads(regenerated.read_text(encoding="utf-8"))
            self.assertEqual(actual, expected)


if __name__ == "__main__":
    unittest.main()
