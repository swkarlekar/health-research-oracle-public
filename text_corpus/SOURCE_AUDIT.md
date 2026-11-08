# Exact PDF source audit

Generated: 2026-11-06T17:35:01.272709-05:00

## Result

**The package passes exact PDF source-membership checks. Resource-content coverage is incomplete.** The attached seven-page resource PDF is the only inclusion authority; the previous corpus is not used to decide what belongs here.

The PDF contains 130 URI annotations and 108 distinct exact URLs. Independent pypdf and PyMuPDF extraction agrees, including query strings, fragments, repeated occurrences, and raw URI bytes. Every URL has an inventory and acquisition-status record. No URL was omitted from or added to the source inventory.

| Check | Result |
| --- | ---: |
| PDF URL targets accounted for | 108 |
| Missing or added inventory URL targets | 0 |
| Unlisted source IDs in retrieval | 0 |
| Extra packaged source payloads | 0 |
| Orphan raw files | 0 |
| Targets with downloaded bytes | 84 |
| Targets with no acquired payload | 24 |
| Documents supplying evidence passages | 58 |
| Downloaded index-only targets | 26 |
| Evidence passages checked against source lineage | 4,572 |
| Integrity tests passed | 47 |

The scope verifier checks every saved acquisition, selected raw hash, document, section, chunk, and SQLite record against the PDF-derived allowlist and canonical text. It also checks for unexpected packaged source files. All 89 raw files are accounted for: 84 selected representations plus five retained eCFR parent indexes. The source-membership checks pass while the completion result remains `incomplete`.

## Corrections made

Five previous FAQ/notice sample files were physically present in the preceding ZIP as comparison material. They were excluded from retrieval, but their presence did not meet a strict PDF-only package requirement. Those source files, prior-corpus manifests, and comparison reports have been removed from this release.

The NY Medicaid Choice Program Materials page previously yielded only a Loading shell. The same downloaded HTML contains the requested page's three main text components in public embedded JSON. Processor 1.2.1 recovers those components in order, excluding other page objects and the sidebar. The original raw bytes are unchanged. No JavaScript was executed and no new resource was added.

All 1,886 non-whitespace characters match the three source components exactly. The canonical page now has 2,308 characters in 14 sections. It remains an index: the linked brochure and plan-list files have not been acquired merely by recovering their link labels.

## Missing content

There are 24 targets without acquired payloads. Failed requests remain explicit gaps; another version or similarly named resource has not been substituted. The exact URLs, attempts, and errors are in the coverage ledger and acquisition manifest. The results describe this environment's access rather than universal availability.

| Listed host | Missing acquisitions |
| --- | ---: |
| redacted.invalidprovider | 1 |
| govt.westlaw.com | 15 |
| otda.ny.gov | 1 |
| www.health.ny.gov | 4 |
| www.medicaid.gov | 2 |
| www.wnylc.com | 1 |

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

The 84 selected downloads comprise 52 direct requests/recorded redirects, 27 declared NYDOH alternate-host recoveries using the exact listed path, and 5 observed eCFR View Full Text representations. The five parent indexes are retained as provenance for those full-text representations.

The exact PDF URI and actual delivery URL are separate fields. The source-identity audit preserves both the literal relative HTML href and its resolved absolute URL for the eCFR links. These routes are disclosed representations; they do not prove equality to a historical snapshot the customer once viewed. The PDF supplies links, not immutable source copies.

## Inspect and reproduce

- `audit/source_coverage.md`: readable ledger with a row for each of the 108 targets.
- `audit/source_coverage.jsonl`: exact URI, PDF pages/references, status, selected raw hash/path, route, processing and passage counts, and review flags.
- `audit/scope_verification.json`: complete source-membership and corpus-lineage checks, with separate completeness status.
- `audit/independent_pdf_inventory_audit.json`: independent two-parser PDF audit.
- `audit/source_identity_audit.jsonl`: actual content-title/citation observations and scope qualifications.
- `audit/embedded_page_recovery.json`: verification of the repaired Program Materials extraction.
- `evaluation/integrity_checks.json`: 47 passed tests and 15,120 table-cell offset checks.

Run the README commands to reproduce the checks. Add `--require-complete` to `verify_scope.py` to fail the gate when acquisition, index-only, or content gaps remain. This release fails that stricter gate; a passing source-membership result must not be reported as complete document coverage.

No model inference, fine-tuning, OCR, or client benchmark was performed for this audit. The source list is checked directly against the supplied PDF, and remaining content gaps are explicit.
