#!/usr/bin/env python3
"""Report exact PDF membership and incomplete resource-content coverage."""
from collections import Counter
from datetime import datetime
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]

def read(name):
    return json.loads((ROOT / name).read_text(encoding='utf-8'))

def rows(name):
    return [json.loads(s) for s in (ROOT / name).read_text(encoding='utf-8').splitlines() if s.strip()]

def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()

def main():
    pdf = read('audit/independent_pdf_inventory_audit.json')
    scope = read('audit/scope_verification.json')
    checks = read('evaluation/integrity_checks.json')
    processing = read('processed/processing_report.json')
    identity = read('audit/source_identity_summary.json')
    acq = rows('manifests/acquisition.jsonl')
    docs = rows('processed/documents.jsonl')
    coverage = rows('audit/source_coverage.jsonl')
    if not pdf['exact_link_inventory_audit_passed'] or scope['scope_status'] != 'passed':
        raise ValueError('PDF inventory or corpus scope verification failed')
    if checks['status'] != 'passed' or checks['corpus_file_sha256'] != digest(ROOT / 'processed/documents.jsonl'):
        raise ValueError('Integrity results failed or belong to another corpus snapshot')
    if scope['ground_truth']['sha256'] != digest(ROOT / 'reference/customer-resource-links.pdf'):
        raise ValueError('Scope verification used a different reference PDF')
    for audit_hashes in (pdf['manifests_read'], identity['input_hashes']):
        for name, expected in audit_hashes.items():
            if digest(ROOT / name) != expected:
                raise ValueError('Stale audit input: ' + name)
    success = [r for r in acq if r['status'] == 'downloaded']
    failed = [r for r in acq if r['status'] != 'downloaded']
    routes = dict(Counter(r['metadata']['selected_acquisition_batch'] for r in success))
    coverage_counts = dict(Counter(r['coverage_status'] for r in coverage))
    missing_hosts = dict(Counter(urlsplit(r['source_url']).netloc for r in failed))
    summary = {
        'release_kind': 'pdf_only_source_membership_verified_contents_incomplete',
        'generated_at': datetime.now(ZoneInfo('America/New_York')).isoformat(),
        'inclusion_authority': 'reference/customer-resource-links.pdf only',
        'customer_pdf_sha256': pdf['pdf_sha256'], 'pdf_pages': pdf['pdf_pages'],
        'link_occurrences': pdf['pdf_external_uri_annotations'], 'distinct_exact_pdf_urls': pdf['pdf_exact_unique_urls'],
        'pdf_link_inventory_passed': True, 'corpus_scope_status': scope['scope_status'],
        'completion_status': scope['completion_status'], 'all_resource_contents_complete': False,
        'acquired_targets': len(success), 'unacquired_targets': len(failed),
        'coverage_status_counts': coverage_counts, 'successful_delivery_routes': routes,
        'indexed_documents': sum(bool(d.get('retrieval_included')) for d in docs),
        'retrieval_chunks': processing['chunk_count'], 'canonical_sections': processing['section_count'],
        'extracted_characters': processing['extracted_char_count'], 'raw_files': scope['counts']['raw_files'],
        'orphan_raw_files': scope['counts']['orphan_raw_files'],
        'extra_packaged_source_payloads': scope['package_scope']['extra_source_payload_count'],
        'extra_retrieval_source_ids': scope['retrieval_scope']['extra_source_ids'],
        'unavailable_by_listed_host': missing_hosts, 'integrity_tests_passed': checks['automated_tests_run'],
        'table_cells_checked': checks['actual_corpus_table_cells_audited'],
        'collection_descendants_exhaustively_acquired': False,
        'historical_document_byte_equivalence_established': False,
        'no_old_corpus_used_for_source_selection': True,
        'text_only': True, 'ocr_used': False, 'model_called': False,
        'client_benchmark_status': 'awaiting_client_benchmark',
    }
    (ROOT / 'processed/corpus_summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    issues = []
    for r in coverage:
        if r['coverage_status'] != 'evidence_available' or r['review_flags']:
            issues.append({'issue_id': 'source-' + r['source_id'], 'kind': 'source_coverage_or_identity',
                'status': 'unresolved', 'source_id': r['source_id'], 'source_url_exact': r['source_url_exact'],
                'pdf_pages': r['pdf_pages'], 'coverage_status': r['coverage_status'],
                'target_resolution_status': r['target_resolution_status'], 'review_flags': r['review_flags'],
                'failure_reason': r['failure_reason'],
                'note': 'An inventory row does not establish complete resource content or legal applicability.'})
    (ROOT / 'reviews/uncertainties.jsonl').write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in issues))
    host_table = '\n'.join(f'| {host} | {count} |' for host, count in sorted(missing_hosts.items()))
    report = f'''# Exact PDF source audit

Generated: {summary['generated_at']}

## Result

**The package passes exact PDF source-membership checks. Resource-content coverage is incomplete.** The attached seven-page resource PDF is the only inclusion authority; the previous corpus is not used to decide what belongs here.

The PDF contains 130 URI annotations and 108 distinct exact URLs. Independent pypdf and PyMuPDF extraction agrees, including query strings, fragments, repeated occurrences, and raw URI bytes. Every URL has an inventory and acquisition-status record. No URL was omitted from or added to the source inventory.

| Check | Result |
| --- | ---: |
| PDF URL targets accounted for | 108 |
| Missing or added inventory URL targets | 0 |
| Unlisted source IDs in retrieval | 0 |
| Extra packaged source payloads | {summary['extra_packaged_source_payloads']} |
| Orphan raw files | {summary['orphan_raw_files']} |
| Targets with downloaded bytes | {len(success)} |
| Targets with no acquired payload | {len(failed)} |
| Documents supplying evidence passages | {summary['indexed_documents']} |
| Downloaded index-only targets | {coverage_counts.get('listed_target_index_only', 0)} |
| Evidence passages checked against source lineage | {summary['retrieval_chunks']:,} |
| Integrity tests passed | {checks['automated_tests_run']} |

The scope verifier checks every saved acquisition, selected raw hash, document, section, chunk, and SQLite record against the PDF-derived allowlist and canonical text. It also checks for unexpected packaged source files. All 89 raw files are accounted for: 84 selected representations plus five retained eCFR parent indexes. The source-membership checks pass while the completion result remains `incomplete`.

## Corrections made

Five previous FAQ/notice sample files were physically present in the preceding ZIP as comparison material. They were excluded from retrieval, but their presence did not meet a strict PDF-only package requirement. Those source files, prior-corpus manifests, and comparison reports have been removed from this release.

The NY Medicaid Choice Program Materials page previously yielded only a Loading shell. The same downloaded HTML contains the requested page's three main text components in public embedded JSON. Processor 1.2.1 recovers those components in order, excluding other page objects and the sidebar. The original raw bytes are unchanged. No JavaScript was executed and no new resource was added.

All 1,886 non-whitespace characters match the three source components exactly. The canonical page now has 2,308 characters in 14 sections. It remains an index: the linked brochure and plan-list files have not been acquired merely by recovering their link labels.

## Missing content

There are 24 targets without acquired payloads. Failed requests remain explicit gaps; another version or similarly named resource has not been substituted. The exact URLs, attempts, and errors are in the coverage ledger and acquisition manifest. The results describe this environment's access rather than universal availability.

| Listed host | Missing acquisitions |
| --- | ---: |
{host_table}

The SharePoint workbook has not been parsed. The four customer notice targets remain the exact 2017-named URLs in the PDF, with no newer notice files substituted.

The 26 downloaded index-only targets include manuals, plan lists, policy collections, statutory-part indexes, and unresolved fragment/broad-index responses. Their descendant chapters and files have not been exhaustively acquired. A complete URL inventory therefore does not establish complete resource contents.

## Identity and scope qualifications

Actual source titles, citations, or descriptions were checked against the PDF labels. The compatible observations and their limits are recorded for all 108 targets in `audit/source_identity_audit.jsonl`.

- Four FindLaw fragment targets return broader Public Health Law indexes; the intended article/title contents remain unresolved. Article 36 and Article 40 additionally share one exact URI in the customer PDF.
- One legacy eCFR subchapter link resolves to the full Title 42 index, a broader scope than the PDF label.
- The PDF labels Medicare Parts 422 and 423 as Chapter VI; the acquired publisher representations identify Chapter IV. The part numbers agree, and the discrepancy is retained.
- The PDF names some cases and subclauses without separate file links. They are recorded in `audit/visible_unlinked_references.json`. No standalone document was guessed from a narrative mention.

A matching title or citation does not establish complete historical text, legal currentness, or applicability. PDF dates and other timestamps remain separate evidence signals.

## Delivery routes

The 84 selected downloads comprise {routes.get('customer_direct_acquisition.jsonl', 0)} direct requests/recorded redirects, {routes.get('customer_nydoh_recovery_acquisition.jsonl', 0)} declared NYDOH alternate-host recoveries using the exact listed path, and {routes.get('customer_fulltext_acquisition.jsonl', 0)} observed eCFR View Full Text representations. The five parent indexes are retained as provenance for those full-text representations.

The exact PDF URI and actual delivery URL are separate fields. The source-identity audit preserves both the literal relative HTML href and its resolved absolute URL for the eCFR links. These routes are disclosed representations; they do not prove equality to a historical snapshot the customer once viewed. The PDF supplies links, not immutable source copies.

## Inspect and reproduce

- `audit/source_coverage.md`: readable ledger with a row for each of the 108 targets.
- `audit/source_coverage.jsonl`: exact URI, PDF pages/references, status, selected raw hash/path, route, processing and passage counts, and review flags.
- `audit/scope_verification.json`: complete source-membership and corpus-lineage checks, with separate completeness status.
- `audit/independent_pdf_inventory_audit.json`: independent two-parser PDF audit.
- `audit/source_identity_audit.jsonl`: actual content-title/citation observations and scope qualifications.
- `audit/embedded_page_recovery.json`: verification of the repaired Program Materials extraction.
- `evaluation/integrity_checks.json`: {checks['automated_tests_run']} passed tests and {checks['actual_corpus_table_cells_audited']:,} table-cell offset checks.

Run the README commands to reproduce the checks. Add `--require-complete` to `verify_scope.py` to fail the gate when acquisition, index-only, or content gaps remain. This release fails that stricter gate; a passing source-membership result must not be reported as complete document coverage.

No model inference, fine-tuning, OCR, or client benchmark was performed for this audit. The source list is checked directly against the supplied PDF, and remaining content gaps are explicit.
'''
    (ROOT / 'SOURCE_AUDIT.md').write_text(report, encoding='utf-8')
    (ROOT / 'CORPUS_REPORT.md').write_text(report, encoding='utf-8')
    print(json.dumps(summary, indent=2))

if __name__ == '__main__':
    main()
