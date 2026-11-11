"""Canonical context expansion over local text, independent of retrieval provider.

Expanded evidence is a deterministic span of the pinned document text. Its ID
encodes document/start/end and is re-resolved during quotation validation. The
original corpus is read-only; no synthetic text is written into its chunk index.
"""
from __future__ import annotations

import json
import copy

from .corpus import HarnessError, base


class ExpandedCorpus:
    def __init__(self, retriever, graph, *, short_document_chars=14000,
                 max_unit_chars=16000, max_total_chars=42000,
                 max_related_documents=3, max_relationships=20, max_evidence=20):
        if not 1 <= short_document_chars <= max_unit_chars <= max_total_chars <= 80000:
            raise HarnessError("Context limits must be positive, ordered, and at most 80000 characters")
        if not 0 <= max_related_documents <= 6 or not 1 <= max_relationships <= 40 or not 1 <= max_evidence <= 24:
            raise HarnessError("Graph expansion limits are outside the supported range")
        self.retriever, self.graph = retriever, graph
        self.short_document_chars = short_document_chars
        self.max_unit_chars, self.max_total_chars = max_unit_chars, max_total_chars
        self.max_related_documents, self.max_relationships = max_related_documents, max_relationships
        self.max_evidence = max_evidence
        self._closed = False
        self._relationship_cache = {}
        if graph.summary()["snapshot_sha256"] != retriever.summary()["snapshot_sha256"]:
            raise HarnessError("Relationship graph and retrieval corpus snapshots differ")

    def __getattr__(self, name):
        return getattr(self.retriever, name)

    def summary(self):
        graph = self.graph.summary()
        return {**self.retriever.summary(), "context_graph": {
            "snapshot_sha256": graph["snapshot_sha256"],
            "graph_content_sha256": graph.get("graph_content_sha256"),
            "limits": {"short_document_chars": self.short_document_chars,
                       "max_unit_chars": self.max_unit_chars, "max_total_chars": self.max_total_chars,
                       "max_related_documents": self.max_related_documents,
                       "max_relationships": self.max_relationships, "max_evidence": self.max_evidence}}}

    def close(self):
        if not self._closed:
            self.graph.close()
            self.retriever.close()
            self._closed = True

    def _document(self, document_id):
        doc = self.graph.document(document_id)
        policy = self.retriever.policy.get(document_id, {})
        if not policy.get("retrieval_allowed") or doc.get("source_url") != policy.get("source_url") or doc.get("raw_sha256") != policy.get("raw_sha256"):
            raise HarnessError("Expanded context is outside the selected canonical source registry")
        if not isinstance(doc.get("text"), str):
            raise HarnessError("Canonical document text is unavailable for expansion")
        row = self.retriever.connection.execute(
            "SELECT metadata_json FROM documents WHERE document_id=?", (document_id,)).fetchone()
        if row is None:
            raise HarnessError("Expanded document is not in the pinned retrieval corpus")
        original = json.loads(row[0])
        expected = original.get("text_sha256")
        if not expected or base.digest(doc["text"]) != expected:
            raise HarnessError("Expanded document text differs from the original canonical corpus hash")
        return doc

    def _graph_edges(self, document_id):
        # Repeated quote/review validation must not rescan the entire graph per
        # relationship. The audited graph digest invalidates this small cache.
        version = self.graph.summary().get("graph_content_sha256")
        key = (version, document_id)
        if version is None:
            return self.graph.relationships_for(document_id, include_candidates=True)
        if key not in self._relationship_cache:
            self._relationship_cache = {k: value for k, value in self._relationship_cache.items() if k[0] == version}
            self._relationship_cache[key] = self.graph.relationships_for(document_id, include_candidates=True)
        return copy.deepcopy(self._relationship_cache[key])

    def _canonical_seed(self, document_id, start):
        row = self.retriever.connection.execute(
            "SELECT chunk_id FROM chunks WHERE document_id=? "
            "AND json_extract(metadata_json,'$.document_start_char')<=? "
            "AND json_extract(metadata_json,'$.document_end_char')>? "
            "ORDER BY json_extract(metadata_json,'$.document_start_char') DESC LIMIT 1", (document_id, start, start)).fetchone()
        if row is None:
            row = self.retriever.connection.execute(
                "SELECT chunk_id FROM chunks WHERE document_id=? ORDER BY chunk_id LIMIT 1", (document_id,)).fetchone()
        if row is None:
            raise HarnessError("No canonical evidentiary chunk exists for expanded document")
        return self.retriever.chunk(row[0])

    def chunk(self, chunk_id):
        if not isinstance(chunk_id, str) or not chunk_id.startswith("ctx:"):
            item = self.retriever.chunk(chunk_id)
            doc = self._document(item["document_id"])
            if doc["text"][item["document_start_char"]:item["document_end_char"]] != item["text"]:
                raise HarnessError("Retrieved chunk differs from the canonical document span")
            return item
        parts = chunk_id.split(":")
        if len(parts) != 5 or not parts[2].isdigit() or not parts[3].isdigit():
            raise HarnessError("Malformed canonical context identity")
        _, document_id, raw_start, raw_end, text_hash = parts
        start, end = int(raw_start), int(raw_end)
        doc = self._document(document_id)
        text = doc["text"]
        if not 0 <= start < end <= len(text) or end - start > self.max_unit_chars:
            raise HarnessError("Expanded context span exceeds canonical text or limits")
        excerpt = text[start:end]
        if base.digest(excerpt) != text_hash:
            raise HarnessError("Expanded context no longer matches its canonical hash")
        seed = dict(self._canonical_seed(document_id, start))
        seed.pop("retrieval_score", None)
        sections = [s for s in self.graph.sections_for_document(document_id)
                    if s["document_start_char"] < end and s["document_end_char"] > start]
        last = self._canonical_seed(document_id, end - 1)
        pages = sorted({p for p in (seed.get("page"), last.get("page")) if p is not None})
        section_ids = [s["section_id"] for s in sections]
        # The source locator describes the span; page identifies its starting page.
        partial_section = any(s['document_start_char'] < start or s['document_end_char'] > end for s in sections)
        seed.update({"chunk_id": chunk_id, "section_id": section_ids[0] if len(section_ids) == 1 else None,
                     "text": excerpt, "text_sha256": text_hash,
                     "document_start_char": start, "document_end_char": end,
                     "section_start_char": start - sections[0]["document_start_char"] if len(sections) == 1 else None,
                     "section_end_char": end - sections[0]["document_start_char"] if len(sections) == 1 else None,
                     "page": pages[0] if pages else None,
                     "source_locator": (f"pages={pages[0]}-{pages[-1]}&" if pages else "") + f"document_chars={start}-{end}",
                     "context_kind": ("whole_document" if start == 0 and end == len(text) else
                                      "bounded_neighbor_context" if partial_section else "section_or_page_context"),
                     "context_section_ids": section_ids, "context_pages": pages,
                     "table_context": [table for section in sections for table in section.get("tables", [])],
                     "heading_path": sections[0].get("heading_path", []) if sections else []})
        return seed

    def _context_for(self, document_id, *, seed=None, section_id=None):
        doc = self._document(document_id)
        text = doc["text"]
        if not text.strip():
            return None
        if len(text) <= self.short_document_chars:
            start, end = 0, len(text)
        else:
            sections = self.graph.sections_for_document(document_id)
            if section_id:
                anchors = [s for s in sections if s["section_id"] == section_id]
            elif seed:
                anchors = [s for s in sections if s["document_start_char"] < seed["document_end_char"]
                           and s["document_end_char"] > seed["document_start_char"]]
            else:
                anchors = []
            if not anchors:
                return None
            start = min(s["document_start_char"] for s in anchors)
            end = max(s["document_end_char"] for s in anchors)
            # PDF page text and extracted tables may be separate canonical sections.
            pages = {s.get("page") for s in anchors if s.get("page") is not None}
            same_page = [s for s in sections if s.get("page") in pages] if pages else []
            if same_page:
                start = min(start, min(s["document_start_char"] for s in same_page))
                end = max(end, max(s["document_end_char"] for s in same_page))
            if end - start > self.max_unit_chars:
                # A large HTML section may contain many operative provisions.
                # Add only canonical neighboring chunks, explicitly incomplete;
                # never load the entire section or pretend this is its boundary.
                if not seed:
                    return None
                start, end = seed['document_start_char'], seed['document_end_char']
                rows = self.retriever.connection.execute(
                    "SELECT metadata_json FROM chunks WHERE document_id=? ORDER BY json_extract(metadata_json,'$.document_start_char')",
                    (document_id,)).fetchall()
                ranges = [(json.loads(row[0])['document_start_char'], json.loads(row[0])['document_end_char']) for row in rows]
                before = [r for r in ranges if r[0] < start]
                after = [r for r in ranges if r[1] > end]
                if before and end - before[-1][0] <= self.max_unit_chars:
                    start = before[-1][0]
                if after and after[0][1] - start <= self.max_unit_chars:
                    end = after[0][1]
                if end - start > self.max_unit_chars:
                    return None
                return self.chunk(f"ctx:{document_id}:{start}:{end}:{base.digest(text[start:end])}")
            # Expand to immediate neighbors only when they fit. Never cut a table.
            adjacent = sorted(sections, key=lambda s: s["document_start_char"])
            before = [s for s in adjacent if s["document_end_char"] <= start]
            after = [s for s in adjacent if s["document_start_char"] >= end]
            if before and end - before[-1]["document_start_char"] <= self.max_unit_chars:
                start = before[-1]["document_start_char"]
            if after and after[0]["document_end_char"] - start <= self.max_unit_chars:
                end = after[0]["document_end_char"]
        return self.chunk(f"ctx:{document_id}:{start}:{end}:{base.digest(text[start:end])}")

    def quotation_locator(self, item, start, end):
        """Locate the selected quote, rather than the beginning of its parent span."""
        left, right = item['document_start_char'] + start, item['document_start_char'] + end
        pages = sorted({s['page'] for s in self.graph.sections_for_document(item['document_id'])
                        if s.get('page') is not None and s['document_start_char'] < right
                        and s['document_end_char'] > left})
        return {'page': pages[0] if pages else item.get('page'),
                'page_end': pages[-1] if pages else item.get('page'),
                'source_locator': (f'pages={pages[0]}-{pages[-1]}&' if pages else '') + f'document_chars={left}-{right}'}

    def _expand_context(self, packet, *, reserve_chars=0):
        trace = packet.setdefault("context_expansion", {"version": "1.0", "seed_chunk_ids": [],
            "added_context_ids": [], "relationships": [], "omissions": [],
            "limits": {"max_total_chars": self.max_total_chars, "max_unit_chars": self.max_unit_chars,
                       "max_related_documents": self.max_related_documents, "max_hops": 1},
            "complete_document_or_graph_audit": False})
        for omission in packet['retrieval'].get('budget_omissions', []):
            if omission not in trace['omissions']:
                trace['omissions'].append(omission)
        seeds = [e for e in packet["evidence"] if not e["chunk_id"].startswith("ctx:")]
        trace["seed_chunk_ids"] = [e["chunk_id"] for e in seeds]
        total = sum(len(e["text"]) for e in packet["evidence"])
        existing = {e["chunk_id"] for e in packet["evidence"]}
        loaded_sections = set()

        def add(item, reason):
            nonlocal total
            if item is None:
                trace["omissions"].append({"reason": reason, "detail": "Complete context exceeds unit limit or is unavailable"})
                return False
            if item["chunk_id"] in existing:
                loaded_sections.update(item.get("context_section_ids", []))
                return True
            if total + len(item["text"]) > self.max_total_chars - reserve_chars or len(packet["evidence"]) >= self.max_evidence:
                trace["omissions"].append({"reason": reason, "document_id": item["document_id"], "detail": "Context budget reached; evidence was not truncated"})
                return False
            next_id = max(packet['retrieval'].get('max_assigned_evidence_id', 0),
                          max((int(e['evidence_id'][1:]) for e in packet['evidence']), default=0)) + 1
            packet['retrieval']['max_assigned_evidence_id'] = next_id
            packet["evidence"].append(dict(item, evidence_id=f"E{next_id}",
                                           quote_candidates=base.quote_candidates(item["text"])))
            if item.get('context_kind') == 'bounded_neighbor_context':
                trace['omissions'].append({'reason': 'incomplete_large_section', 'document_id': item['document_id'],
                    'detail': 'Only the exact retrieved chunk and bounded neighboring chunks are loaded; the containing section exceeds the context limit.'})
            existing.add(item["chunk_id"])
            total += len(item["text"])
            trace["added_context_ids"].append(item["chunk_id"])
            loaded_sections.update(item.get("context_section_ids", []))
            return True

        for seed in seeds:
            if seed.get("section_id"):
                loaded_sections.add(seed["section_id"])
            add(self._context_for(seed["document_id"], seed=seed), "retrieved_parent_context")

        seed_docs = {e["document_id"] for e in seeds}
        whole_documents = {e["document_id"] for e in packet["evidence"] if e.get("context_kind") == "whole_document"}
        edges = {}
        for doc_id in sorted(seed_docs):
            for edge in self._graph_edges(doc_id):
                # Follow outgoing references only from evidence already in context;
                # incoming amendments are considered for their original target.
                incoming = edge.get("target_document_id") == doc_id
                source_sections = {ref["section_id"] for ref in edge.get("evidence", [])}
                if incoming and edge.get("relation_type") == "references" and doc_id not in whole_documents:
                    qualifiers = edge.get("qualifiers") or {}
                    target_sections = set(qualifiers.get("target_section_ids") or ([qualifiers["target_section_id"]] if qualifiers.get("target_section_id") else []))
                    incoming = bool(target_sections & loaded_sections)
                if incoming or source_sections & loaded_sections:
                    edges[edge["relationship_id"]] = edge
        priority = {"supersedes": 0, "supersedes_in_part": 0, "amends": 1, "clarifies": 2, "corrects": 2, "conflicts_with": 3, "references": 4}
        edges = sorted(edges.values(), key=lambda e: (priority.get(e["relation_type"], 3), e["relationship_id"]))
        related_docs = set()
        trace["relationships"] = []
        for edge in edges[:self.max_relationships]:
            self.graph.validate_relationship(edge)
            source, target = edge["source_document_id"], edge.get("target_document_id")
            relationship_trace = {key: edge.get(key) for key in (
                "relationship_id", "source_document_id", "target_document_id", "relation_type", "status", "target_reference", "qualifiers")}
            relationship_trace["interpretation"] = "Documentary relationship lead; does not automatically establish applicability, currentness, or supersession."
            relationship_trace["evidence_chunk_ids"] = []
            for ref in edge.get("evidence", []):
                try:
                    section = self.graph.section(ref["section_id"])
                    asserting_doc = section["document_id"]
                    policy = self.retriever.policy.get(asserting_doc, {})
                    if not policy.get("retrieval_allowed"):
                        raise HarnessError("Relationship source has no selected evidentiary text")
                    if asserting_doc not in seed_docs and asserting_doc not in related_docs and len(related_docs) >= self.max_related_documents:
                        raise HarnessError("Related-document limit reached")
                    item = self._context_for(asserting_doc, section_id=section["section_id"])
                    if add(item, "relationship_support"):
                        if asserting_doc not in seed_docs:
                            related_docs.add(asserting_doc)
                        relationship_trace["evidence_chunk_ids"].append(item["chunk_id"])
                except HarnessError as exc:
                    trace["omissions"].append({"relationship_id": edge["relationship_id"], "detail": str(exc)})
            if target and target not in seed_docs and target not in related_docs:
                if len(related_docs) < self.max_related_documents and self.retriever.policy.get(target, {}).get("retrieval_allowed"):
                    doc = self._document(target)
                    # A vague reference to a large document is not a license to load
                    # its arbitrary first section; let the reviewer request a search.
                    if len(doc["text"]) <= self.short_document_chars:
                        item = self._context_for(target)
                        if add(item, "referenced_short_document"):
                            related_docs.add(target)
                    else:
                        qualifiers = edge.get("qualifiers") or {}
                        target_sections = qualifiers.get("target_section_ids") or ([qualifiers["target_section_id"]] if qualifiers.get("target_section_id") else [])
                        if target_sections:
                            for section_id in target_sections[:2]:
                                item = self._context_for(target, section_id=section_id)
                                if add(item, "referenced_exact_provision"):
                                    related_docs.add(target)
                        else:
                            trace["omissions"].append({"relationship_id": edge["relationship_id"], "document_id": target,
                                                      "detail": "Reference targets a long document without an exact provision; a focused retrieval is required"})
                else:
                    trace["omissions"].append({"relationship_id": edge["relationship_id"], "document_id": target,
                                              "detail": "Target is unavailable, excluded, or exceeds related-document limit"})
            if not target:
                trace["omissions"].append({"relationship_id": edge["relationship_id"], "detail": "Referenced source identity is unresolved or outside the acquired corpus"})
            trace["relationships"].append(relationship_trace)
        if len(edges) > self.max_relationships:
            trace["omissions"].append({"detail": "Relationship traversal limit reached", "omitted_relationship_count": len(edges) - self.max_relationships})
        trace["graph_snapshot"] = self.graph.summary()
        packet["corpus"] = self.summary()
        packet["retrieval"]["evidence_count"] = len(packet["evidence"])
        packet["packet_id"] = base.packet_hash(packet)
        return packet

    def packet(self, question, scope, limit=4, *, search_queries=None):
        packet = (self.retriever.packet(question, scope, limit, search_queries=search_queries) if search_queries
                  else self.retriever.packet(question, scope, limit))
        # Reserve room for a genuinely new focused search. Never cut a source span.
        reserve = min(4000, self.max_total_chars // 4)
        self._bound_new_evidence(packet, 0, self.max_total_chars - reserve)
        return self._expand_context(packet, reserve_chars=reserve)

    def _bound_new_evidence(self, packet, existing_count, maximum):
        retained = packet['evidence'][:existing_count]
        total = sum(len(item['text']) for item in retained)
        omitted = []
        for item in packet['evidence'][existing_count:]:
            if total + len(item['text']) <= maximum:
                retained.append(item)
                total += len(item['text'])
            else:
                omitted.append({'reason': 'retrieval_evidence_budget', 'document_id': item['document_id'],
                                'chunk_id': item['chunk_id'], 'detail': 'Whole canonical passage omitted to preserve the source-character budget'})
        packet['evidence'] = retained
        packet['retrieval'].setdefault('budget_omissions', []).extend(omitted)
        packet['retrieval']['evidence_count'] = len(retained)
        packet['packet_id'] = base.packet_hash(packet)

    def expand(self, packet, query, limit=4):
        self.validate_context(packet)
        existing_count = len(packet['evidence'])
        self.retriever.expand(packet, query, limit)
        self._bound_new_evidence(packet, existing_count, self.max_total_chars)
        return self._expand_context(packet)

    def focus(self, packet, evidence_ids, *, additional_search_available=True):
        """Spend context on selected source anchors; keep prior packets in trace.

        No source text or selected identity is rewritten. Expanded parent spans
        receive new monotonic IDs and still undergo canonical/graph validation.
        """
        self.validate_context(packet)
        selected = [e for e in packet['evidence'] if e['evidence_id'] in evidence_ids]
        if not selected:
            return packet
        related = {identity for edge in packet.get('context_expansion', {}).get('relationships', [])
                   for identity in edge.get('evidence_chunk_ids', [])}
        retained = []
        for item in packet['evidence']:
            child_of_selected = any(parent.get('context_kind') and parent['document_id'] == item['document_id']
                and parent['document_start_char'] <= item['document_start_char']
                and parent['document_end_char'] >= item['document_end_char'] for parent in selected)
            # Independent review must still see unselected retrieval candidates;
            # the draft's omission is not authority to hide them. Retain loaded
            # relationship qualifications too, under the same canonical budget.
            if not item.get('context_kind') or item in selected or child_of_selected or item['chunk_id'] in related:
                retained.append(item)
        packet['retrieval']['max_assigned_evidence_id'] = max(
            packet['retrieval'].get('max_assigned_evidence_id', 0),
            max(int(e['evidence_id'][1:]) for e in packet['evidence']))
        packet['retrieval'].setdefault('relevance_focus_omissions', []).extend(
            {'evidence_id': e['evidence_id'], 'chunk_id': e['chunk_id'],
             'reason': 'Unselected parent context reprioritized; raw retrieval and relationship hits retained, previous packet in debug trace'}
            for e in packet['evidence'] if e not in retained)
        packet['evidence'] = sorted(retained, key=lambda e: e['evidence_id'] not in evidence_ids)
        prior = packet.pop('context_expansion')
        packet['retrieval']['prior_context_omission_count'] = len(prior['omissions'])
        packet['packet_id'] = base.packet_hash(packet)
        reserve = min(4000, self.max_total_chars // 4) if additional_search_available else 0
        return self._expand_context(packet, reserve_chars=reserve)

    def validate_context(self, packet):
        """Bind relationship claims to the effective graph, not just a packet hash."""
        base.validate_packet(packet, self)
        trace = packet.get("context_expansion")
        if not isinstance(trace, dict) or trace.get("graph_snapshot") != self.graph.summary():
            raise HarnessError("Context trace belongs to a different relationship graph version")
        evidence = {e["chunk_id"]: e for e in packet["evidence"]}
        raw_ids = [e["chunk_id"] for e in packet["evidence"] if not e["chunk_id"].startswith("ctx:")]
        if trace.get("seed_chunk_ids") != raw_ids:
            raise HarnessError("Context trace changed its canonical retrieval seeds")
        checked = set()
        for claimed in trace.get("relationships", []):
            identity = claimed.get("relationship_id")
            if identity in checked:
                raise HarnessError("Context trace repeats a relationship identity")
            checked.add(identity)
            actual = next((edge for edge in self._graph_edges(claimed.get("source_document_id"))
                           if edge["relationship_id"] == identity), None)
            fields = ("relationship_id", "source_document_id", "target_document_id", "relation_type", "status", "target_reference", "qualifiers")
            if actual is None or any(claimed.get(key) != actual.get(key) for key in fields):
                raise HarnessError("Context relationship differs from its canonical reviewed graph record")
            for chunk_id in claimed.get("evidence_chunk_ids", []):
                item = evidence.get(chunk_id)
                if item is None:
                    raise HarnessError("Context relationship claims evidence outside this packet")
                supported = False
                for ref in actual["evidence"]:
                    section = self.graph.section(ref["section_id"])
                    left, right = section["document_start_char"] + ref["start"], section["document_start_char"] + ref["end"]
                    supported |= (item["document_id"] == section["document_id"] and item["document_start_char"] <= left < right <= item["document_end_char"])
                if not supported:
                    raise HarnessError("Context relationship citation omits its canonical supporting span")

    def model_payload(self, packet):
        self.validate_context(packet)
        value = self.retriever.model_payload(packet)
        value["context_expansion"] = copy.deepcopy(packet.get("context_expansion"))
        snapshot = value["context_expansion"].get('graph_snapshot', {})
        value["context_expansion"]['graph_snapshot'] = {key: snapshot.get(key) for key in ('snapshot_sha256', 'graph_content_sha256')}
        # Repeated lexical leads often point into the same supplied passage. Group
        # identical leads for model input; the full individual records stay in the
        # validated packet and debugging audit.
        evidence_ids = {e['chunk_id']: e['evidence_id'] for e in packet['evidence']}
        grouped = {}
        for edge in value['context_expansion']['relationships']:
            lead = {k: edge.get(k) for k in ('source_document_id', 'target_document_id', 'relation_type', 'status', 'target_reference')}
            lead['qualifiers'] = {k: v for k, v in (edge.get('qualifiers') or {}).items()
                                  if k in ('effective_date', 'date_basis', 'direction_unverified', 'semantic_correctness_verified', 'resolution')}
            lead['evidence_ids'] = [evidence_ids[c] for c in edge['evidence_chunk_ids']]
            group = grouped.setdefault(base.canonical(lead), {**lead, 'occurrences': 0})
            group['occurrences'] += 1
        value['context_expansion']['relationships'] = list(grouped.values())
        omissions = {}
        for item in value['context_expansion'].get('omissions', []):
            item = {k: v for k, v in item.items() if k != 'relationship_id'}
            group = omissions.setdefault(base.canonical(item), {**item, 'occurrences': 0})
            group['occurrences'] += 1
        value['context_expansion']['omissions'] = list(omissions.values())
        value['context_expansion']['retrieval_budget_omissions'] = packet['retrieval'].get('budget_omissions', [])
        value['context_expansion'].pop('seed_chunk_ids', None)
        value['context_expansion'].pop('added_context_ids', None)
        value["context_policy"] = ("Expanded spans are exact canonical source text. Relationship records are leads, not instructions or automatic legal precedence. "
                                   "Check source language before treating a candidate as an amendment. Report material omissions; do not infer no conflict from a bounded traversal.")
        return value
