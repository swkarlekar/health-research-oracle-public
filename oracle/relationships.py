"""Snapshot-pinned canonical context and evidence-backed document relationships.

Construction scans source text locally. A structurally verified reference is only
proof that a source names a target; it never establishes legal authority or currency.
Semantic relationships remain candidates until separately interpreted and reviewed.
"""
from __future__ import annotations

import html
import json
import re
import sqlite3
import tempfile
import urllib.parse
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from .corpus import HarnessError, base

SCHEMA_VERSION = "1.0"
RELATION_TYPES = frozenset({"references", "amends", "supersedes", "supersedes_in_part",
                           "clarifies", "corrects", "conflicts_with", "relationship_signal"})
STATUSES = frozenset({"verified_reference", "candidate", "unresolved", "model_reviewed",
                      "model_rejected", "verified", "rejected"})
URL_RE = re.compile(r"https?://[^\s<>\[\]{}\"“”]+", re.I)
DASHES = "-\u2010\u2011\u2012\u2013\u2014\u2015\u2212"
DASH_CLASS = re.escape(DASHES)
IDENTIFIER = rf"\d+[a-z]*(?:\.\d+[a-z]*|[{DASH_CLASS}][a-z0-9]+|\s+[{DASH_CLASS}]\s*\d+[a-z]*)*(?:\([a-z0-9]+\))*"
POLICY_RE = re.compile(rf"\b(?:MLTC\s+)?Policy\s+(?:No\.?\s*)?(\d{{2}}[.{DASH_CLASS}]\d{{2}})\b", re.I)
FEDERAL_RE = re.compile(rf"\b(\d+)\s*(C\.?\s*F\.?\s*R\.?|U\.?\s*S\.?\s*C\.?)\s*(?:§{{1,2}}\s*|[Ss]ection\s+)?({IDENTIFIER})(?![a-z0-9{DASH_CLASS}])", re.I)
NYCRR_RE = re.compile(rf"\b(\d+)\s*N\.?\s*Y\.?\s*C\.?\s*R\.?\s*R\.?\s*(?:§{{1,2}}\s*)?({IDENTIFIER})(?![a-z0-9{DASH_CLASS}])", re.I)
NY_LAW_RE = re.compile(rf"\b(?:N\.?\s*Y\.?\s*)?(Public\s+Health\s+Law|Social\s+Services\s+Law|PHL|SSL)\s*§{{0,2}}\s*({IDENTIFIER})(?![a-z0-9{DASH_CLASS}])", re.I)
BARE_SECTION_RE = re.compile(rf"§\s*({IDENTIFIER})(?![a-z0-9{DASH_CLASS}])", re.I)
SEMANTIC_RE = re.compile(r"\b(supersed\w*|amend\w*|clarif\w*|correct\w*|replac\w*|rescind\w*|rescission\w*|withdrawn|withdrew|conflict\w*)\b", re.I)


def _rows(path):
    with Path(path).open(encoding="utf-8") as stream:
        for line_no, line in enumerate(stream, 1):
            if line.strip():
                try:
                    value = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise HarnessError(f"Invalid graph input JSON at {path}:{line_no}") from exc
                if not isinstance(value, dict):
                    raise HarnessError("Graph JSONL rows must be objects")
                yield value


def _url_key(value):
    # Preserve query strings and fragments; neither is assumed legally irrelevant.
    parsed = urllib.parse.urlsplit(html.unescape(value))
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        return None
    return urllib.parse.urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path,
                                    parsed.query, parsed.fragment))


def _citation_matches(text, family=None):
    matches = []
    occupied = []
    for expression, kind in ((POLICY_RE, "policy"), (FEDERAL_RE, "federal"),
                             (NYCRR_RE, "nycrr"), (NY_LAW_RE, "nylaw")):
        for match in expression.finditer(text):
            if kind == "policy":
                key = "POLICY " + _identifier(match.group(1)).replace("-", ".")
            elif kind == "federal":
                code = re.sub(r"[.\s]", "", match.group(2)).upper()
                key = f"{match.group(1)} {code} {_identifier(match.group(3))}"
            elif kind == "nycrr":
                key = f"{match.group(1)} NYCRR {_identifier(match.group(2))}"
            else:
                name = re.sub(r"\s+", " ", match.group(1)).lower()
                code = "PHL" if name in {"phl", "public health law"} else "SSL"
                key = f"NY {code} {_identifier(match.group(2))}"
            matches.append((match.start(), match.end(), key, kind))
            occupied.append(match.span())
    if family:
        for match in BARE_SECTION_RE.finditer(text):
            if not any(a <= match.start() < b for a, b in occupied):
                matches.append((match.start(), match.end(), f"{family} {_identifier(match.group(1))}", "contextual_section"))
    return sorted(matches)


def _identifier(value):
    return re.sub(r"\s+", "", value.translate(str.maketrans({c: "-" for c in DASHES}))).lower()


def _family(doc):
    title = " ".join(str(doc.get(k) or "") for k in ("title", "extracted_title"))
    federal = FEDERAL_RE.search(title)
    if federal:
        return f"{federal.group(1)} {re.sub(r'[.\s]', '', federal.group(2)).upper()}"
    parsed = urllib.parse.urlsplit(doc.get("resolved_url") or doc.get("source_url") or "")
    if parsed.hostname and parsed.hostname.endswith("ecfr.gov"):
        modern = re.search(r"/title-(\d+)(?:/|$)", parsed.path)
        old = re.search(r"(?:se|pt)(\d+)\.", urllib.parse.parse_qs(parsed.query).get("node", [""])[0])
        if modern or old:
            return f"{(modern or old).group(1)} CFR"
    return None


def _aliases(doc):
    aliases = set()
    for key in ("title", "extracted_title"):
        aliases.update(m[2] for m in _citation_matches(doc.get(key) or "", _family(doc)))
    return aliases


def _semantic_kind(word):
    word = word.lower()
    if word.startswith(("supersed", "replac", "rescind", "withdraw")):
        return "supersedes"
    if word.startswith("amend"):
        return "amends"
    if word.startswith("clarif"):
        return "clarifies"
    if word.startswith("correct"):
        return "corrects"
    return "conflicts_with"


def _evidence(section, start, end):
    return {"section_id": section["section_id"], "start": start, "end": end,
            "text_sha256": section["text_sha256"]}


def _base_payload(documents, sections, relationships):
    return {"documents": [(k, {field: value for field, value in d.items() if field != "text"})
                          for k, d in sorted(documents.items())],
            "sections": [(k, {field: value for field, value in s.items() if field != "text"})
                         for k, s in sorted(sections.items())],
            "relationships": [relationships[k] for k in sorted(relationships)]}


def _resolve_citation(alias, alias_targets, provision_targets):
    """Prefer direct source identities, then explicit provision headings.

    A subsection citation can resolve to its containing recorded provision while
    retaining the original locator. Never bind merely because two titles overlap.
    """
    keys = [alias]
    while re.search(r"\([a-z0-9]+\)$", keys[-1]):
        keys.append(re.sub(r"\([a-z0-9]+\)$", "", keys[-1]))
    for key in keys:
        if alias_targets.get(key):
            return set(alias_targets[key]), set(), key
        if provision_targets.get(key):
            hits = provision_targets[key]
            return {doc_id for doc_id, _ in hits}, {section_id for _, section_id in hits}, key
    return set(), set(), alias


def _extract(section, doc, url_targets, alias_targets, provision_targets):
    text = section["text"]
    refs = []
    for match in URL_RE.finditer(text):
        value = match.group().rstrip(".,;:)")
        end = match.start() + len(value)
        refs.append((match.start(), end, value, "url", set(url_targets.get(_url_key(value), ())), set(), None))
    for start, end, alias, kind in _citation_matches(text, _family(doc)):
        targets, target_sections, matched_alias = _resolve_citation(alias, alias_targets, provision_targets)
        refs.append((start, end, text[start:end], kind, targets, target_sections, matched_alias))
    semantic = list(SEMANTIC_RE.finditer(text))
    consumed_signals = set()
    for start, end, reference, reference_kind, targets, target_sections, matched_alias in sorted(refs, key=lambda r: (r[0], r[1], r[3])):
        # Only same-paragraph language supplies a semantic candidate, never a fact.
        paragraph_start = text.rfind("\n\n", 0, start) + 2
        paragraph_end = text.find("\n\n", end)
        paragraph_end = len(text) if paragraph_end == -1 else paragraph_end
        nearby = [(abs(s.start() - start), index, s) for index, s in enumerate(semantic)
                  if paragraph_start <= s.start() < paragraph_end and abs(s.start() - start) <= 240]
        relation_type = "references"
        proof_start, proof_end = max(0, start - 100), min(len(text), end + 160)
        if nearby:
            _, index, signal = min(nearby, key=lambda x: x[0])
            consumed_signals.add(index)
            relation_type = _semantic_kind(signal.group())
            proof_start = max(paragraph_start, min(start, signal.start()) - 100)
            proof_end = min(paragraph_end, max(end, signal.end()) + 160)
        target = next(iter(targets)) if len(targets) == 1 else None
        status = ("verified_reference" if target and relation_type == "references"
                  else "candidate" if target else "unresolved")
        qualifiers = {"reference_kind": reference_kind,
                      "resolution": "unique_exact_recorded_identity" if target else "ambiguous" if targets else "not_in_snapshot",
                      "possible_target_document_ids": sorted(targets),
                      "possible_target_section_ids": sorted(target_sections),
                      "target_section_ids": sorted(target_sections) if target else [],
                      "target_section_id": next(iter(target_sections)) if target and len(target_sections) == 1 else None,
                      "matched_citation_identity": matched_alias,
                      "self_reference": target == doc["document_id"],
                      "direction_unverified": relation_type != "references",
                      "semantic_correctness_verified": False,
                      "effective_date": None,
                      "date_basis": None}
        record = {"source_document_id": doc["document_id"], "target_document_id": target,
                  "relation_type": relation_type, "status": status, "target_reference": reference,
                  "evidence": [_evidence(section, proof_start, proof_end)], "qualifiers": qualifiers}
        record["relationship_id"] = "rel_" + base.digest(base.canonical(record))[:32]
        yield record
    for index, signal in enumerate(semantic):
        if index in consumed_signals:
            continue
        record = {"source_document_id": doc["document_id"], "target_document_id": None,
                  "relation_type": _semantic_kind(signal.group()), "status": "unresolved",
                  "target_reference": "", "evidence": [_evidence(section, max(0, signal.start() - 160), min(len(text), signal.end() + 240))],
                  "qualifiers": {"resolution": "target_not_identified", "matched_text": signal.group(),
                                 "direction_unverified": True, "semantic_correctness_verified": False,
                                 "effective_date": None, "date_basis": None}}
        record["relationship_id"] = "rel_" + base.digest(base.canonical(record))[:32]
        yield record


class GraphStore:
    def __init__(self, path: Path, expected_snapshot: str | None = None):
        path = Path(path)
        if not path.is_file():
            raise HarnessError("Relationship graph does not exist; build it locally first")
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys=ON")
        self._record_pins = {}
        self._verification_token = None
        self._summary_cache = None
        self._checking_integrity = False
        try:
            self.metadata = {row[0]: json.loads(row[1]) for row in self.connection.execute("SELECT key,value FROM metadata")}
            if self.metadata.get("schema_version") != SCHEMA_VERSION:
                raise HarnessError("Unsupported relationship graph schema")
            if expected_snapshot and expected_snapshot != self.metadata["snapshot_sha256"]:
                raise HarnessError("Relationship graph belongs to a different corpus snapshot")
            self._ensure_integrity()
        except Exception:
            self.connection.close()
            raise

    def close(self):
        self.connection.close()

    def _get(self, table, identity, column):
        self._ensure_integrity()
        row = self.connection.execute(f"SELECT record_json FROM {table} WHERE {column}=?", (identity,)).fetchone()
        if not row:
            raise HarnessError(f"Unknown graph {table} identity")
        item = json.loads(row[0])
        payload = {key: value for key, value in item.items() if key != "text"} if table != "relationships" else item
        if base.digest(base.canonical(payload)) != self._record_pins.get((table, identity)):
            raise HarnessError("Graph record differs from its pinned canonical base")
        return item

    def _state_token(self):
        return (self.connection.execute("PRAGMA data_version").fetchone()[0], self.connection.total_changes)

    def _ensure_integrity(self):
        if self._checking_integrity:
            return
        token = self._state_token()
        if token == self._verification_token:
            return
        self._checking_integrity = True
        try:
            for _ in range(3):
                before = self._state_token()
                self._verify_base()
                self._verify_ledger()
                after = self._state_token()
                if before == after:
                    self._verification_token = after
                    break
            else:
                raise HarnessError("Graph changed repeatedly during integrity verification; retry after writers finish")
            self._summary_cache = None
        finally:
            self._checking_integrity = False

    def _verify_base(self):
        values, pins = {}, {}
        for table, column in (("documents", "document_id"), ("sections", "section_id"), ("relationships", "relationship_id")):
            records = {}
            for row in self.connection.execute(f"SELECT {column},record_json FROM {table} ORDER BY {column}"):
                item = json.loads(row[1])
                if item.get(column) != row[0]:
                    raise HarnessError("Graph base record identity differs from its database key")
                records[row[0]] = item
                payload = {key: value for key, value in item.items() if key != "text"} if table != "relationships" else item
                pins[(table, row[0])] = base.digest(base.canonical(payload))
                if table != "relationships" and item.get("text_sha256") and base.digest(item.get("text", "")) != item["text_sha256"]:
                    raise HarnessError("Graph canonical base text hash mismatch")
            values[table] = records
        if self.metadata.get("integrity_algorithm") != "canonical_metadata_v1":
            raise HarnessError("Graph integrity manifest is obsolete; rebuild from the canonical corpus into a new graph")
        actual = base.digest(base.canonical(_base_payload(values["documents"], values["sections"], values["relationships"])))
        if actual != self.metadata["graph_base_sha256"]:
            raise HarnessError("Graph canonical base digest mismatch")
        source_snapshot = base.hashlib.sha256()
        for doc_id, document in sorted(values["documents"].items()):
            canonical_metadata = base.canonical({key: value for key, value in document.items() if key != "text"})
            source_snapshot.update(doc_id.encode("utf-8") + b"\0" + canonical_metadata.encode("utf-8") + b"\n")
        if source_snapshot.hexdigest() != self.metadata["snapshot_sha256"]:
            raise HarnessError("Graph document metadata does not reproduce the pinned corpus snapshot")
        if self._record_pins and pins != self._record_pins:
            raise HarnessError("Graph base records changed during this session")
        self._record_pins = pins

    def _verify_ledger(self):
        previous = None
        for row in self.connection.execute("SELECT record_json FROM reviews ORDER BY sequence"):
            record = json.loads(row[0])
            try:
                original = self.relationship(record["relationship_id"])
                verdict = record["decision"]["decision"]
                actor_type = record["actor_type"]
                status = ("verified" if actor_type == "human" else "model_reviewed") if verdict == "accept" else (
                    "rejected" if actor_type == "human" else "model_rejected") if verdict == "reject" else "unresolved"
                if (actor_type not in {"human", "model"} or verdict not in {"accept", "reject", "uncertain"}
                        or record["resulting_status"] != status
                        or record["snapshot_sha256"] != self.metadata["snapshot_sha256"]
                        or record["candidate_sha256"] != base.digest(base.canonical(original))
                        or record["previous_review_sha256"] != previous
                        or record["review_id"] != "review_" + base.digest(base.canonical({k: v for k, v in record.items() if k != "review_id"}))):
                    raise HarnessError("Graph review ledger identity, status, or hash chain is invalid")
            except (KeyError, TypeError) as exc:
                raise HarnessError("Malformed graph review ledger record") from exc
            previous = base.digest(row[0])

    def document(self, document_id):
        item = self._get("documents", document_id, "document_id")
        if item.get("text_sha256") and base.digest(item.get("text", "")) != item["text_sha256"]:
            raise HarnessError("Graph canonical document text hash mismatch")
        return item

    def section(self, section_id):
        item = self._get("sections", section_id, "section_id")
        if base.digest(item["text"]) != item["text_sha256"]:
            raise HarnessError("Graph canonical section text hash mismatch")
        return item

    def sections_for_document(self, document_id):
        self.document(document_id)
        return [self.section(row[0]) for row in self.connection.execute(
            "SELECT section_id FROM sections WHERE document_id=? ORDER BY section_index,section_id", (document_id,))]

    def relationship(self, relationship_id):
        return self._get("relationships", relationship_id, "relationship_id")

    def reviews_for(self, relationship_id):
        self.relationship(relationship_id)
        return [json.loads(row[0]) for row in self.connection.execute(
            "SELECT record_json FROM reviews WHERE relationship_id=? ORDER BY sequence", (relationship_id,))]

    def _effective(self, original):
        reviews = self.reviews_for(original["relationship_id"])
        item = dict(original)
        for review in reviews:
            decision = review["decision"]
            for key in ("source_document_id", "target_document_id", "relation_type", "evidence", "qualifiers"):
                if key in decision:
                    item[key] = decision[key]
            item["status"] = review["resulting_status"]
            item["latest_review_id"] = review["review_id"]
        return item

    def relationships_for(self, document_id, include_candidates=True):
        self.document(document_id)
        # Reviews can resolve or reverse an edge, so filter after applying the ledger.
        result = []
        for row in self.connection.execute("SELECT record_json FROM relationships ORDER BY relationship_id"):
            item = self._effective(json.loads(row[0]))
            if document_id not in {item["source_document_id"], item["target_document_id"]}:
                continue
            if item["status"] in {"rejected", "model_rejected"}:
                continue
            if not include_candidates and item["status"] not in {"verified", "verified_reference"}:
                continue
            item["direction"] = "outgoing" if item["source_document_id"] == document_id else "incoming"
            result.append(item)
        return result

    def candidates(self, limit=100, offset=0, include_reviewed=False):
        if type(limit) is not int or not 1 <= limit <= 100000 or type(offset) is not int or offset < 0:
            raise HarnessError("Invalid graph candidate page bounds")
        result = []
        for row in self.connection.execute("SELECT record_json FROM relationships ORDER BY relationship_id"):
            original = json.loads(row[0])
            if not include_reviewed and self.reviews_for(original["relationship_id"]):
                continue
            effective = self._effective(original)
            if effective["status"] in {"candidate", "unresolved"}:
                result.append(original)
        result.sort(key=lambda r: (r["target_document_id"] is None, r["relation_type"] == "references", r["relationship_id"]))
        return result[offset:offset + limit]

    def validate_relationship(self, record):
        if record.get("relation_type") not in RELATION_TYPES or record.get("status") not in STATUSES:
            raise HarnessError("Unsupported graph relationship type or status")
        for doc_id in (record["source_document_id"], record.get("target_document_id")):
            if doc_id and not self.connection.execute("SELECT 1 FROM documents WHERE document_id=?", (doc_id,)).fetchone():
                raise HarnessError("Relationship endpoint is outside the corpus snapshot")
        refs = record.get("evidence")
        if not isinstance(refs, list) or not refs:
            raise HarnessError("Relationship needs canonical supporting evidence")
        for ref in refs:
            section = self.section(ref.get("section_id"))
            start, end = ref.get("start"), ref.get("end")
            if (type(start) is not int or type(end) is not int or not 0 <= start < end <= len(section["text"])
                    or not section["text"][start:end].strip() or ref.get("text_sha256") != section["text_sha256"]):
                raise HarnessError("Relationship evidence does not select an exact canonical section span")
            if "text" in ref and ref["text"] != section["text"][start:end]:
                raise HarnessError("Relationship evidence quotation differs from canonical text")
        qualifiers = record.get("qualifiers", {})
        if not isinstance(qualifiers, dict):
            raise HarnessError("Relationship qualifiers must be an object")
        if record["relation_type"] != "references" and record["status"] == "verified_reference":
            raise HarnessError("Structural reference verification cannot verify an amendment")
        if qualifiers.get("date_basis") in {"last_modified", "metadata_last_modified", "pdf_modification_date_raw", "http_last_modified_raw", "metadata_proxy"} and record["status"] == "verified":
            raise HarnessError("Metadata date proxies cannot establish a verified semantic relationship")

    def record_review(self, relationship_id, decision, reviewer, review_kind="model_review", actor_type="model",
                      expected_relationship_sha256=None, expected_snapshot_sha256=None):
        self._ensure_integrity()
        original = self.relationship(relationship_id)
        if expected_snapshot_sha256 and expected_snapshot_sha256 != self.metadata["snapshot_sha256"]:
            raise HarnessError("Review corpus snapshot is stale")
        expected = expected_relationship_sha256
        if expected and expected != base.digest(base.canonical(original)):
            raise HarnessError("Review relationship identity or evidence has changed")
        decision = json.loads(base.canonical(decision))
        if not isinstance(decision, dict) or decision.get("decision") not in {"accept", "reject", "uncertain"}:
            raise HarnessError("Review decision must be accept, reject, or uncertain")
        if actor_type not in {"human", "model"} or not isinstance(reviewer, str) or not reviewer.strip():
            raise HarnessError("Review requires an explicit human or model actor label")
        proposed = self._effective(original)
        proposed.update({key: decision[key] for key in ("source_document_id", "target_document_id", "relation_type", "evidence", "qualifiers") if key in decision})
        verdict = decision["decision"]
        status = ("verified" if actor_type == "human" else "model_reviewed") if verdict == "accept" else (
            "rejected" if actor_type == "human" else "model_rejected") if verdict == "reject" else "unresolved"
        proposed["status"] = status
        self.validate_relationship(proposed)
        if verdict == "accept" and not proposed.get("target_document_id"):
            raise HarnessError("An accepted relationship must resolve a target document")
        if actor_type == "model" and decision.get("status") in {"verified", "human_verified", "verified_reference"}:
            raise HarnessError("Model review cannot claim human or deterministic verification")
        if actor_type == "model" and isinstance(decision.get("qualifiers"), dict):
            decision["qualifiers"]["semantic_correctness_verified"] = False
        # Reserve the ledger append before reading its previous hash, avoiding forks
        # when independent review workers write to the same local graph.
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            self._ensure_integrity()
            verified_data_version = self._state_token()[0]
            previous = self.connection.execute("SELECT record_json FROM reviews ORDER BY sequence DESC LIMIT 1").fetchone()
            record = {"relationship_id": relationship_id, "decision": decision,
                      "reviewer": reviewer, "actor_type": actor_type, "review_kind": review_kind,
                      "created_at": datetime.now(timezone.utc).isoformat(), "resulting_status": status,
                      "candidate_sha256": base.digest(base.canonical(original)),
                      "snapshot_sha256": self.metadata["snapshot_sha256"],
                      "previous_review_sha256": base.digest(previous[0]) if previous else None}
            record["review_id"] = "review_" + base.digest(base.canonical(record))
            self.connection.execute("INSERT INTO reviews(relationship_id,record_json) VALUES(?,?)", (relationship_id, base.canonical(record)))
            self.connection.commit()
            # Preserve the version observed under the write lock. An external
            # commit immediately afterward must invalidate the next cache read.
            self._verification_token = (verified_data_version, self.connection.total_changes)
            self._summary_cache = None
        except Exception:
            self.connection.rollback()
            raise
        return record

    def summary(self):
        self._ensure_integrity()
        if self._summary_cache is not None:
            return json.loads(base.canonical(self._summary_cache))
        statuses, types = Counter(), Counter()
        for row in self.connection.execute("SELECT record_json FROM relationships"):
            item = self._effective(json.loads(row[0]))
            statuses[item["status"]] += 1
            types[item["relation_type"]] += 1
        reviews = [row[0] for row in self.connection.execute("SELECT record_json FROM reviews ORDER BY sequence")]
        result = {**self.metadata["coverage"], "schema_version": SCHEMA_VERSION,
                "snapshot_sha256": self.metadata["snapshot_sha256"],
                "graph_content_sha256": base.digest(base.canonical({"base": self.metadata["graph_base_sha256"], "reviews": reviews})),
                "relationship_status_counts": dict(sorted(statuses.items())),
                "relationship_type_counts": dict(sorted(types.items())), "review_count": len(reviews),
                "semantic_completeness_verified": False,
                "limitations": ["Textual references and lexical relationship signals only; embedded links not present in canonical text may be missed.",
                                "A verified reference establishes source-to-target identity only, not authority, applicability, or legal currentness.",
                                "Unavailable source contents and unresolved targets cannot be assessed; candidate/model-reviewed edges remain evidence leads."]}
        if self._state_token() != self._verification_token:
            raise HarnessError("Graph changed while summarizing; retry after writers finish")
        self._summary_cache = result
        return json.loads(base.canonical(result))


def build_graph(corpus, documents_path: Path, sections_path: Path, output: Path):
    """Build atomically from the entire canonical snapshot, with no network calls."""
    output = Path(output)
    if output.exists():
        raise HarnessError("Graph output already exists; choose a new path to preserve the review ledger")
    canonical = {row[0]: json.loads(row[1]) for row in corpus.connection.execute("SELECT document_id,metadata_json FROM documents")}
    documents = {}
    for doc in _rows(documents_path):
        doc_id = doc.get("document_id")
        if doc_id in documents or doc_id not in canonical:
            raise HarnessError("Duplicate or out-of-snapshot graph source document")
        if {key: value for key, value in doc.items() if key != "text"} != canonical[doc_id]:
            raise HarnessError("Graph source metadata differs from the pinned corpus database")
        if doc.get("text_sha256") and base.digest(doc.get("text", "")) != doc["text_sha256"]:
            raise HarnessError("Graph document text hash differs from canonical metadata")
        documents[doc_id] = doc
    if set(documents) != set(canonical):
        raise HarnessError("Graph input omits corpus source records")
    sections = {}
    for section in _rows(sections_path):
        doc_id, section_id = section.get("document_id"), section.get("section_id")
        if doc_id not in documents or section_id in sections:
            raise HarnessError("Graph section identity or document lineage is invalid")
        text, start, end = section.get("text"), section.get("document_start_char"), section.get("document_end_char")
        if (not isinstance(text, str) or type(start) is not int or type(end) is not int
                or not 0 <= start <= end <= len(documents[doc_id].get("text", ""))
                or documents[doc_id]["text"][start:end] != text or base.digest(text) != section.get("text_sha256")
                or section.get("raw_sha256") != documents[doc_id].get("raw_sha256")):
            raise HarnessError("Graph section text, offsets, or raw source identity differs from canonical document")
        sections[section_id] = section
    section_counts = Counter(s["document_id"] for s in sections.values())
    for doc_id, doc in documents.items():
        if doc.get("section_count") is not None and doc["section_count"] != section_counts[doc_id]:
            raise HarnessError("Graph input does not contain every canonical section")
    chunk_count = 0
    for row in corpus.connection.execute("SELECT c.chunk_id,c.metadata_json,f.text FROM chunks c JOIN chunks_fts f ON f.chunk_id=c.chunk_id"):
        chunk = json.loads(row[1])
        section = sections.get(chunk.get("section_id"))
        if not section or section["document_id"] != chunk["document_id"]:
            raise HarnessError("Canonical chunk has no matching graph section")
        left, right = chunk["section_start_char"], chunk["section_end_char"]
        if section["text"][left:right] != row[2] or base.digest(row[2]) != chunk["text_sha256"]:
            raise HarnessError("Graph section does not reproduce canonical chunk text")
        chunk_count += 1
    url_targets, alias_targets, provision_targets = defaultdict(set), defaultdict(set), defaultdict(set)
    for doc in documents.values():
        for key in ("source_url", "resolved_url"):
            if doc.get(key) and _url_key(doc[key]):
                url_targets[_url_key(doc[key])].add(doc["document_id"])
        for alias in _aliases(doc):
            alias_targets[alias].add(doc["document_id"])
    for section in sections.values():
        doc = documents[section["document_id"]]
        headers = list(section.get("heading_path") or [])
        first_line = section["text"].split("\n", 1)[0].strip()
        if len(first_line) <= 240 and re.match(r"^(?:§|\d+\s*(?:C\.?F\.?R|U\.?S\.?C))", first_line, re.I):
            headers.append(first_line)
        for header in headers:
            for _, _, alias, _ in _citation_matches(header, _family(doc)):
                provision_targets[alias].add((section["document_id"], section["section_id"]))
    relationships = {}
    for section in sections.values():
        for record in _extract(section, documents[section["document_id"]], url_targets, alias_targets, provision_targets):
            relationships[record["relationship_id"]] = record
    coverage = {"document_count": len(documents), "section_count": len(sections), "sections_scanned": len(sections),
                "canonical_chunk_count": chunk_count, "relationship_count": len(relationships),
                "document_processing_status_counts": dict(sorted(Counter(d.get("processing_status", "unknown") for d in documents.values()).items())),
                "documents_without_text": sum(not d.get("text", "").strip() for d in documents.values()),
                "all_snapshot_documents_accounted_for": True, "all_canonical_sections_scanned": True}
    graph_base = _base_payload(documents, sections, relationships)
    metadata = {"schema_version": SCHEMA_VERSION, "snapshot_sha256": corpus.summary()["snapshot_sha256"],
                "graph_base_sha256": base.digest(base.canonical(graph_base)), "coverage": coverage,
                "integrity_algorithm": "canonical_metadata_v1"}
    output.parent.mkdir(parents=True, exist_ok=True)
    temp = tempfile.NamedTemporaryFile(prefix=".graph-", suffix=".sqlite", dir=output.parent, delete=False)
    temporary = Path(temp.name)
    temp.close()
    con = None
    try:
        con = sqlite3.connect(temporary)
        con.executescript("CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);"
                          "CREATE TABLE documents(document_id TEXT PRIMARY KEY,record_json TEXT NOT NULL);"
                          "CREATE TABLE sections(section_id TEXT PRIMARY KEY,document_id TEXT NOT NULL REFERENCES documents(document_id),section_index INTEGER,record_json TEXT NOT NULL);"
                          "CREATE INDEX section_document ON sections(document_id,section_index);"
                          "CREATE TABLE relationships(relationship_id TEXT PRIMARY KEY,source_document_id TEXT NOT NULL REFERENCES documents(document_id),target_document_id TEXT REFERENCES documents(document_id),record_json TEXT NOT NULL);"
                          "CREATE INDEX relationship_source ON relationships(source_document_id);"
                          "CREATE INDEX relationship_target ON relationships(target_document_id);"
                          "CREATE TABLE reviews(sequence INTEGER PRIMARY KEY AUTOINCREMENT,relationship_id TEXT NOT NULL REFERENCES relationships(relationship_id),record_json TEXT NOT NULL);"
                          "CREATE INDEX review_relationship ON reviews(relationship_id,sequence);"
                          "CREATE TRIGGER reviews_no_update BEFORE UPDATE ON reviews BEGIN SELECT RAISE(ABORT,'reviews are append-only'); END;"
                          "CREATE TRIGGER reviews_no_delete BEFORE DELETE ON reviews BEGIN SELECT RAISE(ABORT,'reviews are append-only'); END;")
        con.executemany("INSERT INTO metadata VALUES(?,?)", [(k, base.canonical(v)) for k, v in metadata.items()])
        con.executemany("INSERT INTO documents VALUES(?,?)", [(k, base.canonical(v)) for k, v in documents.items()])
        con.executemany("INSERT INTO sections VALUES(?,?,?,?)", [(k, s["document_id"], s.get("section_index", 0), base.canonical(s)) for k, s in sections.items()])
        con.executemany("INSERT INTO relationships VALUES(?,?,?,?)", [(k, r["source_document_id"], r["target_document_id"], base.canonical(r)) for k, r in relationships.items()])
        con.commit()
        con.close()
        con = None
        graph = GraphStore(temporary, metadata["snapshot_sha256"])
        try:
            for record in relationships.values():
                graph.validate_relationship(record)
            report = graph.summary()
        finally:
            graph.close()
        temporary.replace(output)
        return report
    finally:
        if con is not None:
            con.close()
        temporary.unlink(missing_ok=True)
