#!/usr/bin/env python3
"""Select one traceable representation per exact customer-listed URL."""
from collections import defaultdict
import copy
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit
from lxml import html
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]


def read(path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main():
    folder = ROOT / "manifests"
    inventory = read(folder / "customer_unique_sources.jsonl")
    # The supplied PDF is the only source-selection authority. Never use a
    # previous corpus, a matching citation, or a similar filename as an allowlist.
    pdf_path = ROOT / "reference/customer-resource-links.pdf"
    pdf_hash = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
    pdf_urls = set()
    for page in PdfReader(pdf_path).pages:
        for ref in page.get("/Annots", []):
            action = ref.get_object().get("/A")
            if action and action.get_object().get("/S") == "/URI":
                pdf_urls.add(str(action.get_object()["/URI"]))
    if {r["source_url_exact"] for r in inventory} != pdf_urls:
        raise ValueError("Inventory URL set differs from the supplied PDF")
    if len(inventory) != len(pdf_urls):
        raise ValueError("Inventory must have exactly one row per distinct PDF URI")
    allowed = {r["source_id"]: r for r in inventory}
    for r in inventory:
        expected = "customer_" + hashlib.sha256(r["source_url_exact"].encode()).hexdigest()[:12]
        if r["source_id"] != expected or r["source_document_sha256"] != pdf_hash:
            raise ValueError("Inventory source ID or PDF identity mismatch")
    choices = defaultdict(list)
    batches = ["customer_direct_acquisition.jsonl", "customer_nydoh_recovery_acquisition.jsonl", "customer_fulltext_acquisition.jsonl"]
    for name in batches:
        for record in read(folder / name):
            if record["source_id"] not in allowed or record["source_url"] != allowed[record["source_id"]]["source_url_exact"]:
                raise ValueError("Acquisition contains a source outside the exact PDF allowlist")
            choices[record["source_id"]].append((name, record))
    merged = []
    for item in inventory:
        options = choices.get(item["source_id"], [])
        if not options:
            raise ValueError("Customer target lacks an acquisition attempt: " + item["source_id"])
        successes = [(name, r) for name, r in options if r.get("status") == "downloaded"]
        selected_batch, chosen = (successes or options)[-1]
        record = copy.deepcopy(chosen)
        assert record["source_url"] == item["source_url_exact"]
        record.update(title=item["title"], customer_reference_ids=item["customer_ref_ids"],
                      source_origin="customer_pdf_link", customer_labels=item["labels"],
                      customer_pdf_pages=item["pdf_pages"], customer_link_occurrences=item["occurrences"])
        record["acquisition_representations"] = [
            {"batch": name, **{k: r.get(k) for k in ["status", "source_url", "fetch_url", "resolved_url", "raw_path", "sha256", "media_type", "retrieved_at", "failure_reason", "attempts"]}}
            for name, r in options
        ]
        attempts = []
        for _, r in options:
            for attempt in r.get("attempts", []):
                if attempt not in attempts:
                    attempts.append(attempt)
        record["attempts"] = attempts
        meta = record.setdefault("metadata", {})
        meta.update(customer_inventory_status="explicitly_listed", customer_reference_ids=item["customer_ref_ids"],
                    customer_listed_url=item["source_url_exact"], customer_pdf_sha256=item["source_document_sha256"],
                    source_list_reconciliation="customer_link_inventory_reconciled",
                    resource_type_expected=item["resource_type_expected"],
                    resource_subtype_expected=item["resource_subtype_expected"],
                    customer_historical_document_bytes_supplied=False,
                    version_resolution_status="customer_supplied_links_not_historical_document_bytes",
                    operative_status="not_determined", selected_acquisition_batch=selected_batch)
        flags = list(meta.get("review_flags", []))
        collection = item["resource_type_expected"] == "collection"
        actual_regulatory_body = False
        headings = []
        if record.get("status") == "downloaded" and record.get("media_type") == "text/html":
            tree = html.fromstring((ROOT / record["raw_path"]).read_bytes())
            headings = [" ".join(n.text_content().split()) for n in tree.xpath('//h1|//h2')][:8]
            if "ecfr.gov" in urlsplit(record.get("resolved_url", "")).netloc:
                actual_regulatory_body = bool(tree.xpath("//div[contains(concat(' ',normalize-space(@class),' '),' part ') or contains(concat(' ',normalize-space(@class),' '),' subpart ') or contains(concat(' ',normalize-space(@class),' '),' section ')]"))
        meta["observed_page_headings"] = headings
        if actual_regulatory_body:
            collection = False
            meta["observed_representation"] = "regulatory_text_container"
        if item["resource_subtype_expected"] in {"contact_directory", "consumer_information_directory"}:
            collection = False
            meta["observed_representation"] = "customer_selected_reference_directory"
        if record["source_url"].lower().endswith("/pub100_18.pdf"):
            collection = True
            flags.append("manual_table_of_contents_not_manual_chapter_text")
        if record.get("status") != "downloaded":
            meta["target_resolution_status"] = "unavailable_after_recorded_attempts"
            flags.append("customer_target_not_acquired")
        elif selected_batch == "customer_fulltext_acquisition.jsonl":
            meta["target_resolution_status"] = "observed_publisher_full_text_link_followed"
        elif selected_batch == "customer_nydoh_recovery_acquisition.jsonl":
            meta["target_resolution_status"] = "public_nydoh_alternate_host_same_path"
            flags.append("alternate_host_delivery_recorded_not_assumed_identical_to_customer_snapshot")
        else:
            meta["target_resolution_status"] = ("listed_url_fetched" if record.get("resolved_url") == record["source_url"] else "listed_url_redirect_followed")
        if record.get("resolved_url", "").rstrip("/") == "https://www.ecfr.gov/current/title-42":
            collection = True
            meta["target_resolution_status"] = "legacy_subchapter_link_redirected_to_broader_title_index"
            flags.append("redirect_scope_broader_than_customer_label")
        if "codes.findlaw.com" in record["source_url"] and urlsplit(record["source_url"]).fragment:
            collection = True
            flags.append("legacy_hashbang_article_target_not_resolved_by_downloaded_index")
            meta["target_resolution_status"] = "public_health_law_index_downloaded_fragment_target_unverified"
        if item.get("label_ambiguities"):
            flags.append("customer_labels_share_one_ambiguous_target")
            meta["customer_label_ambiguities"] = item["label_ambiguities"]
        if any("Ch. VI" in label for label in item.get("labels", [])) and "/chapter-IV/" in record.get("resolved_url", ""):
            flags.append("customer_chapter_label_differs_from_resolved_url")
        if collection:
            flags.append("collection_contents_not_exhaustively_expanded")
        record["source_role"] = "collection_index" if collection else "customer_selected_document"
        record["source_kind"] = "collection_index" if collection else "direct_document"
        meta["review_flags"] = sorted(set(flags))
        meta["requires_human_review"] = bool(flags)
        record["source_list_match"] = "explicit_customer_url_with_recorded_representation"
        merged.append(record)
    assert len(merged) == len(inventory) == len({r["source_url"] for r in merged})
    (folder / "acquisition.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in sorted(merged, key=lambda x:x["source_id"])), encoding="utf-8")
    from collections import Counter
    print(json.dumps({"customer_targets": len(merged), "statuses": dict(Counter(r["status"] for r in merged)),
                      "selected_batches": dict(Counter(r["metadata"]["selected_acquisition_batch"] for r in merged))}))


if __name__ == "__main__":
    main()
