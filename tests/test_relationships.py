"""Offline graph coverage and evidence tests; synthetic documents are not legal advice."""
import copy
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from oracle.corpus import HarnessError, base
from oracle.relationships import GraphStore, build_graph, _citation_matches, _base_payload


class CorpusFixture:
    def __init__(self, documents, sections):
        self.connection = sqlite3.connect(":memory:")
        self.connection.executescript("CREATE TABLE documents(document_id TEXT,metadata_json TEXT);"
                                     "CREATE TABLE chunks(chunk_id TEXT,metadata_json TEXT);"
                                     "CREATE TABLE chunks_fts(chunk_id TEXT,text TEXT);")
        for doc in documents:
            self.connection.execute("INSERT INTO documents VALUES(?,?)", (doc["document_id"], base.canonical({k: v for k, v in doc.items() if k != "text"})))
        for section in sections:
            if not section["text"]:
                continue
            chunk = {"chunk_id": section["section_id"] + "_c1", "document_id": section["document_id"],
                     "section_id": section["section_id"], "section_start_char": 0,
                     "section_end_char": len(section["text"]), "text_sha256": section["text_sha256"]}
            self.connection.execute("INSERT INTO chunks VALUES(?,?)", (chunk["chunk_id"], base.canonical(chunk)))
            self.connection.execute("INSERT INTO chunks_fts VALUES(?,?)", (chunk["chunk_id"], section["text"]))
        self.connection.commit()

    def summary(self):
        records = list(self.connection.execute("SELECT document_id,metadata_json FROM documents ORDER BY document_id"))
        snapshot = base.hashlib.sha256()
        for doc_id, metadata in records:
            snapshot.update(doc_id.encode() + b"\0" + metadata.encode() + b"\n")
        return {"snapshot_sha256": snapshot.hexdigest()}


class GraphTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.documents, self.sections = [], []
        self.add_document("a", "MLTC Policy 16.06", "Synthetic rule A. Café 🏥.\n\nA dated source.")
        self.add_document("b", "MLTC Policy 17.02", "This synthetic policy clarifies MLTC Policy 16.06.\n\nFurther reference: https://example.org/a\n\nUnknown reference: Policy 99.99.")
        self.add_document("c", "Unavailable source", "", processing_status="not_acquired")
        self.corpus = CorpusFixture(self.documents, self.sections)
        self.addCleanup(self.corpus.connection.close)
        self.doc_path, self.section_path = self.root / "documents.jsonl", self.root / "sections.jsonl"
        self.graph_path = self.root / "graph.sqlite"
        self.write_inputs()

    def add_document(self, doc_id, title, text, processing_status="processed"):
        doc = {"document_id": doc_id, "title": title, "source_url": f"https://example.org/{doc_id}",
               "resolved_url": f"https://example.org/{doc_id}", "text": text,
               "text_sha256": base.digest(text), "raw_sha256": base.digest("raw" + text),
               "processing_status": processing_status, "section_count": 1 if text else 0,
               "date_signals": {"pdf_modification_date_raw": "D:20260102030405Z"}}
        self.documents.append(doc)
        if text:
            self.sections.append({"section_id": doc_id + "_s1", "document_id": doc_id,
                                  "section_index": 1, "text": text, "text_sha256": base.digest(text),
                                  "raw_sha256": doc["raw_sha256"], "document_start_char": 0,
                                  "document_end_char": len(text), "page": 1, "heading_path": [title], "tables": []})

    def write_inputs(self):
        self.doc_path.write_text("\n".join(base.canonical(d) for d in self.documents) + "\n")
        self.section_path.write_text("\n".join(base.canonical(s) for s in self.sections) + "\n")

    def build(self):
        report = build_graph(self.corpus, self.doc_path, self.section_path, self.graph_path)
        self.graph = GraphStore(self.graph_path, self.corpus.summary()["snapshot_sha256"])
        self.addCleanup(self.graph.close)
        return report

    def semantic(self):
        return next(r for r in self.graph.relationships_for("b") if r["relation_type"] == "clarifies")

    def test_every_document_and_section_accounted_for_including_missing(self):
        report = self.build()
        self.assertEqual(report["document_count"], 3)
        self.assertEqual(report["sections_scanned"], 2)
        self.assertEqual(report["documents_without_text"], 1)
        self.assertEqual(self.graph.document("c")["processing_status"], "not_acquired")
        self.assertFalse(report["semantic_completeness_verified"])

    def test_complete_canonical_text_and_context_are_available(self):
        self.build()
        self.assertEqual(self.graph.document("a")["text"], self.documents[0]["text"])
        section = self.graph.sections_for_document("a")[0]
        self.assertEqual(section["heading_path"], ["MLTC Policy 16.06"])
        self.assertEqual(section["document_end_char"], len(self.documents[0]["text"]))

    def test_unique_reference_is_structurally_verified_but_semantic_edge_is_candidate(self):
        self.build()
        edges = self.graph.relationships_for("b")
        reference = next(r for r in edges if r["target_reference"] == "https://example.org/a")
        self.assertEqual(reference["status"], "verified_reference")
        self.assertEqual(reference["target_document_id"], "a")
        semantic = self.semantic()
        self.assertEqual(semantic["status"], "candidate")
        self.assertEqual(semantic["target_document_id"], "a")
        self.assertFalse(semantic["qualifiers"]["semantic_correctness_verified"])

    def test_unknown_reference_remains_unresolved(self):
        self.build()
        record = next(r for r in self.graph.relationships_for("b") if r["target_reference"] == "Policy 99.99")
        self.assertIsNone(record["target_document_id"])
        self.assertEqual(record["status"], "unresolved")

    def test_incoming_relationship_found_when_starting_from_original(self):
        self.build()
        record = next(r for r in self.graph.relationships_for("a") if r["relation_type"] == "clarifies")
        self.assertEqual(record["direction"], "incoming")
        self.assertEqual(record["source_document_id"], "b")

    def test_metadata_dates_never_create_supersession_edges(self):
        self.build()
        self.assertFalse(any(r["relation_type"].startswith("supersedes") for r in self.graph.relationships_for("a")))

    def test_ambiguous_policy_identity_not_silently_resolved(self):
        self.add_document("d", "MLTC Policy 16.06", "A different recorded resource with the same policy identifier.")
        self.corpus.connection.close()
        self.corpus = CorpusFixture(self.documents, self.sections)
        self.addCleanup(self.corpus.connection.close)
        self.write_inputs()
        self.build()
        record = self.semantic()
        self.assertIsNone(record["target_document_id"])
        self.assertEqual(record["qualifiers"]["resolution"], "ambiguous")
        self.assertEqual(record["qualifiers"]["possible_target_document_ids"], ["a", "d"])

    def test_altered_document_text_blocks_build_without_partial_output(self):
        self.documents[0]["text"] += "tampered"
        self.write_inputs()
        with self.assertRaises(HarnessError):
            build_graph(self.corpus, self.doc_path, self.section_path, self.graph_path)
        self.assertFalse(self.graph_path.exists())

    def test_omitted_section_blocks_build(self):
        self.sections.pop()
        self.write_inputs()
        with self.assertRaises(HarnessError):
            build_graph(self.corpus, self.doc_path, self.section_path, self.graph_path)
        self.assertFalse(self.graph_path.exists())

    def test_stale_graph_snapshot_rejected(self):
        self.build()
        with self.assertRaises(HarnessError):
            GraphStore(self.graph_path, "different")

    def test_changed_base_relationship_is_detected_on_reopen(self):
        self.build()
        candidate = self.semantic()
        row = self.graph.relationship(candidate["relationship_id"])
        row["target_document_id"] = "c"
        self.graph.connection.execute("UPDATE relationships SET record_json=? WHERE relationship_id=?",
                                      (base.canonical(row), candidate["relationship_id"]))
        self.graph.connection.commit()
        with self.assertRaises(HarnessError):
            GraphStore(self.graph_path)

    def test_changed_section_page_is_detected_on_read_and_reopen(self):
        self.build()
        section = self.graph.section("a_s1")
        section["page"] = 99
        self.graph.connection.execute("UPDATE sections SET record_json=? WHERE section_id='a_s1'", (base.canonical(section),))
        self.graph.connection.commit()
        with self.assertRaises(HarnessError):
            self.graph.section("a_s1")
        with self.assertRaises(HarnessError):
            GraphStore(self.graph_path)

    def test_rehashed_section_forgery_does_not_bypass_base_manifest(self):
        self.build()
        section = self.graph.section("a_s1")
        section["text"] = "Forged text with a recomputed local hash."
        section["text_sha256"] = base.digest(section["text"])
        self.graph.connection.execute("UPDATE sections SET record_json=? WHERE section_id='a_s1'", (base.canonical(section),))
        self.graph.connection.commit()
        with self.assertRaises(HarnessError):
            GraphStore(self.graph_path)

    def test_changed_date_metadata_does_not_bypass_base_manifest(self):
        self.build()
        document = self.graph.document("a")
        document["date_signals"]["pdf_modification_date_raw"] = "D:20990101"
        self.graph.connection.execute("UPDATE documents SET record_json=? WHERE document_id='a'", (base.canonical(document),))
        self.graph.connection.commit()
        with self.assertRaises(HarnessError):
            GraphStore(self.graph_path)

    def test_rehashed_document_metadata_still_has_to_match_corpus_snapshot(self):
        self.build()
        document = self.graph.document("a")
        document["date_signals"]["pdf_modification_date_raw"] = "D:20990101"
        self.graph.connection.execute("UPDATE documents SET record_json=? WHERE document_id='a'", (base.canonical(document),))
        records = {table: {row[0]: json.loads(row[1]) for row in self.graph.connection.execute(f"SELECT {key},record_json FROM {table}")}
                   for table, key in (("documents", "document_id"), ("sections", "section_id"), ("relationships", "relationship_id"))}
        forged_base = base.digest(base.canonical(_base_payload(records["documents"], records["sections"], records["relationships"])))
        self.graph.connection.execute("UPDATE metadata SET value=? WHERE key='graph_base_sha256'", (base.canonical(forged_base),))
        self.graph.connection.commit()
        with self.assertRaises(HarnessError):
            GraphStore(self.graph_path, self.corpus.summary()["snapshot_sha256"])

    def test_external_write_invalidates_cached_summary(self):
        self.build()
        self.graph.summary()
        section = self.graph.section("a_s1")
        section["page"] = 123
        external = sqlite3.connect(self.graph_path)
        try:
            external.execute("UPDATE sections SET record_json=? WHERE section_id='a_s1'", (base.canonical(section),))
            external.commit()
        finally:
            external.close()
        with self.assertRaises(HarnessError):
            self.graph.summary()

    def test_citation_multi_letter_and_unicode_dash_identity_are_complete(self):
        cases = [("42 U.S.C. § 1395eee", "42 USC 1395eee"),
                 ("42 U.S.C. § 1395w–28", "42 USC 1395w-28"),
                 ("42 U.S.C. § 1395w – 28", "42 USC 1395w-28"),
                 ("42 U.S.C. § 1396u-2(b)(4)", "42 USC 1396u-2(b)(4)"),
                 ("N.Y. Social Services Law § 365-a(8)", "NY SSL 365-a(8)")]
        for source, expected in cases:
            with self.subTest(source=source):
                matches = _citation_matches(source)
                self.assertEqual(len(matches), 1)
                self.assertEqual(matches[0][2], expected)
                self.assertEqual(source[matches[0][0]:matches[0][1]], source)

    def test_identifier_does_not_absorb_following_sentence(self):
        text = "42 U.S.C. § 1396a. This is a different sentence."
        match = _citation_matches(text)[0]
        self.assertEqual(match[2], "42 USC 1396a")
        self.assertEqual(text[match[0]:match[1]], "42 U.S.C. § 1396a")
        heading = "42 U.S.C. § 1396a – State plans for medical assistance"
        match = _citation_matches(heading)[0]
        self.assertEqual(match[2], "42 USC 1396a")
        self.assertEqual(heading[match[0]:match[1]], "42 U.S.C. § 1396a")

    def test_forged_ledger_append_is_detected(self):
        self.build()
        candidate = self.semantic()
        first = self.graph.record_review(candidate["relationship_id"], {"decision": "uncertain"}, reviewer="model")
        forged = copy.deepcopy(first)
        forged["created_at"] = "2099-01-01T00:00:00+00:00"
        forged["review_id"] = "review_" + base.digest(base.canonical({k: v for k, v in forged.items() if k != "review_id"}))
        # Rehashing the forged row cannot hide its missing predecessor link.
        self.graph.connection.execute("INSERT INTO reviews(relationship_id,record_json) VALUES(?,?)",
                                      (candidate["relationship_id"], base.canonical(forged)))
        self.graph.connection.commit()
        with self.assertRaises(HarnessError):
            GraphStore(self.graph_path)

    def test_review_evidence_must_be_exact(self):
        self.build()
        record = copy.deepcopy(self.semantic())
        record["evidence"][0]["end"] = 999999
        with self.assertRaises(HarnessError):
            self.graph.validate_relationship(record)
        record = copy.deepcopy(self.semantic())
        record["evidence"][0]["text"] = "An invented quotation"
        with self.assertRaises(HarnessError):
            self.graph.validate_relationship(record)

    def test_model_review_appends_and_does_not_become_verified(self):
        self.build()
        candidate = self.semantic()
        original = self.graph.relationship(candidate["relationship_id"])
        before = self.graph.summary()["graph_content_sha256"]
        review = self.graph.record_review(candidate["relationship_id"], {"decision": "accept", "notes": "Synthetic model agreement"},
                                         reviewer="test-model", expected_relationship_sha256=base.digest(base.canonical(original)))
        self.assertEqual(review["resulting_status"], "model_reviewed")
        self.assertEqual(self.graph.relationship(candidate["relationship_id"]), original)
        self.assertNotEqual(self.graph.summary()["graph_content_sha256"], before)
        self.assertFalse(any(r["relation_type"] == "clarifies" for r in self.graph.relationships_for("a", include_candidates=False)))

    def test_reviewed_uncertainty_is_skipped_on_default_resume(self):
        self.build()
        candidate = self.semantic()
        self.graph.record_review(candidate["relationship_id"], {"decision": "uncertain"}, reviewer="model")
        self.assertNotIn(candidate["relationship_id"], {r["relationship_id"] for r in self.graph.candidates()})
        self.assertIn(candidate["relationship_id"], {r["relationship_id"] for r in self.graph.candidates(include_reviewed=True)})

    def test_explicit_provision_heading_resolves_subsection_into_parent_document(self):
        text = "§ 438.10 Information requirements.\nSynthetic provision text."
        self.documents[0].update(title="42 C.F.R. Part 438", source_url="https://www.ecfr.gov/current/title-42/part-438",
                                 resolved_url="https://www.ecfr.gov/current/title-42/part-438", text=text,
                                 text_sha256=base.digest(text), raw_sha256=base.digest("raw" + text))
        self.sections[0].update(text=text, text_sha256=base.digest(text), raw_sha256=self.documents[0]["raw_sha256"],
                                document_end_char=len(text), heading_path=["§ 438.10 Information requirements."])
        self.add_document("d", "Synthetic scope memo", "See 42 CFR 438.10(a).")
        self.corpus.connection.close()
        self.corpus = CorpusFixture(self.documents, self.sections)
        self.addCleanup(self.corpus.connection.close)
        self.write_inputs()
        self.build()
        record = next(r for r in self.graph.relationships_for("d") if r["target_reference"].startswith("42 CFR"))
        self.assertEqual(record["target_document_id"], "a")
        self.assertEqual(record["qualifiers"]["target_section_id"], "a_s1")
        self.assertEqual(record["qualifiers"]["matched_citation_identity"], "42 CFR 438.10")
        self.assertEqual(record["status"], "verified_reference")

    def test_opioid_withdrawal_is_not_a_document_withdrawal_signal(self):
        text = "Services for opioid withdrawal. See https://example.org/a."
        self.add_document("d", "Synthetic care memo", text)
        self.corpus.connection.close()
        self.corpus = CorpusFixture(self.documents, self.sections)
        self.addCleanup(self.corpus.connection.close)
        self.write_inputs()
        self.build()
        self.assertTrue(all(r["relation_type"] == "references" for r in self.graph.relationships_for("d")))

    def test_model_cannot_claim_human_verification(self):
        self.build()
        candidate = self.semantic()
        with self.assertRaises(HarnessError):
            self.graph.record_review(candidate["relationship_id"], {"decision": "accept", "status": "human_verified"}, reviewer="model")
        self.assertEqual(self.graph.reviews_for(candidate["relationship_id"]), [])

    def test_stale_review_identity_or_snapshot_is_rejected(self):
        self.build()
        candidate = self.semantic()
        for changes in ({"expected_relationship_sha256": "wrong"}, {"expected_snapshot_sha256": "wrong"}):
            with self.subTest(changes=changes), self.assertRaises(HarnessError):
                self.graph.record_review(candidate["relationship_id"], {"decision": "accept"}, reviewer="model", **changes)

    def test_review_ledger_is_append_only_and_chained(self):
        self.build()
        candidate = self.semantic()
        first = self.graph.record_review(candidate["relationship_id"], {"decision": "uncertain"}, reviewer="model-one")
        second = self.graph.record_review(candidate["relationship_id"], {"decision": "reject"}, reviewer="model-two")
        self.assertEqual(len(self.graph.reviews_for(candidate["relationship_id"])), 2)
        self.assertIsNotNone(second["previous_review_sha256"])
        self.assertNotEqual(first["review_id"], second["review_id"])
        with self.assertRaises(sqlite3.IntegrityError):
            self.graph.connection.execute("DELETE FROM reviews")

    def test_builder_refuses_to_overwrite_graph_review_history(self):
        self.build()
        original = self.graph_path.read_bytes()
        with self.assertRaises(HarnessError):
            build_graph(self.corpus, self.doc_path, self.section_path, self.graph_path)
        self.assertEqual(self.graph_path.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
