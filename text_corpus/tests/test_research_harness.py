"""Mechanical integration checks using real processed corpus passages.

These are software integrity checks, not a client-query benchmark or evidence of
legal answer quality. They do not call a model or create training examples.
Run after process_corpus.py: python -m unittest discover -s tests -v
"""
from __future__ import annotations

import argparse
import copy
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("oracle_research", ROOT / "scripts/research.py")
research = importlib.util.module_from_spec(spec)
spec.loader.exec_module(research)


class ResearchHarnessIntegrity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = ROOT / "processed/corpus.sqlite"
        if not path.exists():
            raise unittest.SkipTest("Build the real corpus first; no synthetic evaluation data is substituted")
        cls.corpus = research.Corpus(path)
        temporary_root = ROOT / "tmp"
        temporary_root.mkdir(exist_ok=True)
        cls.temporary = tempfile.TemporaryDirectory(dir=temporary_root)
        cls.resolutions = Path(cls.temporary.name) / "decisions.jsonl"
        args = argparse.Namespace(query="Medicaid", limit=2, include_indexes=False, include_chunk=[],
                                  program=None, plan_type=None, research_date=None, resolutions=cls.resolutions)
        cls.packet = research.make_packet(cls.corpus, args)
        if not cls.packet["evidence"]:
            raise unittest.SkipTest("No Medicaid passage available in the real corpus")

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "corpus"):
            cls.corpus.close()
        if hasattr(cls, "temporary"):
            cls.temporary.cleanup()

    def response(self):
        evidence = self.packet["evidence"][0]
        span = evidence["quote_candidates"][0]
        return {"status": "supported", "scope": copy.deepcopy(self.packet["scope"]),
                "findings": [{"text": "Selected passage from the retrieved document.", "evidence_ids": [evidence["evidence_id"]]}],
                "quotes": [{"chunk_id": evidence["chunk_id"], "start": span["start"], "end": span["end"]}],
                "uncertainties": [], "clarifying_questions": []}

    def test_collection_indexes_excluded(self):
        results = self.corpus.search("Medicaid", limit=30)
        self.assertTrue(results)
        self.assertTrue(all(item["source_role"] != "collection_index" for item in results))

    def test_quotation_is_exact_and_source_url_is_canonical(self):
        response = self.response()
        result = research.render_response(self.packet, response, self.corpus)
        source = self.packet["evidence"][0]
        quote = response["quotes"][0]
        self.assertEqual(result["quotes"][0]["text"], source["text"][quote["start"]:quote["end"]])
        self.assertEqual(result["quotes"][0]["citation"]["source_url"], source["source_url"])
        self.assertFalse(result["verification"]["semantic_correctness_verified"])
        self.assertEqual(result["corpus_caveat"], self.packet["corpus"]["collection_caveat"])
        self.assertEqual(result["corpus_coverage"]["unavailable_sources"], self.packet["corpus"]["unavailable_sources"])
        for key in ("customer_reference_ids", "customer_inventory_status", "target_resolution_status", "version_resolution_status"):
            self.assertEqual(result["quotes"][0]["citation"][key], source[key])

    def test_fabricated_evidence_is_rejected(self):
        response = self.response()
        response["findings"][0]["evidence_ids"] = ["E99999"]
        with self.assertRaises(research.HarnessError):
            research.render_response(self.packet, response, self.corpus)

    def test_out_of_bounds_quote_is_rejected(self):
        response = self.response()
        response["quotes"][0]["end"] = len(self.packet["evidence"][0]["text"]) + 1
        with self.assertRaises(research.HarnessError):
            research.render_response(self.packet, response, self.corpus)

    def test_tampered_packet_text_rejected_even_with_recomputed_packet_hash(self):
        packet = copy.deepcopy(self.packet)
        packet["evidence"][0]["text"] += "Altered text."
        packet["evidence"][0]["text_sha256"] = research.digest(packet["evidence"][0]["text"])
        packet["packet_id"] = research.packet_hash(packet)
        with self.assertRaises(research.HarnessError):
            research.render_response(packet, self.response(), self.corpus)

    def test_generated_url_is_rejected(self):
        response = self.response()
        response["findings"][0]["text"] = "See https://example.invalid/invented-source"
        with self.assertRaises(research.HarnessError):
            research.render_response(self.packet, response, self.corpus)

    def test_confidence_state_cannot_hide_explicit_uncertainty(self):
        response = self.response()
        response["uncertainties"] = [{"kind": "test_only", "description": "Mechanical status consistency check.",
                                      "evidence_ids": [], "question_for_user": None}]
        with self.assertRaises(research.HarnessError):
            research.render_response(self.packet, response, self.corpus)

    def test_human_correction_appends_without_rewriting(self):
        path = Path(self.temporary.name) / "append-check.jsonl"
        args = argparse.Namespace(evidence_id=["E1"], human_decision="Test-only user selection; no legal relationship determined.",
                                  description="Mechanical append-only storage check.", reviewer="test_alias", kind="test_only",
                                  resolutions=path, output=None, supersedes_record_id=None)
        first = research.append_resolution(self.packet, args, self.corpus)
        before = path.read_bytes()
        args.supersedes_record_id = first["record_id"]
        args.human_decision = "Test-only correction; no source metadata changed."
        research.append_resolution(self.packet, args, self.corpus)
        self.assertTrue(path.read_bytes().startswith(before))
        self.assertEqual(len(path.read_text().splitlines()), 2)
        records = research.read_resolutions(path, self.packet["scope"], self.packet["evidence"])
        self.assertEqual([r["active"] for r in records], [False, True])
        self.assertTrue(all(r["source_fact"] is False and r["updates_source_metadata"] is False for r in records))

    def test_remote_inference_url_rejected_without_network(self):
        with self.assertRaises(research.HarnessError):
            research.local_endpoint("https://example.invalid/v1/chat/completions")

    def test_empty_retrieval_does_not_invoke_a_model(self):
        packet = copy.deepcopy(self.packet)
        packet["evidence"] = []
        packet["human_resolutions"] = []
        packet["retrieval"]["evidence_count"] = 0
        packet["packet_id"] = research.packet_hash(packet)
        with mock.patch("urllib.request.build_opener") as network:
            result = research.infer(packet, argparse.Namespace(), self.corpus)
            network.assert_not_called()
        self.assertEqual(result["status"], "not_found")
        self.assertFalse(result["model_invoked"])


class ProvenanceIntegrity(unittest.TestCase):
    """Metadata-only fixtures check bookkeeping; no legal query or answer is invented."""

    def source(self, **changes):
        value = {"document_id": "metadata-fixture", "processing_status": "processed",
                 "source_origin": "customer_pdf_link", "source_url": "https://example.invalid/metadata-fixture",
                 "acquisition_status": "downloaded", "source_role": "statutory_text",
                 "acquisition_metadata": {"customer_inventory_status": "explicitly_listed",
                                          "customer_reference_ids": ["reference-fixture"]}}
        value.update(changes)
        return value

    def test_explicit_selection_does_not_clear_other_uncertainties(self):
        doc = self.source(uncertainty_flags=["original_inventory_missing", "original_resource_inventory_not_reconciled",
                                            "target_label_mismatch", "historical_edition_currentness_not_verified"],
                          acquisition_metadata={"customer_inventory_status": "explicitly_listed",
                                                "customer_reference_ids": ["reference-fixture"],
                                                "target_resolution_status": "human_review_required",
                                                "version_resolution_status": "unverified",
                                                "review_flags": ["alternate_source_not_verified"]})
        evidence = research.recorded_provenance(doc)
        self.assertTrue(evidence["customer_selection_explicitly_recorded"])
        self.assertEqual(set(evidence["uncertainty_flags"]), {"target_label_mismatch", "historical_edition_currentness_not_verified",
                                                           "alternate_source_not_verified"})
        self.assertEqual(evidence["version_resolution_status"], "unverified")
        summary = research.summarize_provenance([doc])
        self.assertTrue(summary["customer_inventory"]["provided_inventory_recorded"])
        self.assertFalse(summary["customer_inventory"]["all_customer_targets_or_versions_verified"])
        self.assertNotIn("inventory is missing", summary["collection_caveat"])
        self.assertEqual(summary["recorded_uncertainty_flag_counts"]["target_label_mismatch"], 1)

    def test_both_provenance_fields_are_required_without_guessing_from_url(self):
        direct_without_status = self.source(acquisition_metadata={})
        alternate_with_status = self.source(source_origin="alternate_source")
        for doc in (direct_without_status, alternate_with_status):
            doc["uncertainty_flags"] = ["original_resource_inventory_not_reconciled"]
            self.assertFalse(research.recorded_provenance(doc)["customer_selection_explicitly_recorded"])
            self.assertIn("original_resource_inventory_not_reconciled", research.recorded_provenance(doc)["uncertainty_flags"])
        summary = research.summarize_provenance([direct_without_status, alternate_with_status])
        self.assertEqual(summary["customer_inventory"]["other_or_unconfirmed_source_record_count"], 2)
        self.assertFalse(summary["customer_inventory"]["provided_inventory_recorded"])

    def test_blocked_sources_and_collection_indexes_remain_visible(self):
        blocked = self.source(document_id="blocked-fixture", acquisition_status="http_403", processing_status="not_acquired")
        index = self.source(document_id="index-fixture", source_role="collection_index", processing_status="index_only")
        summary = research.summarize_provenance([blocked, index])
        self.assertEqual(summary["customer_inventory"]["explicitly_listed_source_record_count"], 2)
        self.assertEqual(summary["unavailable_sources"][0]["source_url"], blocked["source_url"])
        self.assertEqual(summary["unavailable_sources"][0]["processing_status"], "not_acquired")
        self.assertEqual(summary["unavailable_sources"][0]["customer_reference_ids"], ["reference-fixture"])
        self.assertEqual(summary["collection_index_document_ids"], ["index-fixture"])
        self.assertFalse(summary["collection_expansion_completeness_verified"])
        self.assertEqual(summary["processing_status_counts"], {"index_only": 1, "not_acquired": 1})


class CapacityArithmeticIntegrity(unittest.TestCase):
    """Financial arithmetic only; these scenarios are not model measurements."""

    def arguments(self, **changes):
        values = dict(gpu_hourly_rate=1.0, active_hours_per_year=2000.0,
                      annual_budget=10000.0, queries_per_hour=60.0,
                      input_tokens=12000, output_tokens=1500)
        values.update(changes)
        return argparse.Namespace(**values)

    def test_budget_bounds_and_required_workload_remain_assumptions(self):
        result = research.capacity_estimate(self.arguments())
        self.assertEqual(result["annual_gpu_cost"], 2000.0)
        self.assertEqual(result["maximum_running_hours_at_supplied_rate"], 10000.0)
        self.assertEqual(result["maximum_hourly_rate_for_supplied_hours"], 5.0)
        self.assertEqual(result["workload"]["assumed_annual_queries"], 120000.0)
        self.assertEqual(result["workload"]["required_average_input_tokens_per_second"], 200.0)
        self.assertEqual(result["workload"]["required_average_output_tokens_per_second"], 25.0)
        self.assertFalse(result["model_or_hardware_measured"])
        self.assertFalse(result["workload"]["throughput_measured"])

    def test_continuous_schedule_can_exceed_budget(self):
        result = research.capacity_estimate(self.arguments(gpu_hourly_rate=1.5, active_hours_per_year=8760.0))
        self.assertEqual(result["annual_gpu_cost"], 13140.0)
        self.assertEqual(result["budget_remaining_after_gpu"], -3140.0)
        self.assertFalse(result["within_gpu_budget_under_assumptions"])
        self.assertAlmostEqual(result["maximum_hourly_rate_for_supplied_hours"], 1.141553, places=6)

    def test_missing_token_counts_do_not_invent_throughput(self):
        result = research.capacity_estimate(self.arguments(queries_per_hour=0.0, input_tokens=None, output_tokens=None))
        self.assertIsNone(result["gpu_cost_per_assumed_query"])
        self.assertIsNone(result["workload"]["required_average_input_tokens_per_second"])
        self.assertIsNone(result["workload"]["required_average_output_tokens_per_second"])

    def test_invalid_rate_or_hours_fail_before_division(self):
        for change in ({"gpu_hourly_rate": 0.0}, {"active_hours_per_year": 0.0},
                       {"annual_budget": -1.0}, {"queries_per_hour": float("nan")}):
            with self.subTest(change=change), self.assertRaises(research.HarnessError):
                research.capacity_estimate(self.arguments(**change))


if __name__ == "__main__":
    unittest.main()
