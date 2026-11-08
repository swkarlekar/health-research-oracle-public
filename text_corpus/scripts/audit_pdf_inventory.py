#!/usr/bin/env python3
"""Independently verify the supplied PDF's exact link inventory; no downloads."""
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import argparse
import hashlib
import json
import re

import fitz
from pypdf import PdfReader
from pypdf.generic import IndirectObject

DEFAULT_ROOT = Path(__file__).resolve().parents[1]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_rows(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def write_rows(path, values):
    path.write_text("".join(json.dumps(v, ensure_ascii=False) + "\n" for v in values), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT,
                        help="Corpus root (default: parent of the script's directory).")
    parser.add_argument("--pdf", type=Path,
                        help="Authoritative PDF (default: ROOT/reference/customer-resource-links.pdf).")
    parser.add_argument("--output-dir", type=Path,
                        help="Audit output directory (default: ROOT/audit).")
    args = parser.parse_args()
    ROOT = args.root.resolve()
    PDF = (args.pdf or ROOT / "reference/customer-resource-links.pdf").resolve()
    OUT = (args.output_dir or ROOT / "audit").resolve()
    INVENTORY = ROOT / "manifests/customer_unique_sources.jsonl"
    OCCURRENCES = ROOT / "manifests/customer_resource_links.jsonl"
    reference_copy = ROOT / "reference/customer-resource-links.pdf"
    for path in [PDF, reference_copy, INVENTORY, OCCURRENCES]:
        if not path.is_file():
            parser.error(f"Required input file is missing: {path}")
    pdf_hash = sha(PDF)
    OUT.mkdir(parents=True, exist_ok=True)
    pypdf = PdfReader(PDF)
    pymupdf = fitz.open(PDF)
    independent = []
    non_uri_annotations = []
    text_by_page = []
    colored_unlinked_spans = []
    color_counts = Counter()
    for page_no, page in enumerate(pypdf.pages, 1):
        mu_page = pymupdf[page_no - 1]
        text_by_page.append(mu_page.get_text(sort=True))
        uri_rects = []
        for annot_index, ref in enumerate(page.get("/Annots", []), 1):
            annot = ref.get_object()
            action = annot.get("/A", {})
            if hasattr(action, "get_object"):
                action = action.get_object()
            if action.get("/S") != "/URI" or "/URI" not in action:
                non_uri_annotations.append({"page": page_no, "annotation_index": annot_index, "xref": ref.idnum})
                continue
            uri = action["/URI"]
            rect = fitz.Rect([float(v) for v in annot["/Rect"]]) * mu_page.transformation_matrix
            uri_rects.append(rect)
            row = {
                "pdf_page": page_no,
                "annotation_index_on_page": annot_index,
                "annotation_xref": ref.idnum,
                "source_url_exact": str(uri),
                "uri_pdf_string_bytes_hex": uri.original_bytes.hex(),
                "clipped_label_text": " ".join(mu_page.get_textbox(rect).split()),
                "rect_pdf": [float(v) for v in annot["/Rect"]],
                "source_id": "customer_" + hashlib.sha256(str(uri).encode()).hexdigest()[:12],
                "customer_ref_id": f"customer-ref-{len(independent)+1:03d}",
            }
            independent.append(row)
        for block in mu_page.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    color_counts[span["color"]] += len(span["text"])
                    if span["color"] not in [0, 16777215] and not any(fitz.Rect(span["bbox"]).intersects(r) for r in uri_rects):
                        colored_unlinked_spans.append({"page": page_no, "color": span["color"], "text": span["text"], "bbox": span["bbox"]})

    cross_parser = []
    for page_no, page in enumerate(pymupdf, 1):
        for link in page.get_links():
            if "uri" in link:
                cross_parser.append((page_no, link["xref"], link["uri"]))
    pypdf_keys = [(r["pdf_page"], r["annotation_xref"], r["source_url_exact"]) for r in independent]

    # Inspect every object, not merely those reached by the pages' /Annots.
    all_object_uri_values = []
    unusual_actions = []
    object_errors = []
    def inspect_direct(value, xref, path):
        if isinstance(value, dict):
            for key, child in value.items():
                if str(key) == "/URI":
                    resolved = child.get_object() if hasattr(child, "get_object") else child
                    all_object_uri_values.append({"xref": xref, "path": path + str(key), "uri": str(resolved)})
                if str(key) == "/S" and str(child) in {"/JavaScript", "/Launch", "/GoToR", "/GoToE"}:
                    unusual_actions.append({"xref": xref, "path": path, "action": str(child)})
                if not isinstance(child, IndirectObject):
                    inspect_direct(child, xref, path + str(key) + "/")
        elif isinstance(value, (list, tuple)):
            for index, child in enumerate(value):
                if not isinstance(child, IndirectObject):
                    inspect_direct(child, xref, path + str(index) + "/")
    for xref in range(1, pymupdf.xref_length()):
        try:
            obj = pypdf.get_object(IndirectObject(xref, 0, pypdf))
            inspect_direct(obj, xref, "")
        except Exception as exc:
            object_errors.append({"xref": xref, "error": str(exc)})

    existing_occ = read_rows(OCCURRENCES)
    existing_inv = read_rows(INVENTORY)
    expected_occ = {(r["pdf_page"], r["annotation_index_on_page"], r["annotation_xref"]): r for r in independent}
    actual_occ = {(r["pdf_page"], r["annotation_index_on_page"], r["annotation_xref"]): r for r in existing_occ}
    exact_uri_mismatches = []
    raw_byte_mismatches = []
    pdf_hash_mismatches = []
    occurrence_reference_errors = []
    for key in expected_occ.keys() & actual_occ.keys():
        a, b = expected_occ[key], actual_occ[key]
        if a["source_url_exact"] != b.get("source_url_exact") or a["source_url_exact"] != b.get("source_url"):
            exact_uri_mismatches.append({"annotation": key, "expected": a["source_url_exact"], "actual": b.get("source_url_exact")})
        if a["uri_pdf_string_bytes_hex"] != b.get("uri_pdf_string_bytes_hex"):
            raw_byte_mismatches.append(key)
        if b.get("source_document_sha256") != pdf_hash:
            pdf_hash_mismatches.append(key)
        if a["customer_ref_id"] != b.get("customer_ref_id") or a["source_id"] != b.get("corpus_source_id"):
            occurrence_reference_errors.append({"annotation": key, "expected_ref": a["customer_ref_id"], "actual_ref": b.get("customer_ref_id"), "expected_source_id": a["source_id"], "actual_source_id": b.get("corpus_source_id")})
    grouped = defaultdict(list)
    for index, r in enumerate(independent, 1):
        r["customer_ref_id"] = f"customer-ref-{index:03d}"
        grouped[r["source_url_exact"]].append(r)
    expected_urls = set(grouped)
    actual_urls = {r["source_url_exact"] for r in existing_inv}
    grouping_errors = []
    for actual in existing_inv:
        url = actual["source_url_exact"]
        refs = grouped.get(url)
        if not refs:
            continue
        expected = {
            "source_id": refs[0]["source_id"],
            "source_url": url,
            "source_document_sha256": pdf_hash,
            "customer_ref_ids": [r["customer_ref_id"] for r in refs],
            "pdf_pages": sorted({r["pdf_page"] for r in refs}),
            "occurrence_count": len(refs),
        }
        for key, value in expected.items():
            if actual.get(key) != value:
                grouping_errors.append({"source_url_exact": url, "field": key, "expected": value, "actual": actual.get(key)})
    literals = re.findall(r"https?://[^\s<>]+", "\n".join(text_by_page))
    additional_object_urls = sorted({r["uri"] for r in all_object_uri_values} - expected_urls)
    inventory_counts = Counter(r["source_url_exact"] for r in existing_inv)
    occurrence_counts = Counter((r["pdf_page"], r["annotation_index_on_page"], r["annotation_xref"]) for r in existing_occ)
    audit = {
        "audited_at": datetime.now(ZoneInfo("America/New_York")).isoformat(),
        "authority": "The attached Dataset and resources links.pdf only; no previous-corpus input used.",
        "pdf_sha256": pdf_hash,
        "reference_copy_sha256": sha(reference_copy),
        "pdf_pages": len(pypdf.pages),
        "pdf_external_uri_annotations": len(independent),
        "pdf_exact_unique_urls": len(expected_urls),
        "per_page_uri_annotations": dict(Counter(r["pdf_page"] for r in independent)),
        "parser_versions": {"pypdf": __import__("pypdf").__version__, "PyMuPDF": fitz.VersionBind},
        "cross_parser_annotation_sets_equal": Counter(pypdf_keys) == Counter(cross_parser),
        "external_uri_annotations_not_in_manifest": [expected_occ[k] for k in sorted(expected_occ.keys() - actual_occ.keys())],
        "manifest_occurrences_not_in_pdf": [actual_occ[k] for k in sorted(actual_occ.keys() - expected_occ.keys())],
        "exact_uri_string_mismatches": exact_uri_mismatches,
        "raw_uri_byte_mismatches": raw_byte_mismatches,
        "manifest_pdf_hash_mismatches": pdf_hash_mismatches,
        "manifest_occurrence_record_count": len(existing_occ),
        "duplicate_manifest_occurrences": [{"annotation": key, "count": count} for key,count in occurrence_counts.items() if count != 1],
        "occurrence_reference_errors": occurrence_reference_errors,
        "urls_missing_from_inventory": sorted(expected_urls - actual_urls),
        "urls_extra_in_inventory": sorted(actual_urls - expected_urls),
        "duplicate_inventory_url_records": {u:n for u,n in inventory_counts.items() if n != 1},
        "grouping_or_source_id_errors": grouping_errors,
        "object_count_scanned": pymupdf.xref_length() - 1,
        "all_object_uri_count": len(all_object_uri_values),
        "additional_uri_values_outside_annotation_inventory": additional_object_urls,
        "object_scan_errors": object_errors,
        "non_uri_annotations": non_uri_annotations,
        "unusual_actions": unusual_actions,
        "named_destinations": len(pypdf.named_destinations),
        "all_named_destinations_are_internal_heading_names": all(str(name).lstrip("/").startswith("h.") for name in pypdf.named_destinations),
        "all_named_destination_pages_valid": all(0 <= pypdf.get_destination_page_number(dest) < len(pypdf.pages) for dest in pypdf.named_destinations.values()),
        "embedded_file_names": list(pymupdf.embfile_names()),
        "visible_literal_http_urls": literals,
        "colored_text_spans_not_overlapping_links": colored_unlinked_spans,
        "manifests_read": {str(OCCURRENCES.relative_to(ROOT)): sha(OCCURRENCES), str(INVENTORY.relative_to(ROOT)): sha(INVENTORY)},
        "visual_inspection": "This script does not render pages or perform manual visual inspection. Its colored-text overlap check is automated; any separate manual review must identify the same PDF hash.",
    }
    checks = [audit["cross_parser_annotation_sets_equal"], audit["pdf_sha256"] == audit["reference_copy_sha256"], len(existing_occ) == len(independent)]
    checks += [not audit[k] for k in ["external_uri_annotations_not_in_manifest", "manifest_occurrences_not_in_pdf", "exact_uri_string_mismatches", "raw_uri_byte_mismatches", "manifest_pdf_hash_mismatches", "duplicate_manifest_occurrences", "occurrence_reference_errors", "urls_missing_from_inventory", "urls_extra_in_inventory", "duplicate_inventory_url_records", "grouping_or_source_id_errors", "additional_uri_values_outside_annotation_inventory", "object_scan_errors"]]
    audit["exact_link_inventory_audit_passed"] = all(checks)
    audit["coverage_conclusion"] = ("The exact URL and link-occurrence inventory matches the PDF with zero omitted or extra URI targets. This does not establish complete linked-resource content, collection coverage, or correct resolution of ambiguous labels."
                                    if all(checks) else "The exact link-inventory audit failed. Inspect the recorded mismatches and errors; completeness must not be claimed.")
    write_rows(OUT / "independent_pdf_occurrences.jsonl", independent)
    write_rows(OUT / "independent_pdf_urls.jsonl", [{"source_url_exact": u, "source_id": rs[0]["source_id"], "customer_ref_ids": [r["customer_ref_id"] for r in rs], "pdf_pages": sorted({r["pdf_page"] for r in rs}), "occurrence_count": len(rs)} for u,rs in grouped.items()])
    (OUT / "pdf_text_by_page.txt").write_text("\n\n".join(f"PAGE {i}\n{t}" for i,t in enumerate(text_by_page,1)), encoding="utf-8")
    (OUT / "independent_pdf_inventory_audit.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(audit, ensure_ascii=False, indent=2))
    if not all(checks):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
