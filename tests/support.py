"""Shared synthetic corpus/provider fixtures; never a legal-answer benchmark."""
import copy
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from oracle.corpus import ApprovedCorpus, base, build_registry
from oracle.aws import ApprovalGate


SCOPE = {"program": "Synthetic program alpha", "plan_type": "Synthetic plan alpha", "research_date": "2026-01-01"}


def answer(payload):
    item = payload["evidence"][0]
    return {"status": "supported", "scope": copy.deepcopy(payload["scope"]),
            "findings": [{"text": "The synthetic source contains an alpha rule.", "evidence_ids": [item["evidence_id"]]}],
            "quotes": ([item['passages'][0]['quote_id']] if 'passages' in item else
                       [{"chunk_id": item["chunk_id"], "start": 0, "end": len(item["text"])}]),
            "uncertainties": [], "clarifying_questions": []}


class ScriptedModel:
    def __init__(self, search=False, conflict=False, bad_support=False):
        self.calls = 0
        self.payloads = []
        self.search, self.conflict, self.bad_support = search, conflict, bad_support

    def complete(self, system, payload, schema):
        self.calls += 1
        self.payloads.append(copy.deepcopy(payload))
        response = answer(payload)
        if self.calls == 1:
            return response
        if self.conflict:
            response["status"] = "conflicting_sources"
            response["uncertainties"] = [{"kind": "version_conflict", "description": "The synthetic sources do not establish which version applies.",
                                           "evidence_ids": ["E1"], "question_for_user": None}]
        return {"response": response,
                "checks": {"support": not self.bad_support, "applicability": True, "responsiveness": True,
                           "dates_and_versions": True, "conflicts_and_exceptions": True, "boundary_safe": True},
                "additional_query": "synthetic alpha addendum" if self.search else None,
                "metadata_proxy_evidence_ids": [],
                "issues": []}


class RoutedModel:
    def __init__(self, case=None, research=None):
        self.case = case or {'classification': {'requests': [{'category': 'public_rules', 'request_text': 'Synthetic public research'}], 'missing_attributes': []}, 'public_check': {'explicit_public_research': True, 'request_text': 'Synthetic public research'}}
        self.research = research or ScriptedModel()
        self.calls = 0
        self.model = 'synthetic-replay'
        self.usage = []

    def complete(self, system, payload, schema):
        from oracle.request_routing import ROUTER_POLICY, PUBLIC_POLICY
        self.calls += 1
        if system == ROUTER_POLICY:
            assert set(payload) == {'question'}
            return copy.deepcopy(self.case['classification'])
        if system == PUBLIC_POLICY:
            assert set(payload) == {'question'}
            return copy.deepcopy(self.case['public_check'])
        return self.research.complete(system, payload, schema)


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = self.root / "corpus.sqlite"
        con = sqlite3.connect(self.db)
        con.executescript("CREATE TABLE documents(document_id TEXT PRIMARY KEY, metadata_json TEXT);"
                          "CREATE TABLE chunks(chunk_id TEXT PRIMARY KEY, document_id TEXT, metadata_json TEXT);"
                          "CREATE VIRTUAL TABLE chunks_fts USING fts5(chunk_id UNINDEXED,title,heading_path,text);")
        for i, text in enumerate(["Synthetic alpha rule. Café 🏥.", "Synthetic alpha addendum. Applicability remains unresolved."]):
            doc_id, chunk_id = f"doc{i}", f"chunk{i}"
            doc = {"document_id": doc_id, "source_url": f"https://example.org/synthetic/{i}",
                   "source_origin": "customer_pdf_link", "raw_sha256": base.digest(text),
                   "text_sha256": base.digest(text), "char_count": len(text),
                   "source_role": "customer_selected_document", "processing_status": "processed",
                   "acquisition_status": "downloaded",
                   "acquisition_metadata": {"customer_inventory_status": "explicitly_listed"},
                   "date_signals": {"pdf_modification_date_raw": "D:20260102030405Z"}}
            chunk = {"chunk_id": chunk_id, "document_id": doc_id, "text_sha256": base.digest(text),
                     "raw_sha256": doc["raw_sha256"], "source_url": doc["source_url"],
                     "title": "Synthetic alpha", "document_start_char": 0, "document_end_char": len(text)}
            con.execute("INSERT INTO documents VALUES(?,?)", (doc_id, json.dumps(doc)))
            con.execute("INSERT INTO chunks VALUES(?,?,?)", (chunk_id, doc_id, json.dumps(chunk)))
            con.execute("INSERT INTO chunks_fts VALUES(?,?,?,?)", (chunk_id, "Synthetic alpha", "", text))
        con.commit()
        con.close()
        self.registry = self.root / "sources.json"
        self.registry.write_text(json.dumps(build_registry(self.db)))
        self.corpus = ApprovedCorpus(self.db, self.registry)
        self.addCleanup(self.corpus.close)

    def grant(self, action="bedrock:Converse", resource=None, **changes):
        value = {"user_approved": True, "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
                 "action": action, "region": "us-east-1", "resource": resource or {
                     "model_id": "synthetic-model", "max_output_tokens": 2400, "max_prompt_chars": 110000},
                 "max_calls": 3, "used_calls": 0}
        value.update(changes)
        path = self.root / "grant.json"
        path.write_text(json.dumps(value))
        return ApprovalGate(path)


class FixtureGraph:
    def __init__(self, corpus):
        self.corpus = corpus
        self.documents = {}
        self.sections = {}
        self.edges = []
        self.closed = False
        for i in range(2):
            item = corpus.chunk(f"chunk{i}")
            doc = json.loads(corpus.connection.execute("SELECT metadata_json FROM documents WHERE document_id=?", (f"doc{i}",)).fetchone()[0])
            doc["text"] = item["text"]
            self.documents[f"doc{i}"] = doc
            self.sections[f"section{i}"] = {"section_id": f"section{i}", "document_id": f"doc{i}",
                "text": item["text"], "text_sha256": base.digest(item["text"]),
                "document_start_char": 0, "document_end_char": len(item["text"]),
                "page": 1, "tables": [], "heading_path": ["Synthetic alpha"]}

    def summary(self):
        return {"snapshot_sha256": self.corpus.summary()["snapshot_sha256"], "relationship_count": len(self.edges),
                "graph_content_sha256": base.digest(base.canonical(self.edges))}

    def document(self, document_id):
        return self.documents[document_id]

    def section(self, section_id):
        return self.sections[section_id]

    def sections_for_document(self, document_id):
        return [s for s in self.sections.values() if s["document_id"] == document_id]

    def relationships_for(self, document_id, include_candidates=True):
        return [edge for edge in self.edges if document_id in {edge["source_document_id"], edge["target_document_id"]}]

    def validate_relationship(self, record):
        return True

    def close(self):
        self.closed = True

