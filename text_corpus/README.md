# Text corpus from the customer's resource PDF

**Public distribution note:** the preparation notes below describe the original research package. Use the [project setup](../README.md) and [data Release manifest](../docs/data-bundle.md) for the current demo. Original reference PDFs, acquisition/audit files, and private runtime artifacts are not distributed. One unavailable sharing URL and cloud resource metadata are withheld; available source text, quotations, and document vectors are unchanged. `MANIFEST.sha256` is the historical preparation manifest, not the verifier for the public Release.

**Source-list membership is verified; resource-content coverage is incomplete.** The sole source-selection authority is the attached seven-page “Dataset and resources links” PDF, preserved unchanged in `reference/customer-resource-links.pdf`. Its SHA-256 is `de9bf3fdcc362501b4c3bb9e7db22a56065f22418ba888b3717abcfcd291a45e`.

The PDF contains 130 hyperlink occurrences and 108 distinct exact URLs. Every one has an inventory and acquisition-status record. Query strings, fragments, repeated occurrences, page references, and labels are retained. No previous corpus is used to decide which sources belong here.

Read **`SOURCE_AUDIT.md`** and **`audit/source_coverage.md`** before treating this package as a complete dataset. The former explains the guarantees and remaining gaps; the latter gives a row for every PDF-listed target. Machine-readable audits and coverage rows are alongside them.

## Current coverage

| Item | Count |
| --- | ---: |
| Exact customer URL targets accounted for | 108 |
| Targets with downloaded bytes | 84 |
| Targets without an acquired payload | 24 |
| Documents supplying evidence passages | 58 |
| Downloaded collection/index targets outside default retrieval | 26 |
| Evidence chunks | 4,572 |
| Extracted sections, pages, and table representations | 2,867 |

The 24 missing acquisitions are 15 Westlaw targets, four customer-linked NYDOH notice PDFs, two Medicaid.gov targets, one SharePoint workbook, one OTDA target, and one WNYLC target. Their rows remain explicit; they supply no source text.

An acquired index is not its underlying manual chapters, plan-list files, policy collection, or statutory part. Those descendant files were not exhaustively collected. Four FindLaw fragment targets remain unresolved, one eCFR link redirects to a broader index, and two Medicare chapter labels in the PDF differ from their linked part's publisher label. These uncertainties are preserved rather than replaced with guessed sources.

The PDF also mentions case names and subclauses without giving separate file links. `audit/visible_unlinked_references.json` records these references. No standalone case or statute file was added merely from a narrative mention.

## Scope and delivery rules

The `source_url` is always the exact URI from the PDF. A separate `resolved_url` records delivery. Of the 84 selected downloads, 52 came from a direct request or recorded redirect, 27 from a recorded NYDOH alternate hostname using the exact listed path, and five through explicit eCFR “View Full Text” links observed on the customer-linked page. Those five retain the parent index and link evidence. The 89 raw files therefore represent 84 listed targets, including five index/full-text pairs.

All alternate routes are visible. They do not establish that the delivered bytes equal a historical copy the customer once viewed. The PDF supplies links, not immutable snapshots of all the sources. Matching a title or legal citation also does not establish legal currentness, applicability, or complete content.

The strict package contains no previous FAQ/notice samples, previous source manifests, or comparison corpus. Its only source payloads are recorded representations of PDF-listed targets and the reference PDF itself. Code, configurations, and audit reports are supporting project files, not additional evidence sources.

The NY Medicaid Choice Program Materials page required a processing correction. Its visible HTML body says “Loading,” but the same downloaded response contains the requested page's three main text components in public embedded JSON. Processor 1.2.1 recovers those components in their declared order, excluding other page objects and the sidebar. The raw file was unchanged, no JavaScript was executed, and no linked brochure or plan-list file was added. `audit/embedded_page_recovery.json` records exact text-preservation verification.

## Useful files

| Path | Purpose |
| --- | --- |
| `manifests/customer_resource_links.jsonl` | Every hyperlink occurrence, PDF page, label, and exact URI. |
| `manifests/customer_unique_sources.jsonl` | One row per distinct exact PDF URI. |
| `manifests/acquisition.jsonl` | Selected downloads and explicit failures, with full delivery provenance. |
| `raw/` | Original source bytes and retained parent-index representations. |
| `processed/documents.jsonl` | Text, source metadata, dates, status, and uncertainty flags. |
| `processed/sections.jsonl` | Canonical section/page/table text with source locators. |
| `processed/chunks.jsonl` | Evidence passages and exact quotation offsets. |
| `processed/corpus.sqlite` | SQLite FTS5 retrieval database. |
| `audit/` | Independent PDF extraction, source identity review, scope verification, and complete coverage ledger. |
| `reviews/uncertainties.jsonl` | Outstanding source and coverage questions. |
| `evaluation/` | Integrity checks and pending client-benchmark status. |
| `examples/` | Offline search and evidence-packet smoke examples from this snapshot. |

Processing uses embedded text only, with no OCR, image models, audio transcription, or source paraphrasing. PDF modification dates, creation dates, HTTP dates, and dates mentioned in text remain separate signals. None is automatically treated as an effective date or supersession decision.

Quotes are exact spans of canonical extracted text. PDF layout and whitespace can change during extraction; raw files and page locators remain available to inspect material layout questions. The integrity checks do not establish semantic entailment or legal-answer quality.

## Use the corpus

Run from this folder with Python 3.12 and SQLite FTS5 support:

```bash
python3 scripts/research.py search "MLTC involuntary disenrollment" --limit 6

python3 scripts/research.py packet \
  "What are the reasons that a Medicaid plan may disenroll a member?" \
  --program "New York Medicaid" --plan-type unknown --limit 6 \
  --output packet.json
```

These commands perform lexical retrieval and assemble evidence; they do not call a model. To validate a response in the supplied schema and render its citations and quotations:

```bash
python3 scripts/research.py render \
  --packet packet.json --response response.json \
  --format markdown --output answer.md
```

The harness rejects stale packets and altered evidence, uses canonical citation URLs and quote spans, and carries source gaps into the rendered result. User review decisions remain separately labeled and scoped to the same evidence and research context. They do not rewrite documentary facts.

Retrieval is still a lexical baseline. Program and plan fields record scope rather than enforce applicability filtering; the smoke example can rank Medicare passages for a Medicaid query. No model inference, fine-tuning, or client-benchmark evaluation was performed. Model and budget configurations remain unevaluated planning inputs. See `examples/README.md` for context estimates and current limitations.

## Rebuild and verify

Install the pinned extraction/audit dependencies from `requirements.txt` in a virtual environment, then run:

```bash
python3 scripts/audit_pdf_inventory.py
python3 scripts/merge_customer_manifest.py
python3 scripts/process_corpus.py --progress
python3 scripts/verify_release.py
python3 scripts/verify_scope.py --root . \
  --pdf reference/customer-resource-links.pdf --output-dir audit
python3 scripts/build_report.py
```

The manifest merge rejects URLs or source IDs outside the actual PDF allowlist. The scope verifier separately checks PDF occurrences, manifest membership, selected raw-file hashes, section/chunk lineage, SQLite contents, and unexpected packaged source files. It can pass the inclusion checks while still reporting `completion_status: incomplete`.

The independent source-identity observations refer to specific raw hashes and are not regenerated by the mechanical checks. Re-review them if the source bytes change. Regenerate example packets after any corpus change. `MANIFEST.sha256` describes the packaged release; rebuilding changes file hashes.

The ZIP has one top-level `text_corpus/` directory. Extract it into `<local checkout>` after keeping any older `text_corpus` folder separately, so different snapshots are not mixed.
