"""Reuse the corpus release's canonical quotation and citation implementation."""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("corpus_research", ROOT / "text_corpus/scripts/research.py")
base = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(base)
HarnessError = base.HarnessError


class ApprovedCorpus(base.Corpus):
    def __init__(self, db: Path, registry: Path):
        super().__init__(db)
        self.registry = json.loads(registry.read_text())
        self.temporal_policy = base.load_json(ROOT / "config/temporal-policy.json")
        self.policy = {s["document_id"]: s for s in self.registry["sources"]}
        if len(self.policy) != len(self.registry["sources"]):
            raise HarnessError("Duplicate source registry identity")
        self._summary = super().summary()
        if self.registry["snapshot_sha256"] != self._summary["snapshot_sha256"]:
            raise HarnessError("Source registry is stale; rebuild and review it for this corpus snapshot")

    def summary(self):
        return self._summary

    def allowed(self, item):
        policy = self.policy.get(item["document_id"], {})
        return (policy.get("retrieval_allowed") is True
                and item["source_role"] != "collection_index"
                and item["source_url"] == policy.get("source_url")
                and item["raw_sha256"] == policy.get("raw_sha256"))

    def search(self, query, limit=4, include_indexes=False):
        if not 1 <= limit <= 12:
            raise HarnessError("Oracle retrieval limit must be between 1 and 12")
        # Short focused searches retain the original baseline. Long scenarios use
        # the actual questions, rather than letting narrative demographics consume
        # the baseline's first-40-token budget. This is still lexical, not Titan.
        intent = re.split(r"\b(?:under\s+)?(?:what|which)\b", query, maxsplit=1, flags=re.I)
        if len(query) < 120 or len(intent) == 1:
            self.last_search = {"strategy": "lexical_baseline", "query": query}
            return [item for item in super().search(query, 30, False) if self.allowed(item)][:limit]
        clauses = re.split(r",?\s+(?:and\s+)?(?:what|which)\s+", intent[-1], flags=re.I)[:4]
        stop = set(base.STOP_WORDS) | set("circumstances can member have required rights does under govern apply rules regulations requirements regarding when existing which what new york medicaid plan mltc harp".split())
        expressions = []
        for clause in clauses:
            # Prefix matching joins such forms as disenroll/disenrollment. Tokens
            # are letters only and are bound SQL parameters, never FTS syntax.
            terms = list(dict.fromkeys(t[:7] for t in re.findall(r"[a-z]{3,}", clause.lower()) if t not in stop))[:24]
            if terms:
                expressions.append(" OR ".join('"' + term + '"*' for term in terms))
        if not expressions:
            return []
        plan = next((token for token in ("mltc", "harp", "cdpap") if re.search(r"\b" + token + r"\b", query, re.I)), None)
        ranked = []
        for expression in expressions:
            rows = self.connection.execute(
                "SELECT f.text,c.metadata_json AS chunk_meta,d.metadata_json AS doc_meta, "
                "bm25(chunks_fts,0,0.1,0.2,1) AS score FROM chunks_fts f "
                "JOIN chunks c ON c.chunk_id=f.chunk_id JOIN documents d ON d.document_id=c.document_id "
                "WHERE chunks_fts MATCH ? ORDER BY score LIMIT 100", (expression,)).fetchall()
            found = [self._snapshot(row) for row in rows]
            found = [item for item in found if self.allowed(item)]
            found.sort(key=lambda item: item['retrieval_score'] *
                       (1.8 if plan and re.search(r"\b" + plan + r"\b", item.get('title') or '', re.I) else 1))
            ranked.append(found)
        selected, seen, pages = [], set(), set()
        # Round-robin gives separate requested issues a chance to contribute.
        while any(ranked) and len(selected) < limit:
            for queue in ranked:
                while queue:
                    item = queue.pop(0)
                    identity = (item['document_id'], item['text_sha256'])
                    page = (item['document_id'], item.get('page'))
                    if identity in seen or (page[1] is not None and page in pages):
                        continue
                    selected.append(item)
                    seen.add(identity)
                    pages.add(page)
                    break
                if len(selected) == limit:
                    break
        self.last_search = {"strategy": "lexical_question_clauses", "query": query,
                            "clause_expressions": expressions, "plan_title_boost": plan,
                            "semantic_retrieval": False}
        return selected

    def chunk(self, chunk_id):
        item = super().chunk(chunk_id)
        if not self.allowed(item):
            raise HarnessError("Source is outside the selected source registry")
        return item

    def packet(self, question, scope, limit=4):
        packet = base.make_packet(self, argparse.Namespace(
            query=question, limit=limit, include_indexes=False, include_chunk=[],
            program=scope.get("program"), plan_type=scope.get("plan_type"),
            research_date=scope.get("research_date"), resolutions=ROOT / "artifacts/resolutions.jsonl"))
        if hasattr(self, 'last_search'):
            packet['retrieval']['search_plan'] = self.last_search
            packet['packet_id'] = base.packet_hash(packet)
        return packet

    def packet_from_chunks(self, question, scope, chunk_ids, retrieval):
        """An evidence packet for chunks chosen by the research pipeline; same contract as packet()."""
        from datetime import datetime, timezone
        if not isinstance(question, str) or not question.strip() or len(question) > 1600:
            raise HarnessError("Research question must contain 1 to 1600 characters")
        evidence = []
        for index, chunk_id in enumerate(dict.fromkeys(chunk_ids), 1):
            item = self.chunk(chunk_id)
            evidence.append(dict(item, evidence_id=f"E{index}", quote_candidates=base.quote_candidates(item["text"])))
        full_scope = {"question": question, "program": scope.get("program"), "plan_type": scope.get("plan_type"),
                      "research_date": scope.get("research_date"), "assumptions": []}
        packet = {"schema_version": base.SCHEMA_VERSION, "created_at": datetime.now(timezone.utc).isoformat(),
                  "scope": full_scope, "corpus": self.summary(),
                  "retrieval": {**retrieval, "evidence_count": len(evidence)},
                  "evidence": evidence,
                  "human_resolutions": base.read_resolutions(ROOT / "artifacts/resolutions.jsonl", full_scope, evidence),
                  "system_prompt": (base.CONFIG / "document_only_system.txt").read_text(encoding="utf-8"),
                  "response_schema": base.load_json(base.CONFIG / "response.schema.json")}
        packet["packet_id"] = base.packet_hash(packet)
        return packet

    def expand(self, packet, query, limit=4):
        found = self.search(query, limit)
        ids = {e["chunk_id"] for e in packet["evidence"]}
        for item in found:
            if item["chunk_id"] not in ids:
                packet["evidence"].append(dict(item, evidence_id=f"E{len(packet['evidence']) + 1}",
                                             quote_candidates=base.quote_candidates(item["text"])))
                ids.add(item["chunk_id"])
        packet["retrieval"]["evidence_count"] = len(packet["evidence"])
        packet["retrieval"]["additional_query"] = query
        packet["retrieval"]["additional_search_plan"] = getattr(self, 'last_search', None)
        packet["packet_id"] = base.packet_hash(packet)
        return packet

    def model_payload(self, packet):
        # Document-level date/relationship contexts supplement the retrieved excerpts.
        # They are search leads, never sufficient citation support without a retrieved span.
        documents = []
        signal_budget = 8000
        document_fields = ("title", "source_url", "source_role", "source_review_status", "customer_selection_explicitly_recorded",
                           "target_resolution_status", "version_resolution_status", "legal_status", "date_signals", "uncertainty_flags")
        for doc_id in sorted({e["document_id"] for e in packet["evidence"]}):
            row = self.connection.execute("SELECT metadata_json FROM documents WHERE document_id=?", (doc_id,)).fetchone()
            doc = json.loads(row[0])
            dates = doc.get("date_signals") or {}
            leads, remaining = [], min(4000, signal_budget)
            signals = dates.get("text_mentions", []) + (doc.get("relationship_signals") or [])
            for signal in signals:
                size = len(base.canonical(signal))
                if size <= remaining and len(leads) < 16:
                    leads.append(signal)
                    remaining -= size
                    signal_budget -= size
            source = next(e for e in packet['evidence'] if e['document_id'] == doc_id)
            documents.append({"document_id": doc_id,
                              "source_metadata": {key: source.get(key) for key in document_fields},
                              "file_date_signals": {key: value for key, value in dates.items() if key != "text_mentions"},
                              "date_and_relationship_leads": leads,
                              "omitted_signal_count": len(signals) - len(leads),
                              "qualification": "Search leads only. Retrieve the exact passage before citing a relationship."})
        evidence = []
        shared_metadata = {doc['document_id']: doc['source_metadata'] for doc in documents}
        # Keep every source character and quote offset. Bounding-box/cell geometry
        # stays in the canonical packet for inspection, not repeated in LLM input.
        fields = ("evidence_id", "chunk_id", "document_id", "section_id", "text", "text_sha256",
                  "document_start_char", "document_end_char",
                  "quote_candidates", "page", "context_pages", "context_kind", "heading_path")
        for item in packet["evidence"]:
            # Document-level provenance is supplied once above, inherited by every
            # excerpt with that document_id. Keep all source text and local locators.
            view = {key: item[key] for key in fields if key in item}
            overrides = {key: item.get(key) for key in document_fields
                         if item.get(key) != shared_metadata[item['document_id']][key]}
            if overrides:
                view['source_metadata_overrides'] = overrides
            view["table_headers"] = [{key: table[key] for key in
                ("headers", "column_count", "header_binding_policy", "header_detection", "source_locator") if key in table}
                for table in (item.get("table_context") or [])]
            evidence.append(view)
        return {"scope": packet["scope"], "evidence": evidence,
                "document_signals": documents,
                "source_metadata_policy": "Each evidence excerpt inherits recorded source_metadata from the document_signals entry with its document_id; excerpt source_metadata_overrides take precedence. Metadata does not establish legal applicability or currency.",
                "temporal_policy": self.temporal_policy,
                "last_modified_proxy_candidates": self.proxy_candidates(packet),
                "coverage": {"document_count": packet["corpus"]["document_count"],
                             "unavailable_sources": [{key: item.get(key) for key in ('document_id', 'title', 'processing_status')}
                                                     for item in packet["corpus"]["unavailable_sources"]],
                             "collection_caveat": packet["corpus"]["collection_caveat"]}}

    def proxy_candidates(self, packet):
        candidates = {}
        for item in packet["evidence"]:
            dates = item["date_signals"]
            field = None
            if self.temporal_policy["document_last_modified_proxy_allowed"] and dates.get("pdf_modification_date_raw"):
                field = "pdf_modification_date_raw"
            elif self.temporal_policy["http_last_modified_proxy_allowed"] and dates.get("http_last_modified_raw"):
                field = "http_last_modified_raw"
            if field:
                candidates[item["evidence_id"]] = {"metadata_field": field, "raw_value": dates[field],
                                                   "qualification": "Secondary date proxy only; not an established effective date or supersession decision."}
        return candidates


def build_registry(db: Path):
    corpus = base.Corpus(db)
    try:
        sources = []
        for row in corpus.connection.execute("SELECT metadata_json FROM documents ORDER BY document_id"):
            doc = json.loads(row[0])
            provenance = base.recorded_provenance(doc)
            role = doc.get("source_role") or (doc.get("acquisition_metadata") or {}).get("source_role")
            sources.append({"document_id": doc["document_id"], "source_url": doc.get("source_url"),
                            "raw_sha256": doc.get("raw_sha256"),
                            "retrieval_allowed": (provenance["customer_selection_explicitly_recorded"]
                                                  and doc.get("processing_status") == "processed"
                                                  and role != "collection_index"),
                            "version_status": provenance["version_resolution_status"]})
        return {"schema_version": "1.0", "snapshot_sha256": corpus.summary()["snapshot_sha256"],
                "approval_basis": "User selected the current customer-PDF corpus. Selection is not authority, applicability, or currency verification.",
                "sources": sources}
    finally:
        corpus.close()
