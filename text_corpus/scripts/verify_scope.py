#!/usr/bin/env python3
"""Audit a corpus against only the exact URI actions in a supplied customer PDF.

Read-only with respect to corpus payloads and SQLite. No network, URL
normalization, prior-corpus ground truth, re-extraction, or legal/content
equivalence judgment. Reports are written only to --output (outside the corpus
by default). Exit 1 means an inventory, lineage, or packaged-payload violation;
exit 0 means those checks passed, NOT that acquisition/evidence is complete.
Use --require-complete to make incomplete acquisition/evidence return exit 2.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote, urljoin

from pypdf import PdfReader

VERSION = "1.0.0"
BATCH_NAMES = (
    "customer_direct_acquisition.jsonl",
    "customer_nydoh_recovery_acquisition.jsonl",
    "customer_fulltext_acquisition.jsonl",
)
CHECK_NAMES = (
    "pdf_uri_actions_read", "occurrence_multiset_matches_pdf", "unique_inventory_matches_pdf",
    "acquisition_inventory_matches_pdf", "document_inventory_matches_pdf",
    "source_identity_and_reference_lineage", "selected_acquisition_raw_lineage",
    "saved_acquisition_raw_accounting", "canonical_section_chunk_lineage",
    "sqlite_matches_canonical_records", "no_unlisted_packaged_source_payloads",
)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def text_sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def file_sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def source_id(uri):
    return "customer_" + text_sha(uri)[:12]


def write_jsonl(path, rows):
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        for row in rows:
            stream.write(canonical(row) + "\n")


class Audit:
    def __init__(self, root):
        self.root = root.resolve()
        self.checks = {name: {"passed": True, "violation_count": 0} for name in CHECK_NAMES}
        self.violations = []
        self.hash_cache = {}

    def check(self, name, condition, code, **details):
        if condition:
            return True
        self.checks[name]["passed"] = False
        self.checks[name]["violation_count"] += 1
        self.violations.append({"check": name, "code": code, **details})
        return False

    def read_jsonl(self, relative, check, required=True):
        path = self.root / relative
        if not path.is_file():
            if required:
                self.check(check, False, "required_file_missing", path=relative)
            return []
        rows = []
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise ValueError("Expected a JSON object")
                rows.append(row)
            except (ValueError, TypeError) as exc:
                self.check(check, False, "invalid_jsonl_record", path=relative, line=number, error=str(exc))
        return rows

    def raw_file(self, value, expected_sha=None, check="saved_acquisition_raw_accounting", **details):
        if not isinstance(value, str) or not value:
            self.check(check, False, "raw_path_missing", **details)
            return None, None
        path = Path(value)
        path = path if path.is_absolute() else self.root / path
        try:
            resolved = path.resolve(strict=True)
            relative = resolved.relative_to(self.root).as_posix()
            if not resolved.is_file():
                raise ValueError("Not a regular file")
        except (OSError, ValueError) as exc:
            self.check(check, False, "raw_path_missing_or_outside_root", raw_path=value, error=str(exc), **details)
            return None, None
        if resolved not in self.hash_cache:
            self.hash_cache[resolved] = file_sha(resolved)
        digest = self.hash_cache[resolved]
        if expected_sha is not None:
            self.check(check, bool(re.fullmatch(r"[0-9a-f]{64}", str(expected_sha))) and digest == expected_sha,
                       "raw_sha256_mismatch", raw_path=relative, expected_sha256=expected_sha, actual_sha256=digest, **details)
        return relative, digest


def extract_pdf_actions(path, audit):
    rows, action_errors = [], []
    reader = PdfReader(str(path), strict=False)
    for page_number, page in enumerate(reader.pages, 1):
        for annotation_number, reference in enumerate(page.get("/Annots", []), 1):
            annotation = reference.get_object()
            annotation_xref = getattr(reference, "idnum", None)
            roots = []
            if annotation.get("/A") is not None:
                roots.append(("/A", annotation["/A"]))
            if annotation.get("/AA") is not None:
                additional = annotation["/AA"].get_object()
                roots.extend(("/AA" + str(key), value) for key, value in additional.items())
            seen = set()
            while roots:
                action_path, reference_action = roots.pop(0)
                identity = (getattr(reference_action, "idnum", None), getattr(reference_action, "generation", None))
                if identity[0] is not None:
                    if identity in seen:
                        continue
                    seen.add(identity)
                action = reference_action.get_object()
                if not isinstance(action, dict):
                    continue
                if str(action.get("/S")) == "/URI" and "/URI" in action:
                    value = action["/URI"]
                    raw = None
                    try:
                        raw = value.original_bytes
                    except (AttributeError, ValueError):
                        if isinstance(value, bytes):
                            raw = bytes(value)
                    try:
                        uri = value.decode("utf-8") if isinstance(value, bytes) else str(value)
                    except UnicodeDecodeError as exc:
                        action_errors.append({"page": page_number, "annotation_xref": annotation_xref, "error": str(exc)})
                        continue
                    rows.append({"pdf_page": page_number, "annotation_index_on_page": annotation_number,
                        "annotation_xref": annotation_xref, "action_path": action_path, "source_url_exact": uri,
                        "source_id": source_id(uri), "uri_pdf_string_bytes_hex": raw.hex() if raw is not None else None})
                following = action.get("/Next")
                if following is not None:
                    following = following.get_object()
                    if isinstance(following, list):
                        roots.extend((action_path + f"/Next[{i}]", item) for i, item in enumerate(following))
                    elif isinstance(following, dict):
                        roots.append((action_path + "/Next", following))
    audit.check("pdf_uri_actions_read", bool(rows) and not action_errors, "pdf_uri_extraction_incomplete", errors=action_errors)
    return rows, len(reader.pages)


def exact_url(row):
    return row.get("source_url_exact", row.get("source_url"))


def refs(row):
    value = row.get("customer_reference_ids", row.get("customer_ref_ids", (row.get("acquisition_metadata") or {}).get("customer_reference_ids", [])))
    return value if isinstance(value, list) and all(isinstance(item, str) for item in value) else []


def compare_inventory(audit, rows, expected_uris, check, name, id_field="source_id"):
    actual = Counter(exact_url(row) for row in rows)
    missing = sorted(expected_uris - set(actual))
    extras = sorted((set(actual) - expected_uris), key=str)
    duplicates = [{"source_url_exact": uri, "count": count} for uri, count in actual.items() if count != 1]
    audit.check(check, not missing and not extras and not duplicates, "exact_uri_inventory_mismatch",
                inventory=name, missing_exact_uris=missing, extra_exact_uris=extras, duplicates=duplicates)
    for row in rows:
        uri = exact_url(row)
        if not isinstance(uri, str):
            audit.check(check, False, "exact_uri_missing", inventory=name, source_id=row.get(id_field))
            continue
        audit.check("source_identity_and_reference_lineage", row.get(id_field) == source_id(uri), "noncanonical_source_id",
                    inventory=name, source_id=row.get(id_field), expected_source_id=source_id(uri), source_url_exact=uri)
        if "source_url" in row:
            audit.check(check, row["source_url"] == uri, "source_url_differs_from_exact_uri", inventory=name, source_id=row.get(id_field))
    return {row.get(id_field): row for row in rows}


class Links(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.hrefs = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "a":
            href = dict(attrs).get("href")
            if href is not None:
                self.hrefs.append(href)


def run(root, pdf):
    audit = Audit(root)
    pdf_digest = file_sha(pdf)
    pdf_rows, page_count = extract_pdf_actions(pdf, audit)
    pdf_uris = {row["source_url_exact"] for row in pdf_rows}
    expected_ids = {source_id(uri): uri for uri in pdf_uris}
    audit.check("source_identity_and_reference_lineage", len(expected_ids) == len(pdf_uris), "canonical_source_id_collision")
    pdf_occurrences = Counter((row["pdf_page"], row["source_url_exact"]) for row in pdf_rows)
    occurrence_rows = audit.read_jsonl("manifests/customer_resource_links.jsonl", "occurrence_multiset_matches_pdf")
    recorded_occurrences = Counter((row.get("pdf_page"), exact_url(row)) for row in occurrence_rows)
    audit.check("occurrence_multiset_matches_pdf", recorded_occurrences == pdf_occurrences, "pdf_occurrence_multiset_mismatch",
        missing=[{"pdf_page": page, "source_url_exact": uri, "count": count} for (page, uri), count in (pdf_occurrences - recorded_occurrences).items()],
        extras=[{"pdf_page": page, "source_url_exact": uri, "count": count} for (page, uri), count in (recorded_occurrences - pdf_occurrences).items()])
    pdf_xrefs = Counter((row["pdf_page"], row["source_url_exact"], row["annotation_xref"]) for row in pdf_rows)
    recorded_xrefs = Counter((row.get("pdf_page"), exact_url(row), row.get("annotation_xref")) for row in occurrence_rows)
    audit.check("occurrence_multiset_matches_pdf", pdf_xrefs == recorded_xrefs, "annotation_xref_lineage_mismatch")
    ref_ids = [row.get("customer_ref_id") for row in occurrence_rows]
    audit.check("source_identity_and_reference_lineage", all(isinstance(value, str) and value for value in ref_ids) and len(ref_ids) == len(set(ref_ids)), "occurrence_reference_ids_missing_or_duplicated")
    refs_by_uri, pages_by_uri, labels_by_uri = defaultdict(list), defaultdict(list), defaultdict(list)
    pdf_bytes_by_annotation = {(row["pdf_page"], row["annotation_xref"], row["source_url_exact"]): row["uri_pdf_string_bytes_hex"] for row in pdf_rows}
    for row in occurrence_rows:
        uri = exact_url(row)
        refs_by_uri[uri].append(row.get("customer_ref_id"))
        pages_by_uri[uri].append(row.get("pdf_page"))
        labels_by_uri[uri].append(row.get("clickable_label"))
        audit.check("source_identity_and_reference_lineage", row.get("source_document_sha256") == pdf_digest, "occurrence_pdf_hash_mismatch", customer_ref_id=row.get("customer_ref_id"))
        if row.get("uri_pdf_string_bytes_hex") is not None:
            actual = pdf_bytes_by_annotation.get((row.get("pdf_page"), row.get("annotation_xref"), uri))
            audit.check("occurrence_multiset_matches_pdf", row["uri_pdf_string_bytes_hex"] == actual, "pdf_uri_string_bytes_mismatch", customer_ref_id=row.get("customer_ref_id"))
        if row.get("corpus_source_id") is not None and isinstance(uri, str):
            audit.check("source_identity_and_reference_lineage", row["corpus_source_id"] == source_id(uri), "occurrence_source_id_mismatch", customer_ref_id=row.get("customer_ref_id"))

    unique_rows = audit.read_jsonl("manifests/customer_unique_sources.jsonl", "unique_inventory_matches_pdf")
    acquisition_rows = audit.read_jsonl("manifests/acquisition.jsonl", "acquisition_inventory_matches_pdf")
    document_rows = audit.read_jsonl("processed/documents.jsonl", "document_inventory_matches_pdf")
    unique = compare_inventory(audit, unique_rows, pdf_uris, "unique_inventory_matches_pdf", "customer_unique_sources")
    acquisitions = compare_inventory(audit, acquisition_rows, pdf_uris, "acquisition_inventory_matches_pdf", "acquisition")
    documents = compare_inventory(audit, document_rows, pdf_uris, "document_inventory_matches_pdf", "documents", "document_id")
    for name, rows, id_key in (("unique", unique_rows, "source_id"), ("acquisition", acquisition_rows, "source_id"), ("documents", document_rows, "document_id")):
        for row in rows:
            uri = exact_url(row)
            audit.check("source_identity_and_reference_lineage", Counter(refs(row)) == Counter(refs_by_uri.get(uri, [])), "customer_reference_lineage_mismatch", inventory=name, source_id=row.get(id_key), expected_refs=refs_by_uri.get(uri, []), actual_refs=refs(row))
            declared_pdf = row.get("source_document_sha256") if name == "unique" else (row.get("metadata") if name == "acquisition" else row.get("acquisition_metadata") or {}).get("customer_pdf_sha256")
            audit.check("source_identity_and_reference_lineage", declared_pdf == pdf_digest, "source_pdf_hash_mismatch", inventory=name, source_id=row.get(id_key))
            audit.check("source_identity_and_reference_lineage", row.get("source_origin") == "customer_pdf_link", "source_origin_not_customer_pdf", inventory=name, source_id=row.get(id_key), actual=row.get("source_origin"))

    # Only saved direct/recovery/full-text acquisitions may account for raw files.
    batch_rows, raw_claims, fulltext_rows = [], defaultdict(list), []
    for batch_name in BATCH_NAMES:
        rows = audit.read_jsonl("manifests/" + batch_name, "saved_acquisition_raw_accounting", required=batch_name == BATCH_NAMES[0])
        for row in rows:
            uri, sid = row.get("source_url"), row.get("source_id")
            audit.check("saved_acquisition_raw_accounting", uri in pdf_uris and sid == source_id(uri) if isinstance(uri, str) else False,
                        "saved_acquisition_outside_pdf_allowlist", batch=batch_name, source_id=sid, source_url=uri)
            batch_rows.append((batch_name, row))
            if row.get("status") == "downloaded":
                path, digest = audit.raw_file(row.get("raw_path"), row.get("sha256"), batch=batch_name, source_id=sid)
                audit.check("saved_acquisition_raw_accounting", bool(row.get("sha256")), "saved_raw_hash_missing", batch=batch_name, source_id=sid)
                if path:
                    raw_claims[path].append({"batch": batch_name, "source_id": sid, "source_url": uri, "sha256": digest})
            elif row.get("raw_path"):
                audit.check("saved_acquisition_raw_accounting", False, "nondownloaded_saved_record_claims_raw_payload", batch=batch_name, source_id=sid, raw_path=row["raw_path"])
            if batch_name == BATCH_NAMES[2] and row.get("status") == "downloaded":
                fulltext_rows.append(row)
    raw_dir = audit.root / "raw"
    raw_files = {path.relative_to(audit.root).as_posix() for path in raw_dir.rglob("*") if path.is_file()} if raw_dir.exists() else set()
    raw_orphans = sorted(raw_files - set(raw_claims))
    missing_raw = sorted(set(raw_claims) - raw_files)
    audit.check("saved_acquisition_raw_accounting", not raw_orphans and not missing_raw, "raw_payload_accounting_mismatch", orphan_raw_files=raw_orphans, registered_files_outside_raw_or_missing=missing_raw)
    for row in fulltext_rows:
        evidence = (row.get("metadata") or {}).get("full_text_link_evidence") or {}
        parent, _ = audit.raw_file(evidence.get("parent_raw_path"), evidence.get("parent_sha256"), source_id=row["source_id"], purpose="fulltext_parent")
        audit.check("saved_acquisition_raw_accounting", parent in raw_claims and any(claim["source_id"] == row["source_id"] for claim in raw_claims.get(parent, [])), "fulltext_parent_not_registered_to_same_pdf_source", source_id=row["source_id"], parent_raw_path=parent)
        audit.check("saved_acquisition_raw_accounting", evidence.get("href") == row.get("fetch_url"), "fulltext_fetch_link_mismatch", source_id=row["source_id"])
        if parent:
            parser = Links()
            parser.feed((audit.root / parent).read_bytes().decode("utf-8", errors="replace"))
            resolved_hrefs = {urljoin(evidence.get("parent_resolved_url") or "", href) for href in parser.hrefs}
            audit.check("saved_acquisition_raw_accounting", evidence.get("href") in resolved_hrefs, "fulltext_href_not_found_in_saved_parent", source_id=row["source_id"], href=evidence.get("href"))

    for sid, row in acquisitions.items():
        matching = [(batch, saved) for batch, saved in batch_rows if saved.get("source_id") == sid and saved.get("source_url") == row.get("source_url")
                    and saved.get("status") == row.get("status") and saved.get("raw_path") == row.get("raw_path") and saved.get("sha256") == row.get("sha256")]
        selected_batch = (row.get("metadata") or {}).get("selected_acquisition_batch")
        audit.check("selected_acquisition_raw_lineage", bool(matching) and (not selected_batch or any(batch == selected_batch for batch, _ in matching)),
                    "selected_acquisition_not_in_saved_batches", source_id=sid, selected_batch=selected_batch, raw_path=row.get("raw_path"), sha256=row.get("sha256"))
        doc = documents.get(sid)
        if not doc:
            continue
        audit.check("selected_acquisition_raw_lineage", doc.get("acquisition_status") == row.get("status") and doc.get("raw_path") == row.get("raw_path") and doc.get("source_url") == row.get("source_url") and doc.get("resolved_url") == row.get("resolved_url"), "document_selected_acquisition_mismatch", source_id=sid)
        audit.check("selected_acquisition_raw_lineage", doc.get("acquisition_record") == row, "document_embedded_acquisition_record_mismatch", source_id=sid)
        if row.get("status") == "downloaded":
            audit.check("selected_acquisition_raw_lineage", doc.get("raw_sha256") == row.get("sha256"), "document_selected_raw_hash_mismatch", source_id=sid)
            audit.raw_file(doc.get("raw_path"), doc.get("raw_sha256"), check="selected_acquisition_raw_lineage", source_id=sid)

    section_rows = audit.read_jsonl("processed/sections.jsonl", "canonical_section_chunk_lineage")
    chunk_rows = audit.read_jsonl("processed/chunks.jsonl", "canonical_section_chunk_lineage")
    sections, chunks = {}, {}
    section_counts, chunk_counts = Counter(), Counter()
    chunks_by_section = defaultdict(list)
    retrieval_extra_ids = set()
    for name, rows, id_key, target in (("sections", section_rows, "section_id", sections), ("chunks", chunk_rows, "chunk_id", chunks)):
        for row in rows:
            rid, sid = row.get(id_key), row.get("document_id")
            audit.check("canonical_section_chunk_lineage", isinstance(rid, str) and rid not in target, "missing_or_duplicate_row_id", table=name, row_id=rid)
            target[rid] = row
            doc = documents.get(sid)
            allowed = sid in expected_ids and row.get("source_url") == expected_ids.get(sid)
            audit.check("canonical_section_chunk_lineage", allowed and doc is not None, "canonical_row_outside_pdf_lineage", table=name, row_id=rid, source_id=sid, source_url=row.get("source_url"))
            if name == "chunks" and not allowed:
                retrieval_extra_ids.add(sid)
            if doc is None:
                continue
            for key in ("source_url", "resolved_url", "raw_path", "raw_sha256", "source_origin"):
                audit.check("canonical_section_chunk_lineage", row.get(key) == doc.get(key), "row_document_provenance_mismatch", table=name, row_id=rid, field=key)
            audit.check("canonical_section_chunk_lineage", Counter(refs(row)) == Counter(refs_by_uri.get(doc.get("source_url"), [])), "row_customer_reference_mismatch", table=name, row_id=rid)
            value = row.get("text")
            audit.check("canonical_section_chunk_lineage", isinstance(value, str) and row.get("text_sha256") == text_sha(value), "canonical_text_hash_mismatch", table=name, row_id=rid)
            left, right = row.get("document_start_char"), row.get("document_end_char")
            valid = isinstance(left, int) and isinstance(right, int) and 0 <= left <= right <= len(doc.get("text", ""))
            audit.check("canonical_section_chunk_lineage", valid and doc.get("text", "")[left:right] == value if valid else False, "document_quote_offset_mismatch", table=name, row_id=rid)
            audit.check("canonical_section_chunk_lineage", row.get("offset_unit") == "unicode_code_points", "unsupported_offset_unit", table=name, row_id=rid)
            if name == "sections":
                section_counts[sid] += 1
                index = row.get("section_index")
                audit.check("canonical_section_chunk_lineage", isinstance(index, int) and rid == f"{sid}__s{index:05d}" if isinstance(index, int) else False, "section_id_not_namespaced_to_source", row_id=rid)
            else:
                chunk_counts[sid] += 1
                section = sections.get(row.get("section_id"))
                a, b = row.get("section_start_char"), row.get("section_end_char")
                valid = section is not None and section.get("document_id") == sid and isinstance(a, int) and isinstance(b, int) and 0 <= a <= b <= len(section.get("text", ""))
                audit.check("canonical_section_chunk_lineage", valid and section["text"][a:b] == value if valid else False, "section_quote_offset_mismatch", row_id=rid)
                audit.check("canonical_section_chunk_lineage", isinstance(rid, str) and bool(re.fullmatch(re.escape(str(row.get("section_id"))) + r"__c\d{4,}", rid)), "chunk_id_not_namespaced_to_section", row_id=rid)
                audit.check("canonical_section_chunk_lineage", doc.get("retrieval_included") is True and doc.get("processing_status") == "processed" and doc.get("source_role") != "collection_index", "non_evidence_document_in_retrieval", row_id=rid, source_id=sid)
                chunks_by_section[row.get("section_id")].append(row)
    for sid, doc in documents.items():
        text = doc.get("text", "")
        if doc.get("text_sha256") is not None:
            audit.check("canonical_section_chunk_lineage", isinstance(text, str) and doc["text_sha256"] == text_sha(text), "document_text_hash_mismatch", source_id=sid)
        audit.check("canonical_section_chunk_lineage", section_counts[sid] == doc.get("section_count", 0), "document_section_count_mismatch", source_id=sid)
        if doc.get("retrieval_included"):
            audit.check("canonical_section_chunk_lineage", chunk_counts[sid] == doc.get("chunk_count", 0), "document_chunk_count_mismatch", source_id=sid)
        else:
            audit.check("canonical_section_chunk_lineage", not chunk_counts[sid], "excluded_document_has_retrieval_chunks", source_id=sid)
    for section_id_value, section in sections.items():
        if not documents.get(section.get("document_id"), {}).get("retrieval_included"):
            continue
        covered = 0
        for chunk in sorted(chunks_by_section.get(section_id_value, []), key=lambda value: value.get("section_start_char", -1)):
            a, b = chunk.get("section_start_char"), chunk.get("section_end_char")
            if not isinstance(a, int) or not isinstance(b, int):
                continue
            audit.check("canonical_section_chunk_lineage", not section["text"][covered:a].strip(), "retrieval_chunk_coverage_gap", section_id=section_id_value)
            covered = max(covered, b)
        audit.check("canonical_section_chunk_lineage", not section["text"][covered:].strip(), "retrieval_section_tail_omitted", section_id=section_id_value)

    database = audit.root / "processed/corpus.sqlite"
    db_counts = {}
    try:
        connection = sqlite3.connect("file:" + quote(str(database.resolve())) + "?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        audit.check("sqlite_matches_canonical_records", connection.execute("PRAGMA quick_check").fetchone()[0] == "ok", "sqlite_quick_check_failed")
        for table, mapping, id_key in (("documents", documents, "document_id"), ("chunks", chunks, "chunk_id")):
            seen_ids = Counter()
            for record in connection.execute("SELECT * FROM " + table):
                rid = record[id_key]
                seen_ids[rid] += 1
                expected = mapping.get(rid)
                audit.check("sqlite_matches_canonical_records", expected is not None, "sqlite_extra_row", table=table, row_id=rid)
                if expected is None:
                    if table == "chunks":
                        retrieval_extra_ids.add(record["document_id"])
                    continue
                audit.check("sqlite_matches_canonical_records", json.loads(record["metadata_json"]) == {key: value for key, value in expected.items() if key != "text"}, "sqlite_metadata_mismatch", table=table, row_id=rid)
                for key in record.keys():
                    if key != "metadata_json":
                        audit.check("sqlite_matches_canonical_records", record[key] == expected.get(key), "sqlite_column_mismatch", table=table, row_id=rid, field=key)
            audit.check("sqlite_matches_canonical_records", seen_ids == Counter({key: 1 for key in mapping}), "sqlite_row_set_mismatch", table=table)
            db_counts[table] = sum(seen_ids.values())
        seen_fts = Counter()
        for row in connection.execute("SELECT chunk_id,title,heading_path,text FROM chunks_fts"):
            seen_fts[row["chunk_id"]] += 1
            expected = chunks.get(row["chunk_id"])
            audit.check("sqlite_matches_canonical_records", expected is not None and row["text"] == expected.get("text") and row["title"] == (expected.get("title") or "") and row["heading_path"] == " > ".join(expected.get("heading_path", [])), "sqlite_fts_text_or_identity_mismatch", chunk_id=row["chunk_id"])
        audit.check("sqlite_matches_canonical_records", seen_fts == Counter({key: 1 for key in chunks}), "sqlite_fts_row_set_mismatch")
        db_counts["chunks_fts"] = sum(seen_fts.values())
        connection.close()
    except (sqlite3.Error, OSError, ValueError, KeyError) as exc:
        audit.check("sqlite_matches_canonical_records", False, "sqlite_audit_failed", error=str(exc))

    # Comparison metadata is not source evidence. Actual old document payloads
    # are package extras even when absent from all retrieval tables.
    package_extras = []
    approved_reference_paths = set()
    reference_dir = audit.root / "reference"
    if reference_dir.exists():
        for path in reference_dir.rglob("*.pdf"):
            if file_sha(path) == pdf_digest:
                approved_reference_paths.add(path.relative_to(audit.root).as_posix())
    source_suffixes = {".pdf", ".html", ".htm", ".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx", ".epub", ".rtf"}
    artifact_dirs = {"processed", "manifests", "evaluation", "config", "scripts", "tests", "examples", "reports", "docs", "documentation", "audit"}
    for path in audit.root.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(audit.root).as_posix()
        parts = Path(relative).parts
        if relative in raw_claims or relative in approved_reference_paths:
            continue
        previous_payload = len(parts) >= 3 and parts[:2] == ("comparison", "previous_samples")
        other_payload = path.suffix.lower() in source_suffixes and parts[0] not in artifact_dirs and parts[0] != "raw"
        if previous_payload or other_payload:
            package_extras.append({"path": relative, "sha256": file_sha(path), "category": "previous_comparison_source_payload" if previous_payload else "unregistered_source_document_payload",
                "retrieval_extra": False, "note": "Package payload is not authorized by a direct/recovery/full-text acquisition for a PDF-listed source; retrieval membership is audited separately."})
            audit.check("no_unlisted_packaged_source_payloads", False, "unlisted_packaged_source_payload", path=relative, category=package_extras[-1]["category"])

    ledger = []
    for uri in sorted(pdf_uris):
        sid = source_id(uri)
        acq, doc, inventory = acquisitions.get(sid, {}), documents.get(sid, {}), unique.get(sid, {})
        metadata = acq.get("metadata") or {}
        flags = list(dict.fromkeys((doc.get("uncertainty_flags") or []) + (metadata.get("review_flags") or [])))
        has_text = bool(doc.get("text", "").strip())
        no_text_flag = any(flag in {"no_extractable_text", "no_extractable_text_no_ocr_attempted", "no_rule_body_or_substantive_note_placeholder", "access_or_bot_challenge_page"} for flag in flags)
        if not acq or not doc:
            coverage = "inventory_record_missing"
        elif acq.get("status") != "downloaded":
            coverage = "acquisition_unavailable"
        elif not has_text or no_text_flag or doc.get("processing_status") in {"extraction_failed", "no_extractable_text", "excluded_access_page", "excluded_no_rule_body_placeholder"}:
            coverage = "downloaded_content_unresolved"
        elif doc.get("processing_status") == "index_only" or doc.get("source_role") == "collection_index":
            coverage = "listed_target_index_only"
        elif doc.get("retrieval_included") and chunk_counts[sid]:
            coverage = "evidence_available"
        elif doc.get("duplicate_of"):
            coverage = "duplicate_bytes_not_separately_indexed"
        else:
            coverage = "downloaded_not_admitted_as_evidence"
        selected_batch = metadata.get("selected_acquisition_batch")
        representation = "publisher_full_text_link" if selected_batch == BATCH_NAMES[2] else "declared_alternate_host_recovery" if selected_batch == BATCH_NAMES[1] else "direct_request_with_recorded_redirects"
        ledger.append({"source_id": sid, "source_url_exact": uri, "pdf_pages": sorted(set(pages_by_uri.get(uri, []))),
            "pdf_occurrence_count": sum(count for (page, value), count in pdf_occurrences.items() if value == uri),
            "customer_reference_ids": refs_by_uri.get(uri, []), "customer_labels_not_revalidated_by_this_script": labels_by_uri.get(uri, []),
            "expected_resource_type": inventory.get("resource_type_expected"), "source_role": doc.get("source_role", acq.get("source_role")),
            "acquisition_status": acq.get("status", "missing"), "processing_status": doc.get("processing_status", "missing"),
            "coverage_status": coverage, "has_nonblank_canonical_text": has_text, "canonical_character_count": len(doc.get("text", "")),
            "section_count": section_counts[sid], "retrieval_chunk_count": chunk_counts[sid], "retrieval_included": bool(doc.get("retrieval_included")),
            "selected_raw_path": acq.get("raw_path"), "selected_raw_sha256": acq.get("sha256"), "selected_acquisition_batch": selected_batch,
            "fetch_url": acq.get("fetch_url"), "resolved_url": acq.get("resolved_url"), "representation_route": representation,
            "resolved_url_is_exact_pdf_uri": acq.get("resolved_url") == uri if acq.get("status") == "downloaded" else None,
            "target_resolution_status": metadata.get("target_resolution_status"), "version_resolution_status": metadata.get("version_resolution_status"),
            "review_flags": flags, "failure_reason": acq.get("failure_reason") or doc.get("error"),
            "saved_representations": [{"batch": batch, "status": row.get("status"), "raw_path": row.get("raw_path"), "sha256": row.get("sha256"), "fetch_url": row.get("fetch_url"), "resolved_url": row.get("resolved_url")} for batch, row in batch_rows if row.get("source_id") == sid]})
    coverage_counts = dict(Counter(row["coverage_status"] for row in ledger))
    incomplete = [row for row in ledger if row["coverage_status"] != "evidence_available"]
    scope_passed = all(check["passed"] for check in audit.checks.values())
    retrieval_checks = ("acquisition_inventory_matches_pdf", "document_inventory_matches_pdf", "source_identity_and_reference_lineage", "selected_acquisition_raw_lineage", "canonical_section_chunk_lineage", "sqlite_matches_canonical_records")
    report = {"auditor_version": VERSION, "generated_at": datetime.now(timezone.utc).isoformat(), "root": str(audit.root), "pdf": str(pdf.resolve()),
        "ground_truth": {"kind": "supplied_pdf_exact_uri_actions", "sha256": pdf_digest, "page_count": page_count,
            "uri_occurrence_count": len(pdf_rows), "unique_exact_uri_count": len(pdf_uris), "uri_normalization_used": False,
            "prior_corpus_used_as_ground_truth": False},
        "scope_status": "passed" if scope_passed else "failed", "completion_status": "incomplete" if incomplete else "all_listed_targets_have_admitted_evidence",
        "checks": audit.checks, "violations": audit.violations,
        "retrieval_scope": {"passed": all(audit.checks[name]["passed"] for name in retrieval_checks), "extra_source_ids": sorted(retrieval_extra_ids, key=str)},
        "package_scope": {"extra_source_payload_count": len(package_extras), "extra_source_payloads": package_extras,
            "comparison_metadata_is_not_ground_truth_or_retrieval_evidence": True},
        "counts": {"unique_manifest_records": len(unique_rows), "acquisition_records": len(acquisition_rows), "document_records": len(document_rows),
            "section_records": len(section_rows), "chunk_records": len(chunk_rows), "raw_files": len(raw_files),
            "accounted_raw_files": len(raw_claims), "selected_downloaded_raw_paths": len({row.get("raw_path") for row in acquisition_rows if row.get("status") == "downloaded"}),
            "orphan_raw_files": len(raw_orphans), "sqlite": db_counts, "coverage": coverage_counts},
        "completeness": {"acquisition_complete": all(row["acquisition_status"] == "downloaded" for row in ledger),
            "all_listed_targets_have_nonblank_text": all(row["has_nonblank_canonical_text"] for row in ledger),
            "all_listed_targets_have_admitted_evidence": not incomplete,
            "missing_acquisition_source_ids": [row["source_id"] for row in ledger if row["coverage_status"] == "acquisition_unavailable"],
            "downloaded_content_unresolved_source_ids": [row["source_id"] for row in ledger if row["coverage_status"] == "downloaded_content_unresolved"],
            "index_only_source_ids": [row["source_id"] for row in ledger if row["coverage_status"] == "listed_target_index_only"],
            "collection_scope_note": "A listed collection landing page is accounted for as that exact URI. Its unlisted descendants are neither assumed downloaded nor automatically added to the PDF allowlist."},
        "limits": ["This verifies exact URI membership, recorded acquisition/hash lineage, canonical text offsets, and SQLite consistency; it does not establish legal/content/version equivalence.",
            "Redirects, alternate hosts, and publisher full-text links are disclosed representation routes, not proofs that the customer intended a particular historical version.",
            "No independent text re-extraction or legal interpretation is performed. Index text and a successful HTTP download do not establish complete rule coverage.",
            "Package scan checks every raw file, every comparison/previous_samples file, and source-document extensions outside declared artifact directories. It does not classify arbitrary legal prose embedded in general code or metadata as a source file."],
        "network_used": False, "corpus_payloads_modified": False}
    return report, ledger, pdf_rows


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path, help="Corpus directory to inspect read-only")
    parser.add_argument("--pdf", required=True, type=Path, help="Only source of the exact URI allowlist")
    parser.add_argument("--output", "--output-dir", dest="output", type=Path, default=Path(__file__).resolve().parent / "scope_report")
    parser.add_argument("--require-complete", action="store_true", help="Return exit 2 if any listed target is missing, index-only, or lacks admitted evidence")
    args = parser.parse_args(argv)
    args.output.mkdir(parents=True, exist_ok=True)
    try:
        report, ledger, occurrences = run(args.root, args.pdf)
    except Exception as exc:
        report = {"auditor_version": VERSION, "scope_status": "audit_failed", "completion_status": "not_determined", "error": f"{type(exc).__name__}: {exc}", "network_used": False, "corpus_payloads_modified": False}
        (args.output / "scope_verification.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 1
    (args.output / "scope_verification.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    write_jsonl(args.output / "source_coverage.jsonl", ledger)
    write_jsonl(args.output / "pdf_uri_occurrences.jsonl", occurrences)
    def md(value):
        return str(value).replace("|", "\\|").replace("\n", " ")
    lines = ["# PDF source coverage", "", f"Scope checks: **{report['scope_status']}**. Completion: **{report['completion_status']}**.", "",
        f"The supplied PDF contains {report['ground_truth']['uri_occurrence_count']} URI annotations and {report['ground_truth']['unique_exact_uri_count']} exact distinct targets. No URI normalization or prior corpus was used as ground truth.", "",
        "A passed scope check means that inventory, file/hash lineage, canonical rows, SQLite, and package source-payload checks passed. It does not mean every target was acquired or that its content/version is legally equivalent to another source.", "",
        "## Coverage counts", "", "| Coverage | Sources |", "|---|---:|"]
    for status, count in sorted(report["counts"]["coverage"].items()):
        lines.append(f"| {md(status)} | {count} |")
    lines += ["", "## Every exact PDF target", "", "The linked source IDs use the PDF's exact URI. Labels are copied from the occurrence inventory; this script verifies annotation URI/page lineage, not label meaning. Full paths, hashes, references, redirects, and qualifications are in `source_coverage.jsonl`.", "",
        "| PDF target / label | Pages | Coverage | Characters | Retrieval chunks |", "|---|---|---|---:|---:|"]
    for row in ledger:
        label = "; ".join(dict.fromkeys(value for value in row["customer_labels_not_revalidated_by_this_script"] if isinstance(value, str)))
        link = f"[{row['source_id']}](<{row['source_url_exact']}>)"
        lines.append(f"| {link}: {md(label)} | {', '.join(map(str,row['pdf_pages']))} | {md(row['coverage_status'])} | {row['canonical_character_count']:,} | {row['retrieval_chunk_count']} |")
    if report["package_scope"]["extra_source_payloads"]:
        lines += ["", "## Packaged source payloads outside the allowlist", "", "These are package extras. Retrieval membership is checked separately.", ""]
        lines.extend(f"- `{row['path']}` — {row['category']}" for row in report["package_scope"]["extra_source_payloads"])
    (args.output / "source_coverage.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"scope_status": report["scope_status"], "completion_status": report["completion_status"],
        "checks": report["checks"], "counts": report["counts"], "package_extra_source_payload_count": report["package_scope"]["extra_source_payload_count"],
        "report": str((args.output / "scope_verification.json").resolve()), "coverage_ledger": str((args.output / "source_coverage.jsonl").resolve())}, indent=2))
    if report["scope_status"] != "passed":
        return 1
    return 2 if args.require_complete and report["completion_status"] == "incomplete" else 0


if __name__ == "__main__":
    raise SystemExit(main())
