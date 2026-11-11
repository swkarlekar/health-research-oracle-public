"""Expansion tests exercise canonical identities, table context and graph direction."""
import copy

from support import Fixture, FixtureGraph, SCOPE, ScriptedModel, answer
from unittest.mock import patch
from oracle.context import ExpandedCorpus
from oracle.corpus import HarnessError, base
from oracle.harness import Harness




class ContextTests(Fixture):
    def wrapper(self, **limits):
        graph = FixtureGraph(self.corpus)
        return ExpandedCorpus(self.corpus, graph, **limits)

    def test_short_document_is_added_and_canonical_quote_roundtrips(self):
        corpus = self.wrapper()
        packet = corpus.packet("Synthetic alpha", SCOPE, 1)
        context = next(e for e in packet["evidence"] if e["chunk_id"].startswith("ctx:"))
        self.assertEqual(context["text"], corpus.graph.document(context["document_id"])["text"])
        value = answer(packet)
        value["findings"][0]["evidence_ids"] = [context["evidence_id"]]
        value["quotes"] = [{"chunk_id": context["chunk_id"], "start": 0, "end": len(context["text"])}]
        result = base.render_response(packet, value, corpus)
        self.assertEqual(result["quotes"][0]["text"], context["text"])
        self.assertEqual(result["quotes"][0]["document_start_char"], 0)

    def test_modified_expanded_text_rejected_even_with_new_packet_hash(self):
        corpus = self.wrapper()
        packet = corpus.packet("Synthetic alpha", SCOPE, 1)
        context = next(e for e in packet["evidence"] if e["chunk_id"].startswith("ctx:"))
        context["text"] += "tampered"
        context["text_sha256"] = base.digest(context["text"])
        packet["packet_id"] = base.packet_hash(packet)
        with self.assertRaises(HarnessError):
            base.render_response(packet, answer(packet), corpus)

    def test_context_hash_and_span_cannot_be_fabricated(self):
        corpus = self.wrapper()
        with self.assertRaises(HarnessError):
            corpus.chunk("ctx:doc0:0:10:made-up")

    def test_large_section_neighbor_window_is_canonical_bounded_and_labeled_incomplete(self):
        import json
        import sqlite3
        corpus = self.wrapper(short_document_chars=10, max_unit_chars=20, max_total_chars=100)
        original = self.corpus.chunk('chunk0')
        text = original['text']
        with sqlite3.connect(self.db) as con:
            con.execute("DELETE FROM chunks WHERE chunk_id='chunk0'")
            con.execute("DELETE FROM chunks_fts WHERE chunk_id='chunk0'")
            for index, (start, end) in enumerate(((0, 10), (10, 20), (20, len(text)))):
                key = f'window{index}'
                item = {**original, 'chunk_id': key, 'document_start_char': start,
                        'document_end_char': end, 'text_sha256': base.digest(text[start:end])}
                con.execute('INSERT INTO chunks VALUES(?,?,?)', (key, 'doc0', json.dumps(item)))
                con.execute('INSERT INTO chunks_fts VALUES(?,?,?,?)', (key, 'Synthetic alpha', '', text[start:end]))
        seed = self.corpus.chunk('window1')
        context = corpus._context_for('doc0', seed=seed)
        self.assertEqual(context['text'], text[:20])
        self.assertEqual(context['context_kind'], 'bounded_neighbor_context')
        self.assertLessEqual(len(context['text']), corpus.max_unit_chars)
        with patch.object(self.corpus, 'search', return_value=[seed]):
            packet = corpus.packet('Synthetic alpha', SCOPE, 1)
        corpus.validate_context(packet)
        self.assertTrue(any(o.get('reason') == 'incomplete_large_section' for o in packet['context_expansion']['omissions']))

    def test_graph_document_rehash_cannot_forge_canonical_source(self):
        corpus = self.wrapper()
        doc = corpus.graph.documents["doc0"]
        doc["text"] += " FORGED CANONICAL CONTEXT"
        doc["text_sha256"] = base.digest(doc["text"])
        with self.assertRaises(HarnessError):
            corpus.packet("Synthetic alpha", SCOPE, 1)

    def test_incoming_amendment_is_loaded_without_declaring_supersession(self):
        corpus = self.wrapper()
        # Force a single initial source, then link an amendment pointing TO it.
        with patch.object(self.corpus, "search", return_value=[self.corpus.chunk("chunk0")]):
            corpus.graph.edges = [{"relationship_id": "relation1", "source_document_id": "doc1", "target_document_id": "doc0",
                "relation_type": "clarifies", "status": "candidate", "target_reference": "Synthetic alpha",
                "qualifiers": {}, "evidence": [{"section_id": "section1", "start": 0, "end": 20,
                                                    "text_sha256": corpus.graph.sections["section1"]["text_sha256"]}]}]
            packet = corpus.packet("Synthetic alpha", SCOPE, 1)
        self.assertIn("doc1", {e["document_id"] for e in packet["evidence"]})
        result = Harness(corpus, ScriptedModel()).finish(packet, answer(packet), 1, 2,
                {"metadata_proxy_evidence_ids": [], "checks": {}, "issues": []})
        self.assertEqual(result["status"], "partial")
        self.assertIn("context_and_relationship_limits", [u["kind"] for u in result["uncertainties"]])
        tampered = copy.deepcopy(packet)
        tampered["context_expansion"]["relationships"][0]["relation_type"] = "supersedes"
        tampered["packet_id"] = base.packet_hash(tampered)
        with self.assertRaises(HarnessError):
            corpus.model_payload(tampered)

    def test_budget_omission_is_reported_without_truncation(self):
        corpus = self.wrapper(short_document_chars=20, max_unit_chars=40, max_total_chars=40)
        packet = corpus.packet("Synthetic alpha", SCOPE, 1)
        self.assertTrue(packet["context_expansion"]["omissions"])
        self.assertEqual(packet["evidence"][0]["text"], self.corpus.chunk(packet["evidence"][0]["chunk_id"])["text"])

    def test_compact_model_omissions_preserve_warning_counts_and_full_debug_records(self):
        corpus = self.wrapper(short_document_chars=20, max_unit_chars=40, max_total_chars=40)
        packet = corpus.packet('Synthetic alpha', SCOPE, 1)
        packet['context_expansion']['omissions'].append(copy.deepcopy(packet['context_expansion']['omissions'][0]))
        packet['packet_id'] = base.packet_hash(packet)
        original = copy.deepcopy(packet)
        compact = corpus.model_payload(packet)['context_expansion']['omissions']
        self.assertEqual(sum(item['occurrences'] for item in compact), len(original['context_expansion']['omissions']))
        self.assertLess(len(compact), len(original['context_expansion']['omissions']))
        self.assertEqual(packet, original)

    def test_disallowed_related_document_cannot_enter_evidence(self):
        corpus = self.wrapper()
        self.corpus.policy["doc1"]["retrieval_allowed"] = False
        with self.assertRaises(HarnessError):
            corpus._context_for("doc1")

    def test_quote_page_comes_from_selected_passage_not_parent_start(self):
        corpus = self.wrapper()
        graph = corpus.graph
        original = graph.sections.pop('section0')
        text = original['text']
        for key, start, end, page in [('left', 0, 11, 2), ('right', 11, len(text), 3)]:
            graph.sections[key] = {**original, 'section_id': key, 'text': text[start:end],
                'document_start_char': start, 'document_end_char': end, 'page': page}
        context = corpus._context_for('doc0')
        locator = corpus.quotation_locator(context, 13, len(text))
        self.assertEqual(locator['page'], 3)
        self.assertEqual(locator['page_end'], 3)
        self.assertIn(f'document_chars=13-{len(text)}', locator['source_locator'])

    def test_graph_change_makes_prepared_packet_stale(self):
        corpus = self.wrapper()
        packet = corpus.packet("Synthetic alpha", SCOPE, 1)
        with patch.object(corpus.graph, "summary", return_value={
                "snapshot_sha256": self.corpus.summary()["snapshot_sha256"],
                "graph_content_sha256": "changed-review-ledger"}):
            with self.assertRaises(HarnessError):
                base.render_response(packet, answer(packet), corpus)
