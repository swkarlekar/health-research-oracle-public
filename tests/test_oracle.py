"""Offline control-flow tests. Synthetic policy text is not a legal benchmark."""
import json
import unittest
from unittest.mock import Mock, patch

from oracle.aws import ApprovalGate, BedrockCorpus, BedrockModel, export_ingestion, upload_ingestion
from oracle.corpus import ApprovedCorpus, HarnessError, base
from oracle.harness import Harness, ensure_quotes
from oracle.providers import LocalModel
from oracle.policy import check_response_boundary
from support import Fixture, SCOPE, ScriptedModel, answer










class HarnessTests(Fixture):
    def test_punctuation_only_findings_are_rejected_even_with_valid_citations(self):
        response = answer(self.corpus.packet("Synthetic alpha", SCOPE))
        for text in ('!', ".'", "!'", '123.'):
            response['findings'][0]['text'] = text
            with self.subTest(text=text), self.assertRaises(HarnessError):
                check_response_boundary(response)

    def test_missing_scope_asks_questions_without_retrieval_or_model(self):
        model = Mock()
        with patch.object(self.corpus, "search") as search:
            result = Harness(self.corpus, model).run("Synthetic alpha?", {})
        self.assertEqual(result["status"], "clarification_needed")
        self.assertEqual(len(result["clarifying_questions"]), 3)
        search.assert_not_called()
        model.complete.assert_not_called()

    def test_unknown_is_a_valid_scope_value(self):
        scope = {key: "unknown" for key in SCOPE}
        result = Harness(self.corpus).run("Synthetic alpha?", scope, evidence_only=True)
        self.assertEqual(result["status"], "evidence_only")

    def test_no_retrieval_abstains_without_model(self):
        model = Mock()
        result = Harness(self.corpus, model).run("zzzzunfindablezzzz", SCOPE)
        self.assertEqual(result["status"], "not_found")
        model.complete.assert_not_called()

    def test_review_is_required_and_quotes_are_exact_unicode(self):
        model = ScriptedModel()
        result = Harness(self.corpus, model).run("Synthetic alpha", SCOPE)
        self.assertEqual(model.calls, 2)
        self.assertEqual(result["status"], "supported")
        quote = result["quotes"][0]
        self.assertEqual(quote["text"], self.corpus.chunk(quote["chunk_id"])["text"])
        self.assertTrue(quote["citation"]["source_url"].startswith("https://example.org/synthetic/"))
        self.assertFalse(result["verification"]["semantic_correctness_verified"])

    def test_bounded_additional_search_then_explicit_limits(self):
        model = ScriptedModel(search=True, conflict=True)
        result = Harness(self.corpus, model, limit=1).run("Synthetic alpha", SCOPE)
        self.assertEqual(result["harness"]["searches"], 2)
        self.assertEqual(model.calls, 3)
        self.assertEqual(result["status"], "conflicting_sources")
        self.assertIn("search_limit", [u["kind"] for u in result["uncertainties"]])
        self.assertFalse(model.payloads[-1]["additional_search_available"])

    def test_review_failure_does_not_release_supported_findings(self):
        with self.assertRaises(HarnessError):
            Harness(self.corpus, ScriptedModel(bad_support=True)).run("Synthetic alpha", SCOPE)

    def test_missing_quote_selection_uses_whole_canonical_excerpt(self):
        packet = self.corpus.packet("Synthetic alpha", SCOPE)
        response = answer(packet)
        response["quotes"] = []
        result = base.render_response(packet, ensure_quotes(packet, response), self.corpus)
        self.assertEqual(result["quotes"][0]["text"], packet["evidence"][0]["text"])

    def test_invalid_quote_selection_is_never_repaired(self):
        packet = self.corpus.packet("Synthetic alpha", SCOPE)
        response = answer(packet)
        response["quotes"][0]["end"] = 999999
        with self.assertRaises(HarnessError):
            base.render_response(packet, ensure_quotes(packet, response), self.corpus)

    def test_unsupported_confidence_is_lowered_without_discarding_uncertainty(self):
        packet = self.corpus.packet("Synthetic alpha", SCOPE)
        response = answer(packet)
        response["uncertainties"] = [{"kind": "missing_exception", "description": "A synthetic exception remains unresolved.",
                                       "evidence_ids": ["E1"], "question_for_user": None}]
        result = base.render_response(packet, ensure_quotes(packet, response), self.corpus)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["uncertainties"][0]["kind"], "missing_exception")

    def test_document_last_modified_is_labeled_as_secondary_proxy(self):
        packet = self.corpus.packet("Synthetic alpha", SCOPE)
        response = answer(packet)
        review = {"metadata_proxy_evidence_ids": ["E1"], "checks": {}, "issues": []}
        result = Harness(self.corpus).finish(packet, response, 1, 2, review)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["metadata_date_proxies_used"][0]["metadata_field"], "pdf_modification_date_raw")
        self.assertEqual(result["metadata_date_proxies_used"][0]["raw_value"], "D:20260102030405Z")
        self.assertIn("metadata_date_proxy", [u["kind"] for u in result["uncertainties"]])
        self.assertFalse(result["verification"]["legal_currentness_verified"])

    def test_unavailable_metadata_proxy_is_rejected(self):
        packet = self.corpus.packet("Synthetic alpha", SCOPE)
        response = answer(packet)
        with self.assertRaises(HarnessError):
            Harness(self.corpus).finish(packet, response, 1, 2,
                {"metadata_proxy_evidence_ids": ["E999"], "checks": {}, "issues": []})

    def test_retrieval_date_is_never_used_as_last_modified_proxy(self):
        packet = self.corpus.packet("Synthetic alpha", SCOPE)
        for item in packet["evidence"]:
            item["date_signals"] = {"pdf_modification_date_raw": None, "http_last_modified_raw": None,
                                    "pdf_creation_date_raw": "D:20260102"}
            item["retrieved_at"] = "2026-01-03T00:00:00Z"
        self.assertEqual(self.corpus.proxy_candidates(packet), {})

    def test_pii_input_never_reaches_model(self):
        model = Mock()
        for question in ["member id: 123", "Email me at a@example.org", "SSN 123-45-6789"]:
            with self.subTest(question=question), self.assertRaises(HarnessError):
                Harness(self.corpus, model).run(question, SCOPE)
        model.complete.assert_not_called()

    def test_disallowed_source_cannot_enter_retrieval_or_explicit_chunk(self):
        self.corpus.policy["doc0"]["retrieval_allowed"] = False
        self.assertTrue(all(e["document_id"] != "doc0" for e in self.corpus.search("alpha")))
        with self.assertRaises(HarnessError):
            self.corpus.chunk("chunk0")

    def test_registry_cannot_be_reused_on_changed_snapshot(self):
        value = json.loads(self.registry.read_text())
        value["snapshot_sha256"] = "wrong"
        self.registry.write_text(json.dumps(value))
        with self.assertRaises(HarnessError):
            ApprovedCorpus(self.db, self.registry)

    def test_non_loopback_model_endpoint_rejected(self):
        for endpoint in ["https://paid.example/api/chat", "http://localhost:11434/api/chat?x=1", "http://localhost:11434/other"]:
            with self.subTest(endpoint=endpoint), self.assertRaises(HarnessError):
                LocalModel("model", endpoint)

    def test_cloud_template_requires_explicit_approval_for_every_resource(self):
        from oracle.corpus import ROOT
        template = json.loads((ROOT / "infra/stack.json").read_text())
        self.assertEqual(template["Parameters"]["PaidResourcesApproved"]["Default"], "no")
        self.assertTrue(all(r.get("Condition") == "Approved" for r in template["Resources"].values()))


class AwsTests(Fixture):
    def test_missing_approval_blocks_before_sdk_client(self):
        model = BedrockModel("synthetic-model", "us-east-1", ApprovalGate(self.root / "missing.json"))
        with patch("oracle.aws.aws_client") as client, self.assertRaises(HarnessError):
            model.complete("system", {}, {"type": "object"})
        client.assert_not_called()

    def test_expired_wrong_region_wrong_resource_or_exhausted_grants_block(self):
        for changes in [{"expires_at": "2020-01-01T00:00:00+00:00"}, {"region": "eu-west-1"},
                        {"resource": {"model_id": "other"}}, {"max_calls": 1, "used_calls": 1},
                        {"user_approved": False}, {"max_calls": True}]:
            with self.subTest(changes=changes):
                model = BedrockModel("synthetic-model", "us-east-1", self.grant(**changes))
                with patch("oracle.aws.aws_client") as client, self.assertRaises(HarnessError):
                    model.complete("system", {}, {"type": "object"})
                client.assert_not_called()

    def test_approved_mock_converse_and_call_budget(self):
        gate = self.grant(max_calls=1)
        model = BedrockModel("synthetic-model", "us-east-1", gate)
        fake = Mock()
        fake.converse.return_value = {"stopReason": "end_turn", "output": {"message": {"content": [{"text": '{"ok":true}'}]}}}
        schema = {"type": "object", "additionalProperties": False, "required": ["ok"], "properties": {"ok": {"type": "boolean"}}}
        with patch("oracle.aws.aws_client", return_value=fake):
            self.assertEqual(model.complete("system", {}, schema), {"ok": True})
            with self.assertRaises(HarnessError):
                model.complete("system", {}, schema)
        self.assertEqual(fake.converse.call_count, 1)
        self.assertEqual(fake.converse.call_args.kwargs["inferenceConfig"]["maxTokens"], 2400)

    def test_failed_network_attempt_consumes_budget(self):
        gate = self.grant(max_calls=1)
        with patch("oracle.aws.aws_client", side_effect=OSError("offline")), self.assertRaises(OSError):
            BedrockModel("synthetic-model", "us-east-1", gate).complete("sys", {}, {})
        self.assertEqual(json.loads(gate.path.read_text())["used_calls"], 1)

    def test_export_is_local_and_preserves_quote_text_and_metadata(self):
        with patch("oracle.aws.aws_client") as client:
            manifest = export_ingestion(self.corpus, self.root / "export")
        client.assert_not_called()
        self.assertEqual(manifest["file_count"], 4)
        self.assertEqual((self.root / "export/chunk0.txt").read_text(), self.corpus.chunk("chunk0")["text"])
        meta = json.loads((self.root / "export/chunk0.txt.metadata.json").read_text())["metadataAttributes"]
        self.assertEqual(meta["snapshot_sha256"], self.corpus.summary()["snapshot_sha256"])

    def test_upload_requires_matching_manifest_grant(self):
        directory = self.root / "export"
        export_ingestion(self.corpus, directory)
        gate = self.grant(action="s3:PutObject", resource={"bucket": "wrong"})
        with patch("oracle.aws.aws_client") as client, self.assertRaises(HarnessError):
            upload_ingestion(directory, "bucket", "corpus", "us-east-1", gate)
        client.assert_not_called()

    def test_tampered_export_blocks_before_network(self):
        directory = self.root / "export"
        export_ingestion(self.corpus, directory)
        (directory / "chunk0.txt").write_text("tampered")
        with patch("oracle.aws.aws_client") as client, self.assertRaises(HarnessError):
            upload_ingestion(directory, "bucket", "corpus", "us-east-1", self.grant())
        client.assert_not_called()

    def test_managed_retrieval_resolves_only_pinned_canonical_chunks(self):
        resource = {"knowledge_base_id": "kb-test", "max_results": 30,
                    "snapshot_sha256": self.corpus.summary()["snapshot_sha256"]}
        corpus = BedrockCorpus(self.db, self.registry, "kb-test", "us-east-1", self.grant("bedrock:Retrieve", resource))
        self.addCleanup(corpus.close)
        fake = Mock()
        fake.retrieve.return_value = {"retrievalResults": [{"metadata": {
            "snapshot_sha256": resource["snapshot_sha256"], "chunk_id": "chunk0",
            "text_sha256": self.corpus.chunk("chunk0")["text_sha256"]},
            "content": {"text": "Untrusted managed response is never quoted directly."}}]}
        with patch("oracle.aws.aws_client", return_value=fake):
            result = corpus.search("alpha")
            self.assertEqual(result[0]["text"], self.corpus.chunk("chunk0")["text"])
            fake.retrieve.return_value["retrievalResults"][0]["metadata"]["text_sha256"] = "tampered"
            with self.assertRaises(HarnessError):
                corpus.search("alpha")


if __name__ == "__main__":
    unittest.main()
