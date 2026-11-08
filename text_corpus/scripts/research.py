#!/usr/bin/env python3
"""Document-only proof-of-concept retrieval and answer harness (standard library).

Examples, from the project folder:
  python scripts/research.py search "MLTC involuntary disenrollment"
  python scripts/research.py packet "MLTC involuntary disenrollment" --output packet.json
  python scripts/research.py estimate --packet packet.json
  python scripts/research.py capacity-estimate --gpu-hourly-rate 1.0 --active-hours-per-year 2000
  python scripts/research.py render --packet packet.json --response response.json
  python scripts/research.py infer --packet packet.json --model LOCAL_MODEL_ID --output result.json
  python scripts/research.py record-resolution --packet packet.json --kind version_relationship \
    --description "Which version should this research use?" --evidence-id E1 --evidence-id E2 \
    --human-decision "Use the scope stated in E2 for this question." --reviewer advocate_alias

Only 'infer' contacts a model, and only a loopback OpenAI-compatible endpoint.
There is no training, browsing, autonomous action, or client benchmark in this file.
Quotation offsets are zero-based Unicode code points in the exact chunk text.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import ipaddress
import json
import math
import os
import re
import sqlite3
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config"
SCHEMA_VERSION = "1.0"
STOP_WORDS = frozenset("a an the and or of to in on for from with is are was were be been being what which who whom whose how when where why can could may might do does did i we they it this that these those about please tell me under".split())
STATUSES = {"supported", "partial", "clarification_needed", "conflicting_sources", "currency_unverified", "not_found"}
INVENTORY_SCOPE_NOTE = "Customer source selection only; a listed URL does not verify the target's identity, label, version, completeness, current legal status, or applicability."


class HarnessError(ValueError):
    pass


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as stream:
        return json.load(stream)


def emit(value: Any, output: Path | None = None) -> None:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2)
    text = text.rstrip("\n") + "\n"
    if output is None:
        sys.stdout.write(text)
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=output.parent, delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(text)
    temporary.replace(output)


def validate_schema(value: Any, schema: dict, path: str = "response") -> None:
    """Validate the subset of JSON Schema used by the bundled response contract."""
    types = schema.get("type")
    types = [types] if isinstance(types, str) else types
    checks = {"object": lambda x: isinstance(x, dict), "array": lambda x: isinstance(x, list),
              "string": lambda x: isinstance(x, str), "integer": lambda x: type(x) is int,
              "number": lambda x: type(x) in (int, float), "null": lambda x: x is None,
              "boolean": lambda x: type(x) is bool}
    if types and not any(checks[t](value) for t in types):
        raise HarnessError(f"{path}: expected {' or '.join(types)}")
    if "enum" in schema and value not in schema["enum"]:
        raise HarnessError(f"{path}: unsupported value")
    if isinstance(value, dict):
        missing = set(schema.get("required", [])) - set(value)
        unknown = set(value) - set(schema.get("properties", {}))
        if missing:
            raise HarnessError(f"{path}: missing fields {sorted(missing)}")
        if schema.get("additionalProperties") is False and unknown:
            raise HarnessError(f"{path}: unexpected fields {sorted(unknown)}")
        for key, item in value.items():
            if key in schema.get("properties", {}):
                validate_schema(item, schema["properties"][key], f"{path}.{key}")
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0) or len(value) > schema.get("maxItems", math.inf):
            raise HarnessError(f"{path}: invalid item count")
        if schema.get("uniqueItems") and len({canonical(item) for item in value}) != len(value):
            raise HarnessError(f"{path}: duplicate items")
        for index, item in enumerate(value):
            validate_schema(item, schema.get("items", {}), f"{path}[{index}]")
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0) or len(value) > schema.get("maxLength", math.inf):
            raise HarnessError(f"{path}: invalid string length")
        if "pattern" in schema and not re.search(schema["pattern"], value):
            raise HarnessError(f"{path}: invalid format")
    if type(value) in (int, float) and value < schema.get("minimum", -math.inf):
        raise HarnessError(f"{path}: below minimum")


def query_expression(query: str) -> str:
    # Quote every user term; the CLI does not execute user-supplied FTS syntax.
    terms = list(dict.fromkeys(re.findall(r"[^\W_]+(?:[.\-][^\W_]+)*", query.lower(), re.UNICODE)))
    terms = [term for term in terms if term not in STOP_WORDS][:40]
    return " OR ".join('"' + term.replace('"', '""') + '"' for term in terms)


def string_values(value: Any) -> list[str]:
    values = [value] if isinstance(value, str) else value or []
    return [item for item in values if isinstance(item, str)]


def recorded_provenance(doc: dict, chunk: dict | None = None) -> dict:
    """Carry declared acquisition facts; do not infer identity or version matches."""
    chunk = chunk or {}
    acquisition = doc.get("acquisition_metadata") or {}
    containers = (doc, chunk, acquisition, doc.get("acquisition_record") or {})

    def declared(key: str, default: Any = None) -> Any:
        return next((item[key] for item in containers if item.get(key) is not None), default)

    origin = declared("source_origin", "not_recorded")
    status = declared("customer_inventory_status", "not_recorded")
    explicit = origin == "customer_pdf_link" and status == "explicitly_listed"
    flags = list(dict.fromkeys(flag for item in containers for key in ("uncertainty_flags", "review_flags")
                               for flag in string_values(item.get(key))))
    if explicit:
        # This exact pair resolves selection provenance, not any other uncertainty.
        flags = [flag for flag in flags if flag not in {"original_resource_inventory_not_reconciled", "original_inventory_missing"}]
    return {"source_origin": origin, "customer_inventory_status": status,
            "customer_reference_ids": list(dict.fromkeys(ref for item in containers
                                                        for ref in string_values(item.get("customer_reference_ids")))),
            "customer_listed_url": declared("customer_listed_url"),
            "customer_selection_explicitly_recorded": explicit,
            "inventory_scope_note": INVENTORY_SCOPE_NOTE,
            "target_resolution_status": declared("target_resolution_status", "not_recorded"),
            "version_resolution_status": declared("version_resolution_status", "not_recorded"),
            "uncertainty_flags": flags}


def summarize_provenance(documents: list[dict]) -> dict:
    """Describe this snapshot's recorded coverage without certifying the inventory."""
    records = [(doc, recorded_provenance(doc)) for doc in documents]
    listed = sum(provenance["customer_selection_explicitly_recorded"] for _, provenance in records)
    other = len(records) - listed
    origins = Counter(provenance["source_origin"] for _, provenance in records)
    processing = Counter(doc.get("processing_status") or "not_recorded" for doc, _ in records)
    acquisition = Counter(doc.get("acquisition_status") or "not_recorded" for doc, _ in records)
    warnings = Counter(flag for _, provenance in records for flag in provenance["uncertainty_flags"])
    indexes = [doc["document_id"] for doc, _ in records
               if (doc.get("source_role") or (doc.get("acquisition_metadata") or {}).get("source_role")) == "collection_index"]
    unavailable = [{"document_id": doc["document_id"], "title": doc.get("title"), "customer_reference_ids": provenance["customer_reference_ids"],
                    "source_url": doc.get("source_url"), "acquisition_status": doc.get("acquisition_status"),
                    "processing_status": doc.get("processing_status")}
                   for doc, provenance in records if doc.get("processing_status") not in {"processed", "index_only"}]
    if listed:
        caveat = (f"The supplied customer PDF inventory is recorded for {listed} corpus source records explicitly listed in it. "
                  + INVENTORY_SCOPE_NOTE)
    else:
        caveat = ("This snapshot has no source records with both customer_pdf_link provenance and explicitly_listed inventory status. "
                  "The metadata therefore does not establish customer selection for these records; this does not establish that a source inventory is missing. "
                  + INVENTORY_SCOPE_NOTE)
    if other:
        caveat += f" {other} source records have other or unconfirmed selection provenance; retain their recorded review qualifications."
    if unavailable:
        caveat += (f" {len(unavailable)} source records have no indexed evidentiary text, including any failed, blocked, "
                   "or excluded targets listed in unavailable_sources; they cannot supply an answer passage.")
    caveat += (f" {len(indexes)} collection indexes are retained for provenance and excluded from evidence retrieval by default. "
               "Their linked contents are not certified as exhaustively acquired. Target-label mismatches, alternate sources, "
               "and version uncertainty remain separate review questions. Historical source statements and human interpretations "
               "must remain distinct from a determination of current applicability.")
    return {"customer_inventory": {
                "provided_inventory_recorded": bool(listed),
                "explicitly_listed_source_record_count": listed,
                "other_or_unconfirmed_source_record_count": other,
                "source_origin_counts": dict(sorted(origins.items())),
                "scope_note": INVENTORY_SCOPE_NOTE,
                "all_customer_targets_or_versions_verified": False},
            "processing_status_counts": dict(sorted(processing.items())),
            "acquisition_status_counts": dict(sorted(acquisition.items())),
            "collection_index_document_ids": indexes,
            "collection_expansion_completeness_verified": False,
            "unavailable_sources": unavailable,
            "recorded_uncertainty_flag_counts": dict(sorted(warnings.items())),
            "collection_caveat": caveat}


class Corpus:
    def __init__(self, path: Path):
        if not path.is_file():
            raise HarnessError(f"Corpus database does not exist: {path}. Run process_corpus.py first.")
        uri = "file:" + urllib.parse.quote(str(path.resolve()), safe="/") + "?mode=ro"
        self.connection = sqlite3.connect(uri, uri=True)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA query_only=ON")
        for table in ("documents", "chunks", "chunks_fts"):
            if not self.connection.execute("SELECT 1 FROM sqlite_master WHERE name=?", (table,)).fetchone():
                raise HarnessError(f"Missing corpus table {table}; rebuild with process_corpus.py.")

    def close(self) -> None:
        self.connection.close()

    @staticmethod
    def _snapshot(row: sqlite3.Row) -> dict:
        chunk = json.loads(row["chunk_meta"])
        doc = json.loads(row["doc_meta"])
        acquisition = doc.get("acquisition_metadata") or {}
        text = row["text"]
        if chunk.get("text_sha256") and digest(text) != chunk["text_sha256"]:
            raise HarnessError("Canonical corpus chunk hash mismatch; rebuild or inspect the corpus.")
        result = {key: chunk.get(key) for key in (
            "chunk_id", "document_id", "section_id", "title", "source_url", "resolved_url",
            "raw_sha256", "page", "slide", "source_locator", "heading_path", "section_start_char",
            "section_end_char", "document_start_char", "document_end_char", "extraction_method",
            "source_origin", "table_context")}
        result.update({"text": text, "text_sha256": digest(text), "offset_unit": "unicode_code_points",
                       "legal_status": chunk.get("legal_status") or doc.get("legal_status") or "not_determined",
                       "source_role": doc.get("source_role") or acquisition.get("source_role") or "unspecified",
                       "source_review_status": doc.get("source_review_status") or acquisition.get("source_review_status") or "not_reviewed",
                       "retrieved_at": doc.get("retrieved_at"),
                       "date_signals": {key: (doc.get("date_signals") or {}).get(key) for key in (
                           "pdf_modification_date_raw", "pdf_creation_date_raw", "http_last_modified_raw")}})
        result.update(recorded_provenance(doc, chunk))
        if "score" in row.keys():
            result["retrieval_score"] = row["score"]
        return result

    def summary(self) -> dict:
        snapshot = hashlib.sha256()
        documents = []
        for row in self.connection.execute("SELECT document_id, metadata_json FROM documents ORDER BY document_id"):
            snapshot.update(row["document_id"].encode("utf-8") + b"\0" + row["metadata_json"].encode("utf-8") + b"\n")
            documents.append(json.loads(row["metadata_json"]))
        return {"snapshot_sha256": snapshot.hexdigest(), "document_count": len(documents),
                "chunk_count": self.connection.execute("SELECT count(*) FROM chunks").fetchone()[0],
                **summarize_provenance(documents),
                "legal_status_automatically_resolved": False,
                "source_metadata_note": "File dates and headers are signals, not inferred legal effective dates.",
                "benchmark_status": "awaiting_client_benchmark"}

    def search(self, query: str, limit: int = 6, include_indexes: bool = False) -> list[dict]:
        if not 1 <= limit <= 30:
            raise HarnessError("limit must be between 1 and 30")
        expression = query_expression(query)
        if not expression:
            return []
        excluded = "" if include_indexes else " AND COALESCE(json_extract(d.metadata_json,'$.source_role'),json_extract(d.metadata_json,'$.acquisition_metadata.source_role'),'') != 'collection_index'"
        rows = self.connection.execute(
            "SELECT f.text, c.metadata_json AS chunk_meta, d.metadata_json AS doc_meta, "
            "bm25(chunks_fts,0.0,3.0,2.0,1.0) AS score "
            "FROM chunks_fts f JOIN chunks c ON c.chunk_id=f.chunk_id "
            "JOIN documents d ON d.document_id=c.document_id "
            "WHERE chunks_fts MATCH ?" + excluded + " ORDER BY score LIMIT ?", (expression, limit * 4)).fetchall()
        results, seen = [], set()
        for row in rows:
            item = self._snapshot(row)
            identity = (item["document_id"], item["text_sha256"])
            if identity in seen:
                continue
            seen.add(identity)
            results.append(item)
            if len(results) == limit:
                break
        return results

    def chunk(self, chunk_id: str) -> dict:
        row = self.connection.execute(
            "SELECT f.text, c.metadata_json AS chunk_meta, d.metadata_json AS doc_meta "
            "FROM chunks c JOIN chunks_fts f ON f.chunk_id=c.chunk_id "
            "JOIN documents d ON d.document_id=c.document_id WHERE c.chunk_id=?", (chunk_id,)).fetchone()
        if row is None:
            raise HarnessError(f"Unknown canonical chunk: {chunk_id}")
        return self._snapshot(row)


def packet_hash(packet: dict) -> str:
    return "pkt_" + digest(canonical({k: v for k, v in packet.items() if k != "packet_id"}))


def scope_key(scope: dict) -> dict:
    return {key: scope.get(key) for key in ("question", "program", "plan_type", "research_date")}


def read_resolutions(path: Path, scope: dict, evidence: list[dict]) -> list[dict]:
    if not path.exists():
        return []
    available = {e["chunk_id"]: (e["text_sha256"], e.get("raw_sha256"), e.get("source_url")) for e in evidence}
    current_ids = {e["chunk_id"]: e["evidence_id"] for e in evidence}
    records = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise HarnessError(f"Invalid resolution log JSON at line {line_no}") from exc
        if record.get("record_type") != "human_resolution":
            raise HarnessError(f"Unexpected record type in resolution log at line {line_no}")
        if scope_key(record.get("scope") or {}) != scope_key(scope):
            continue
        refs = record.get("evidence") or []
        if refs and all(available.get(ref.get("chunk_id")) == (ref.get("text_sha256"), ref.get("raw_sha256"), ref.get("source_url")) for ref in refs):
            record = dict(record, reference_map=[{"recorded_evidence_id": ref.get("evidence_id"), "current_evidence_id": current_ids[ref["chunk_id"]], "chunk_id": ref["chunk_id"]} for ref in refs])
            records.append(record)
    superseded = {r["supersedes_record_id"] for r in records if r.get("supersedes_record_id")}
    return [dict(record, active=record["record_id"] not in superseded) for record in records]


def quote_candidates(text: str) -> list[dict]:
    """Offer exact paragraph offsets so the model need not count characters."""
    spans, cursor = [], 0
    for boundary in re.finditer(r"\n\s*\n", text):
        if text[cursor:boundary.start()].strip():
            spans.append({"start": cursor, "end": boundary.start(), "kind": "paragraph"})
        cursor = boundary.end()
    if text[cursor:].strip():
        spans.append({"start": cursor, "end": len(text), "kind": "paragraph"})
    if not spans or len(spans) > 24:
        spans = [{"start": 0, "end": len(text), "kind": "whole_chunk"}]
    return spans


def make_packet(corpus: Corpus, args: argparse.Namespace) -> dict:
    if not args.query.strip() or len(args.query) > 1600:
        raise HarnessError("Research question must contain 1 to 1600 characters")
    found = corpus.search(args.query, args.limit, args.include_indexes)
    by_id = {e["chunk_id"]: e for e in found}
    for chunk_id in args.include_chunk or []:
        item = corpus.chunk(chunk_id)
        if item["source_role"] == "collection_index" and not args.include_indexes:
            raise HarnessError("Collection indexes are excluded from evidence by default. Use --include-indexes explicitly for research about an index.")
        by_id.setdefault(chunk_id, item)
    evidence = [dict(item, evidence_id=f"E{index}", quote_candidates=quote_candidates(item["text"])) for index, item in enumerate(by_id.values(), 1)]
    scope = {"question": args.query, "program": args.program, "plan_type": args.plan_type,
             "research_date": args.research_date, "assumptions": []}
    packet = {"schema_version": SCHEMA_VERSION, "created_at": datetime.now(timezone.utc).isoformat(),
              "scope": scope, "corpus": corpus.summary(),
              "retrieval": {"engine": "sqlite_fts5", "mode": "lexical_only", "query_expression": query_expression(args.query),
                            "requested_limit": args.limit, "evidence_count": len(evidence),
                            "collection_indexes_included": args.include_indexes,
                            "semantic_retrieval": False, "completeness_verified": False},
              "evidence": evidence, "human_resolutions": read_resolutions(args.resolutions, scope, evidence),
              "system_prompt": (CONFIG / "document_only_system.txt").read_text(encoding="utf-8"),
              "response_schema": load_json(CONFIG / "response.schema.json")}
    packet["packet_id"] = packet_hash(packet)
    return packet


def validate_packet(packet: dict, corpus: Corpus) -> None:
    if packet.get("packet_id") != packet_hash(packet):
        raise HarnessError("Packet hash mismatch; regenerate the packet instead of editing evidence.")
    if packet.get("corpus") != corpus.summary():
        raise HarnessError("Corpus snapshot has changed; regenerate the evidence packet before rendering or inference.")
    evidence_ids, chunk_ids = set(), set()
    for item in packet.get("evidence", []):
        evidence_id, chunk_id = item.get("evidence_id"), item.get("chunk_id")
        if not isinstance(evidence_id, str) or not re.fullmatch(r"E[1-9][0-9]*", evidence_id):
            raise HarnessError("Invalid packet evidence ID")
        if evidence_id in evidence_ids or chunk_id in chunk_ids:
            raise HarnessError("Duplicate evidence or chunk identity in packet")
        evidence_ids.add(evidence_id)
        chunk_ids.add(chunk_id)
        current = corpus.chunk(chunk_id)
        for key, expected in current.items():
            if key == "retrieval_score":
                continue
            if item.get(key) != expected:
                raise HarnessError(f"Packet no longer matches canonical corpus for {chunk_id}: {key}")
        if item.get("quote_candidates") != quote_candidates(current["text"]):
            raise HarnessError("Packet quotation candidates differ from canonical paragraph spans")
    if packet.get("response_schema") != load_json(CONFIG / "response.schema.json"):
        raise HarnessError("Packet response schema differs from this harness version; regenerate it.")
    if packet.get("system_prompt") != (CONFIG / "document_only_system.txt").read_text(encoding="utf-8"):
        raise HarnessError("Packet prompt differs from this harness version; regenerate it.")


def citation(item: dict) -> dict:
    return {key: item.get(key) for key in ("evidence_id", "chunk_id", "document_id", "title", "source_url", "resolved_url", "page", "slide", "source_locator", "heading_path", "raw_sha256", "text_sha256", "source_role", "source_origin", "customer_inventory_status", "customer_reference_ids", "customer_listed_url", "customer_selection_explicitly_recorded", "inventory_scope_note", "target_resolution_status", "version_resolution_status", "extraction_method", "retrieved_at", "source_review_status", "legal_status", "date_signals", "uncertainty_flags")}


def reject_generated_urls(value: Any) -> None:
    if isinstance(value, dict):
        for item in value.values():
            reject_generated_urls(item)
    elif isinstance(value, list):
        for item in value:
            reject_generated_urls(item)
    elif isinstance(value, str) and re.search(r"(?:https?://|www\.)", value, re.I):
        raise HarnessError("Model-generated URLs are not accepted; citations must come from canonical chunks.")


def render_response(packet: dict, response: dict, corpus: Corpus) -> dict:
    validate_packet(packet, corpus)
    validate_schema(response, load_json(CONFIG / "response.schema.json"))
    for field in ("findings", "uncertainties", "clarifying_questions"):
        reject_generated_urls(response[field])
    reject_generated_urls(response["scope"]["assumptions"])
    if scope_key(response["scope"]) != scope_key(packet["scope"]):
        raise HarnessError("Response changed the research scope; retain the packet scope and ask a clarification instead.")
    by_evidence = {e["evidence_id"]: e for e in packet["evidence"]}
    by_chunk = {e["chunk_id"]: e for e in packet["evidence"]}
    referenced = set()
    for group in (response["findings"], response["uncertainties"]):
        for entry in group:
            unknown = set(entry["evidence_ids"]) - set(by_evidence)
            if unknown:
                raise HarnessError(f"Response cites unknown evidence IDs: {sorted(unknown)}")
            referenced.update(entry["evidence_ids"])
    rendered_quotes, quoted_chunks = [], set()
    for quote in response["quotes"]:
        item = by_chunk.get(quote["chunk_id"])
        if item is None:
            raise HarnessError("Response selects a quote from a chunk outside this packet")
        start, end = quote["start"], quote["end"]
        if not 0 <= start < end <= len(item["text"]) or not item["text"][start:end].strip():
            raise HarnessError("Quote offsets must select a nonempty span inside canonical chunk text")
        quoted_chunks.add(quote["chunk_id"])
        rendered_quotes.append({**quote, "text": item["text"][start:end], "offset_unit": "unicode_code_points",
                                "document_start_char": item["document_start_char"] + start,
                                "document_end_char": item["document_start_char"] + end,
                                "citation": citation(item)})
    required_quotes = {by_evidence[eid]["chunk_id"] for finding in response["findings"] for eid in finding["evidence_ids"]}
    if required_quotes - quoted_chunks:
        raise HarnessError("Every evidence ID used in a finding needs a verified quotation span")
    status = response["status"]
    if status in {"supported", "partial"} and not response["findings"]:
        raise HarnessError(f"{status} requires at least one supported finding")
    if status == "supported" and (response["uncertainties"] or response["clarifying_questions"]):
        raise HarnessError("Supported status cannot conceal unresolved uncertainties; select an appropriate qualified status")
    if status in {"partial", "conflicting_sources", "currency_unverified"} and not response["uncertainties"]:
        raise HarnessError(f"{status} requires an explicit uncertainty")
    if status == "clarification_needed" and not (response["clarifying_questions"] or any(x["question_for_user"] for x in response["uncertainties"])):
        raise HarnessError("clarification_needed requires a question for the user")
    if status == "not_found" and response["findings"]:
        raise HarnessError("not_found cannot contain substantive findings")
    if not by_evidence and status not in {"not_found", "clarification_needed"}:
        raise HarnessError("There is no retrieved evidence for this result state")
    findings = [{**f, "citations": [citation(by_evidence[eid]) for eid in f["evidence_ids"]]} for f in response["findings"]]
    uncertainties = [{**u, "citations": [citation(by_evidence[eid]) for eid in u["evidence_ids"]]} for u in response["uncertainties"]]
    # These notices are inserted by the application, regardless of model wording.
    return {"schema_version": SCHEMA_VERSION, "packet_id": packet["packet_id"], "status": status,
            "scope": response["scope"], "findings": findings, "quotes": rendered_quotes,
            "uncertainties": uncertainties, "clarifying_questions": response["clarifying_questions"],
            "human_resolutions": packet.get("human_resolutions", []),
            "corpus_caveat": packet["corpus"]["collection_caveat"],
            "corpus_coverage": {key: packet["corpus"][key] for key in (
                "customer_inventory", "processing_status_counts", "acquisition_status_counts",
                "collection_index_document_ids", "collection_expansion_completeness_verified",
                "unavailable_sources", "recorded_uncertainty_flag_counts")},
            "source_notices": [citation(e) for e in packet["evidence"]],
            "verification": {"canonical_chunk_identity_checked": True, "quotation_spans_checked": True,
                             "citations_constructed_from_corpus": True, "schema_checked": True,
                             "semantic_correctness_verified": False, "legal_currentness_verified": False,
                             "benchmark_status": "awaiting_client_benchmark",
                             "note": "Mechanical checks verify source identity and text spans; they do not establish that a paraphrase is correct, complete, applicable, or within the advice boundary."}}


def markdown_literal(text: Any) -> str:
    escaped = html.escape(str(text), quote=False)
    return re.sub(r"([\\`*_\[\]{}#+|])", r"\\\1", escaped)


def source_link(item: dict) -> str:
    label = markdown_literal(item.get("title") or item.get("chunk_id") or item.get("document_id") or "Source")
    url = item.get("source_url")
    if isinstance(url, str) and urllib.parse.urlsplit(url).scheme in {"https", "http"}:
        safe_url = url.replace("(", "%28").replace(")", "%29").replace(" ", "%20").replace("\n", "").replace("\r", "")
        return f"[{label}]({safe_url})"
    return label + " (source URL unavailable)"


def as_markdown(result: dict) -> str:
    lines = [f"# Research result: {result['status'].replace('_', ' ')}", "", markdown_literal(result["corpus_caveat"]), "", "## Scope", ""]
    for key, value in result["scope"].items():
        if value not in (None, [], ""):
            lines.append(f"- **{key.replace('_', ' ').title()}:** {markdown_literal(', '.join(value) if isinstance(value, list) else value)}")
    lines += ["", "## Findings", ""]
    if not result["findings"]:
        lines.append("No supported finding is presented in this result.")
    for finding in result["findings"]:
        refs = "; ".join(f"{c['evidence_id']}: {source_link(c)}" for c in finding["citations"])
        lines.append(f"- {markdown_literal(finding['text'])} ({refs})")
    lines += ["", "## Source language", ""]
    for quote in result["quotes"]:
        ref = quote["citation"]
        location = f", page {ref['page']}" if ref.get("page") is not None else ""
        location += f", slide {ref['slide']}" if ref.get("slide") is not None else ""
        lines += [f"**{ref['evidence_id']} — {source_link(ref)}{location}**", ""]
        lines.extend("> " + markdown_literal(line) for line in quote["text"].splitlines())
        lines.append("")
    if result["uncertainties"]:
        lines += ["## Uncertainties", ""]
        for item in result["uncertainties"]:
            ids = ", ".join(item["evidence_ids"]) or "No supporting passage found"
            lines.append(f"- **{markdown_literal(item['kind'])}:** {markdown_literal(item['description'])} [{ids}]")
            if item["question_for_user"]:
                lines.append(f"  Question for you: {markdown_literal(item['question_for_user'])}")
        lines.append("")
    if result["clarifying_questions"]:
        lines += ["## Clarifying questions", ""] + ["- " + markdown_literal(q) for q in result["clarifying_questions"]] + [""]
    if result["human_resolutions"]:
        lines += ["## Recorded human decisions", "", "These are user interpretations or scope choices, not additional source facts.", ""]
        for item in result["human_resolutions"]:
            state = "active" if item.get("active", True) else "explicitly superseded by a later user record"
            mapping = "; ".join(f"recorded {ref['recorded_evidence_id']} = current {ref['current_evidence_id']}" for ref in item.get("reference_map", []))
            lines.append(f"- {markdown_literal(item['human_decision'])} ({state}; {markdown_literal(item['record_id'])}; {markdown_literal(mapping)})")
        lines.append("")
    if result["corpus_coverage"]["unavailable_sources"]:
        lines += ["## Sources without indexed evidence", "",
                  "These recorded targets did not supply indexed answer passages. Their absence cannot establish that a rule does not exist.", ""]
        for item in result["corpus_coverage"]["unavailable_sources"]:
            lines.append(f"- {source_link(item)}: acquisition {markdown_literal(item['acquisition_status'])}; processing {markdown_literal(item['processing_status'])}.")
        lines.append("")
    lines += ["## Source status and validation", ""]
    for item in result["source_notices"]:
        signals = "; ".join(f"{key}={value}" for key, value in item["date_signals"].items() if value)
        notices = "; ".join(item["uncertainty_flags"]) or "No extraction flag recorded"
        lines.append(f"- {item['evidence_id']}: legal status {markdown_literal(item['legal_status'])}; review status {markdown_literal(item['source_review_status'])}; {markdown_literal(notices)}.")
        selection = "explicitly listed in the customer PDF" if item["customer_selection_explicitly_recorded"] else "other or unconfirmed selection provenance"
        lines.append(f"  Selection: {selection}; target review {markdown_literal(item['target_resolution_status'])}; version review {markdown_literal(item['version_resolution_status'])}.")
        if item.get("resolved_url") and item["resolved_url"] != item.get("source_url"):
            lines.append("  Retrieved copy: " + source_link({"title": "Retrieved source location", "source_url": item["resolved_url"]}))
        if signals:
            lines.append(f"  File/header date signals: {markdown_literal(signals)}. These do not establish legal effectivity.")
    lines += ["", result["verification"]["note"], "Client benchmark: pending.", ""]
    return "\n".join(lines)


def model_messages(packet: dict) -> list[dict]:
    evidence_payload = {key: packet[key] for key in ("scope", "corpus", "retrieval", "evidence", "human_resolutions")}
    return [{"role": "system", "content": packet["system_prompt"]},
            {"role": "user", "content": canonical(evidence_payload)}]


def cost_estimate(packet: dict, args: argparse.Namespace, usage: dict | None = None, response_text: str | None = None) -> dict:
    input_chars = sum(len(x["content"]) for x in model_messages(packet)) + len(canonical(packet["response_schema"]))
    estimated_input = math.ceil(input_chars / args.characters_per_token)
    input_tokens = usage.get("prompt_tokens") if usage else None
    output_tokens = usage.get("completion_tokens") if usage else None
    measured = type(input_tokens) is int and type(output_tokens) is int and input_tokens >= 0 and output_tokens >= 0
    if not measured:
        input_tokens = estimated_input
        output_tokens = math.ceil(len(response_text) / args.characters_per_token) if response_text is not None else args.output_tokens
    per_answer = (input_tokens * args.input_per_million + output_tokens * args.output_per_million) / 1_000_000
    annual_answers = args.queries_per_hour * args.hours_per_day * args.days_per_year
    return {"currency": "USD", "input_tokens": input_tokens, "output_tokens": output_tokens,
            "token_count_basis": "server_reported_usage" if measured else "character_based_estimate_not_model_tokenizer",
            "characters_per_token_assumption": args.characters_per_token,
            "input_per_million_tokens": args.input_per_million, "output_per_million_tokens": args.output_per_million,
            "price_basis": "user_configurable_illustration_not_a_vendor_quote",
            "estimated_model_cost_per_response": round(per_answer, 6), "assumed_annual_responses": annual_answers,
            "estimated_annual_model_cost": round(per_answer * annual_answers, 2),
            "scope": "One inference request over this evidence packet; add verification calls and retries; include billable reasoning tokens if not already counted in reported output usage.",
            "exclusions": ["retrieval", "hosting", "GPU utilization", "training", "development", "expert review"],
            "quality_benchmark_status": "awaiting_client_benchmark"}


def capacity_estimate(args: argparse.Namespace) -> dict:
    """Financial capacity arithmetic from supplied assumptions; no measurements."""
    rate, hours, budget, queries = args.gpu_hourly_rate, args.active_hours_per_year, args.annual_budget, args.queries_per_hour
    values = (rate, hours, budget, queries)
    if any(not math.isfinite(value) for value in values) or rate <= 0 or hours <= 0 or budget < 0 or queries < 0:
        raise HarnessError("GPU hourly rate and running hours must be finite and positive; budget and queries/hour must be finite and nonnegative")
    for name in ("input_tokens", "output_tokens"):
        value = getattr(args, name)
        if value is not None and (type(value) is not int or value < 0):
            raise HarnessError("Optional input/output token counts must be nonnegative integers")
    annual_gpu_cost = rate * hours
    annual_queries = queries * hours
    maximum_hours = budget / rate
    maximum_rate = budget / hours
    if not all(math.isfinite(value) for value in (annual_gpu_cost, annual_queries, maximum_hours, maximum_rate)):
        raise HarnessError("Capacity assumptions exceed the numeric range supported by this calculator")
    workload = {"queries_per_hour": queries, "assumed_annual_queries": annual_queries,
                "input_tokens_per_query": args.input_tokens, "output_tokens_per_query": args.output_tokens,
                "required_average_input_tokens_per_second": None if args.input_tokens is None else round(queries * args.input_tokens / 3600, 6),
                "required_average_output_tokens_per_second": None if args.output_tokens is None else round(queries * args.output_tokens / 3600, 6),
                "throughput_measured": False,
                "interpretation": "These are required aggregate workload rates under the supplied arrival-rate assumption, not demonstrated model throughput. Input processing and output generation have different compute costs; latency, concurrency, batching, and memory remain unmeasured."}
    return {"schema_version": SCHEMA_VERSION, "calculation": "self_hosted_gpu_budget_arithmetic",
            "currency": "USD", "gpu_hourly_rate_assumption": rate, "active_hours_per_year_assumption": hours,
            "running_hours_definition": "Billable hours while the chosen GPU-serving setup is running, including idle time while provisioned.",
            "hourly_rate_definition": "User-supplied aggregate hourly charge for the chosen serving setup; include the whole instance charge if that is how it is billed.",
            "price_basis": "user_supplied_assumptions_no_cloud_price_lookup", "annual_budget": budget,
            "annual_gpu_cost": round(annual_gpu_cost, 2), "budget_remaining_after_gpu": round(budget - annual_gpu_cost, 2),
            "within_gpu_budget_under_assumptions": annual_gpu_cost <= budget,
            "maximum_running_hours_at_supplied_rate": round(maximum_hours, 6),
            "maximum_hourly_rate_for_supplied_hours": round(maximum_rate, 6),
            "gpu_cost_per_assumed_query": None if annual_queries == 0 else round(annual_gpu_cost / annual_queries, 6),
            "workload": workload, "model_or_hardware_measured": False,
            "budget_bound_note": "Maximum hours and hourly rate are financial bounds only; they do not demonstrate that a model fits the hardware, serves the workload, or meets response-time and answer-quality requirements.",
            "exclusions": ["storage", "retrieval", "network charges", "development", "expert review", "training unless its hours are explicitly included"],
            "quality_benchmark_status": "awaiting_client_benchmark"}


def local_endpoint(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise HarnessError("Use a loopback http(s) endpoint without credentials, query parameters, or fragments")
    host = parsed.hostname or ""
    try:
        local = ipaddress.ip_address(host).is_loopback
    except ValueError:
        local = host.lower() == "localhost"
    if not local:
        raise HarnessError("This PoC inference command only permits localhost or a loopback IP address")
    if not parsed.path.endswith("/chat/completions"):
        raise HarnessError("Endpoint must end in /chat/completions")
    return url


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        raise HarnessError("Inference redirects are disabled; configure the exact loopback endpoint")


def infer(packet: dict, args: argparse.Namespace, corpus: Corpus) -> dict:
    validate_packet(packet, corpus)
    if not packet["evidence"]:
        response = {"status": "not_found", "scope": packet["scope"], "findings": [], "quotes": [],
                    "uncertainties": [{"kind": "retrieval_gap", "description": "The lexical search returned no evidentiary document passages in this corpus.", "evidence_ids": [], "question_for_user": None}], "clarifying_questions": []}
        result = render_response(packet, response, corpus)
        result["model_invoked"] = False
        return result
    endpoint = local_endpoint(args.endpoint)
    body = {"model": args.model, "messages": model_messages(packet), "temperature": 0,
            "max_tokens": args.max_output_tokens, "stream": False}
    # Explicit optional serving extension used by Qwen-compatible vLLM servers.
    # It is not assumed to be supported by every compatible endpoint.
    if getattr(args, "chat_template_kwargs", None):
        try:
            template_kwargs = json.loads(args.chat_template_kwargs)
        except json.JSONDecodeError as exc:
            raise HarnessError("--chat-template-kwargs must be a JSON object") from exc
        if not isinstance(template_kwargs, dict):
            raise HarnessError("--chat-template-kwargs must be a JSON object")
        body["chat_template_kwargs"] = template_kwargs
    if args.schema_mode == "json_schema":
        body["response_format"] = {"type": "json_schema", "json_schema": {"name": "document_research_response", "strict": True, "schema": packet["response_schema"]}}
    elif args.schema_mode == "json_object":
        body["response_format"] = {"type": "json_object"}
        body["messages"][0]["content"] += "\nRequired response schema:\n" + canonical(packet["response_schema"])
    else:
        body["messages"][0]["content"] += "\nRequired response schema:\n" + canonical(packet["response_schema"])
    headers = {"Content-Type": "application/json"}
    if args.api_key_env:
        key = os.environ.get(args.api_key_env)
        if not key:
            raise HarnessError("The configured API-key environment variable is unset")
        headers["Authorization"] = "Bearer " + key
    request = urllib.request.Request(endpoint, data=canonical(body).encode("utf-8"), headers=headers, method="POST")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        with opener.open(request, timeout=args.timeout) as response_stream:
            raw = response_stream.read(5_000_001)
        if len(raw) > 5_000_000:
            raise HarnessError("Model response exceeded the 5 MB limit")
        server_response = json.loads(raw)
        response_text = server_response["choices"][0]["message"]["content"]
        if not isinstance(response_text, str):
            raise HarnessError("Expected a text JSON response from the compatible endpoint")
        response = json.loads(response_text)
    except urllib.error.HTTPError as exc:
        raise HarnessError(f"Local model endpoint returned HTTP {exc.code}; no response accepted") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise HarnessError("Local model endpoint was unavailable or timed out; no response accepted") from exc
    except (KeyError, IndexError, json.JSONDecodeError) as exc:
        raise HarnessError("Local model did not return the expected JSON response; no answer accepted") from exc
    result = render_response(packet, response, corpus)
    if args.response_output:
        emit(response, args.response_output)
    result.update({"model_invoked": True, "model": args.model,
                   "cost_estimate": cost_estimate(packet, args, server_response.get("usage"), response_text)})
    return result


def append_resolution(packet: dict, args: argparse.Namespace, corpus: Corpus) -> dict:
    validate_packet(packet, corpus)
    evidence = {e["evidence_id"]: e for e in packet["evidence"]}
    unknown = set(args.evidence_id) - set(evidence)
    if unknown:
        raise HarnessError(f"Unknown evidence IDs for human resolution: {sorted(unknown)}")
    if not args.human_decision.strip() or not args.description.strip() or not args.reviewer.strip():
        raise HarnessError("Decision, description, and reviewer must be nonempty; use a non-identifying reviewer alias")
    path = args.resolutions
    if args.output is not None and args.output.resolve() == path.resolve():
        raise HarnessError("--output cannot overwrite the append-only resolution log")
    if args.supersedes_record_id:
        previous = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()] if path.exists() else []
        prior = next((record for record in previous if record.get("record_id") == args.supersedes_record_id), None)
        if prior is None:
            raise HarnessError("The resolution being corrected does not exist in this append-only log")
        if scope_key(prior.get("scope") or {}) != scope_key(packet["scope"]):
            raise HarnessError("A correction must retain the earlier resolution research scope")
    refs = [{key: evidence[eid].get(key) for key in ("evidence_id", "chunk_id", "document_id", "text_sha256", "raw_sha256", "source_url")} for eid in dict.fromkeys(args.evidence_id)]
    record = {"schema_version": SCHEMA_VERSION, "record_type": "human_resolution", "record_id": "hr_" + uuid.uuid4().hex,
              "recorded_at": datetime.now(timezone.utc).isoformat(), "packet_id": packet["packet_id"],
              "scope": packet["scope"], "kind": args.kind, "description": args.description,
              "human_decision": args.human_decision, "reviewer": args.reviewer, "evidence": refs,
              "supersedes_record_id": args.supersedes_record_id,
              "source_fact": False, "updates_source_metadata": False,
              "reuse_policy": "same_research_scope_and_same_evidence_hashes_only"}
    path.parent.mkdir(parents=True, exist_ok=True)
    # O_APPEND never rewrites earlier decisions. A correction is another record.
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        try:
            import fcntl
            fcntl.flock(descriptor, fcntl.LOCK_EX)
        except ImportError:
            pass
        encoded = (canonical(record) + "\n").encode("utf-8")
        view = memoryview(encoded)
        while view:
            view = view[os.write(descriptor, view):]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return record


def add_cost_arguments(parser: argparse.ArgumentParser, defaults: dict) -> None:
    parser.add_argument("--input-per-million", type=float, default=defaults["input_per_million_tokens"])
    parser.add_argument("--output-per-million", type=float, default=defaults["output_per_million_tokens"])
    parser.add_argument("--output-tokens", type=int, default=defaults["expected_output_tokens"])
    parser.add_argument("--characters-per-token", type=float, default=defaults["characters_per_token_assumption"])
    parser.add_argument("--queries-per-hour", type=float, default=defaults["queries_per_hour"])
    parser.add_argument("--hours-per-day", type=float, default=defaults["hours_per_day"])
    parser.add_argument("--days-per-year", type=float, default=defaults["days_per_year"])


def main(argv: list[str] | None = None) -> int:
    defaults = load_json(CONFIG / "poc_defaults.json")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    subs = parser.add_subparsers(dest="command", required=True)
    for name in ("search", "packet"):
        p = subs.add_parser(name, help="Offline lexical search" if name == "search" else "Export a complete evidence and prompt packet")
        p.add_argument("query")
        p.add_argument("--db", type=Path, default=ROOT / "processed/corpus.sqlite")
        p.add_argument("--limit", type=int, default=defaults["retrieval"]["default_limit"])
        p.add_argument("--include-indexes", action="store_true", help="Explicitly include navigation/collection indexes")
        p.add_argument("--output", type=Path)
        if name == "packet":
            p.add_argument("--program")
            p.add_argument("--plan-type")
            p.add_argument("--research-date", help="Research period/date as supplied; no legal date inferred")
            p.add_argument("--include-chunk", action="append", help="Add a user-selected canonical chunk")
            p.add_argument("--resolutions", type=Path, default=ROOT / "reviews/resolutions.jsonl")
    for name in ("render", "infer", "record-resolution"):
        p = subs.add_parser(name)
        p.add_argument("--packet", type=Path, required=True)
        p.add_argument("--db", type=Path, default=ROOT / "processed/corpus.sqlite")
        p.add_argument("--output", type=Path)
        if name in {"render", "infer"}:
            p.add_argument("--format", choices=("json", "markdown"), default="json")
        if name == "render":
            p.add_argument("--response", type=Path, required=True)
        elif name == "infer":
            p.add_argument("--model", required=True, help="Exact model ID configured in your own local server")
            p.add_argument("--endpoint", default=defaults["inference"]["endpoint"])
            p.add_argument("--api-key-env", help="Optional environment-variable name; its value is never written to output")
            p.add_argument("--max-output-tokens", type=int, default=defaults["inference"]["max_output_tokens"])
            p.add_argument("--schema-mode", choices=("json_schema", "json_object", "none"), default="json_schema")
            p.add_argument("--chat-template-kwargs", help="Optional server-supported JSON object, e.g. disable thinking for the Qwen baseline")
            p.add_argument("--timeout", type=float, default=60.0)
            p.add_argument("--response-output", type=Path, help="Save the validated model JSON separately")
            add_cost_arguments(p, defaults["cost_estimate"])
        else:
            p.add_argument("--kind", required=True)
            p.add_argument("--description", required=True)
            p.add_argument("--evidence-id", action="append", required=True)
            p.add_argument("--human-decision", required=True)
            p.add_argument("--reviewer", required=True, help="Non-identifying reviewer alias")
            p.add_argument("--supersedes-record-id", help="Explicitly correct a previous decision without rewriting it")
            p.add_argument("--resolutions", type=Path, default=ROOT / "reviews/resolutions.jsonl")
    p = subs.add_parser("estimate", help="Illustrative token-based model cost; performs no inference")
    p.add_argument("--packet", type=Path, required=True)
    p.add_argument("--output", type=Path)
    add_cost_arguments(p, defaults["cost_estimate"])
    p = subs.add_parser("capacity-estimate", help="Self-hosted GPU budget arithmetic from supplied assumptions; no model run or price lookup")
    p.add_argument("--gpu-hourly-rate", type=float, required=True, help="Assumed aggregate hourly charge for the selected GPU-serving setup")
    p.add_argument("--active-hours-per-year", type=float, required=True, help="Annual billable running hours, including idle provisioned time")
    p.add_argument("--queries-per-hour", type=float, default=defaults["cost_estimate"]["queries_per_hour"], help="Assumed arrival rate during the supplied running hours; not a throughput measurement")
    p.add_argument("--annual-budget", type=float, default=10000.0)
    p.add_argument("--input-tokens", type=int, help="Optional input/context tokens per query for required-workload arithmetic")
    p.add_argument("--output-tokens", type=int, help="Optional output tokens per query for required-workload arithmetic")
    p.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    corpus = None
    try:
        if args.command == "capacity-estimate":
            emit(capacity_estimate(args), args.output)
            return 0
        if args.command in {"estimate", "infer"}:
            numbers = [args.input_per_million, args.output_per_million, args.output_tokens, args.queries_per_hour, args.hours_per_day, args.days_per_year]
            if any(not math.isfinite(x) or x < 0 for x in numbers) or not math.isfinite(args.characters_per_token) or args.characters_per_token <= 0:
                raise HarnessError("Cost parameters must be finite and nonnegative; characters-per-token must be positive")
        if args.command == "infer" and (args.max_output_tokens <= 0 or not math.isfinite(args.timeout) or args.timeout <= 0):
            raise HarnessError("max-output-tokens and timeout must be positive")
        if args.command == "estimate":
            packet = load_json(args.packet)
            if packet.get("packet_id") != packet_hash(packet):
                raise HarnessError("Packet hash mismatch")
            emit(cost_estimate(packet, args), args.output)
            return 0
        corpus = Corpus(args.db)
        if args.command == "search":
            result = {"query": args.query, "retrieval_mode": "lexical_only", "query_expression": query_expression(args.query),
                      "corpus": corpus.summary(), "collection_indexes_included": args.include_indexes,
                      "results": corpus.search(args.query, args.limit, args.include_indexes)}
        elif args.command == "packet":
            result = make_packet(corpus, args)
        else:
            packet = load_json(args.packet)
            if args.command == "render":
                result = render_response(packet, load_json(args.response), corpus)
            elif args.command == "infer":
                result = infer(packet, args, corpus)
            else:
                result = append_resolution(packet, args, corpus)
        if getattr(args, "format", None) == "markdown":
            emit(as_markdown(result), args.output)
        else:
            emit(result, args.output)
        return 0
    except (HarnessError, OSError, sqlite3.Error, json.JSONDecodeError) as exc:
        print(f"Research harness error: {exc}", file=sys.stderr)
        return 2
    finally:
        if corpus is not None:
            corpus.close()


if __name__ == "__main__":
    raise SystemExit(main())
