# Local validation record

Latest checkpoint (2026-11-08 UTC): **247 oracle tests and 47 corpus tests passed**.
The collaborator setup installs the pinned private data Release; Settings selects
Bedrock or guarded local generation, with account/model-specific spending consent.
Release export, verification, and relocated installation checked all 4,572 vectors
and 84 original files without network or inference. The first prompt search
completed 365 Qwen calls but found no release-ready candidate; the second
independent search supports a narrow two-call request guard, now integrated. See [prompt-search findings](prompt-search.md).
These software checks do not establish legal correctness or completeness.

Previous checkpoint (2026-11-07 local / 2026-11-08 UTC): 174 oracle tests and 47
corpus tests passed. Live AWS evaluation attempted all 15 original scenarios,
exported 4,572 vectors, and demonstrated retrieval after a restore with zero
document re-embedding. Mechanical response acceptance did not establish content
quality: manual inspection found unsupported findings, wrong-program rules,
missing requested issues, and punctuation-only native responses. Current saved
AWS answers are withheld pending content review. The native pattern and empty
finding regressions are fixed and tested locally; further live quality validation
has not run. The original populated AWS stack and restored stack are now deleted; consult
[the current trial record](aws-trial.md). Earlier checkpoints below are historical.

Date: 2026-11-07. Environment: Python 3.12, macOS, SQLite FTS5. At this initial checkpoint, validation was local and AWS clients were mocked. Later checkpoints below describe subsequent local and live-trial work.

## Software checks

| Check | Result |
| --- | --- |
| New offline harness and AWS gate suite | 25 tests passed |
| Existing corpus integrity and quotation harness suite | 47 tests passed with pinned extraction dependencies |
| Python compilation | Passed |
| Eligible source registry | 108 source records pinned; 58 evidence sources allowed |
| Local evidence-packet generation | Passed against existing real corpus |
| Local S3 ingestion export | 9,144 files; 11,634,274 bytes; no network |
| CloudFormation resources | Parsed as JSON; every resource conditioned on explicit approval, default `no` |
| Real AWS integration or deployment | Not run; explicit user approval required |
| GitHub Actions | Manual workflow only; not run as part of validation |

The new tests cover clarification before retrieval, unknown scope, no-hit abstention, a mandatory review pass, the one-search limit, exact Unicode quotations, full-excerpt quote fallback, invalid quote rejection, registry exclusion and staleness, basic PII rejection, loopback-only model access, explicit labeling of metadata date proxies, unavailable proxy rejection, refusal to substitute acquisition/creation dates, absent/expired/mismatched/exhausted AWS grants, failed-attempt accounting, local export identity, tampered upload rejection, and canonical resolution of managed-retrieval IDs.

The first run of the original corpus tests under the system Python lacked the required extraction dependencies. A project-local `.venv` was created and the pinned requirements installed; all 47 then passed. The source corpus was not rebuilt or modified.

## Real local model observations

Installed `gemma3:4b` was invoked through the loopback Ollama server; no new model was downloaded. Early runs surfaced changed research scope, omitted quote selections, and output-budget exhaustion. The validator rejected the changed scope and over-budget output. Generation now constrains scope fields and evidence IDs; missing quote selections use whole canonical excerpts, while invalid selections still fail closed. Structured output is bounded to keep responses practical.

A terminology smoke question completed draft and review with mechanically valid citations, but returned an **unresponsive definition of social services instead of the requested acronym meaning**. The reviewer incorrectly marked that answer supported. This is an observed model-quality failure, not a successful research-answer benchmark. The review policy and contract were expanded to require an explicit responsiveness check. This additional model self-check is not proof of correctness.

The client's later instruction permits document last-modified metadata as a secondary date proxy when text does not establish dates. The implementation exposes allowed candidates to both model passes, validates the reviewer-declared proxy references against canonical metadata, preserves the raw values, and inserts an explicit qualification in the rendered answer. A proxy never becomes an automatically established effective date or supersession decision.

A subsequent local run produced an inconsistent supported status with unresolved uncertainty. The validator rejected it. The harness now deterministically lowers that overclaimed status to partial while preserving all uncertainty; the original corpus validator still rejects such inconsistent raw responses. HTTP `Last-Modified` remains visible metadata and is not enabled as a proxy by default; embedded PDF last-modified metadata is enabled.

The last local smoke run was rejected when the reviewer marked the research-only boundary unsafe. There is no accepted, expert-validated answer from that run. The user confirmed starting with dates embedded in documents; HTTP headers remain supporting metadata only.

This project is an engineering prototype. Tests establish control flow and evidence integrity. The observed local model behavior means it is not ready for advocate use without expert benchmarks, retrieval improvements, and model comparison. No legal or benefits conclusion in an exploratory smoke result should be treated as validated.

## Completed graph and adapter build

The next implementation pass used separate agents for graph extraction/storage, semantic interpretation/review, AWS API contracts, and adversarial code organization. An additional blind semantic pass reviewed candidate evidence independently of the proposed classifications. No paid AWS services or account API calls were used; no Titan embeddings were generated. The actual source interpretations are labeled Codex-agent model reviews, not Ollama/Bedrock invocations or human verification.

The completed map accounts for all 108 source nodes, all 2,867 canonical sections, and 4,572 canonical chunks. It contains 10,991 reference/signal records: 4,181 structurally verified references, 85 model-reviewed reference classifications, 4 rejected lexical false positives, and 6,721 unresolved references/signals. Ninety actual interpretation/review decisions are preserved in the hash-linked ledger; no resolved semantic candidate remains pending. Missing/unknown target identities remain unresolved. These counts include repeated citations and lexical signals; they are not counts of verified amendments.

One FAQ statement about partial supersession is preserved as an attributed assertion on two reference records, with exact co-citation bindings and unverified currency. It is not a verified controlling-law relationship. Full graph content and review artifacts remain local under `artifacts/`; the committed [summary](relationship-map-summary.json) contains coverage/status counts and snapshot hashes.

Final checks passed: **112 oracle tests and 47 original corpus tests, 159 total**, plus Python compilation. The optional boto3/botocore service models were installed only in the project virtual environment. Stubber validates Retrieve, Converse, PutObject and StartIngestionJob shapes using dummy credentials and blocked network connections. The CLI's evidence-only paths, dry-run plans, missing-grant rejection, independently selected models, and early clarification were checked without AWS clients or inference.

The real-corpus API simulation exercises the actual managed-retrieval and Converse adapter code with local monkeypatches: one mocked Retrieve call, two mocked Converse calls, canonical context expansion, graph lookup, and exact quotation rendering. The fake search is backed by SQLite BM25. Its output explicitly reports no real semantic retrieval, embeddings, model judgment, or answer-quality validation. A separate real-source table smoke check preserves the complete timeframe-comparison document, column headings, and extension footnote.

Independent functional audits reproduced and fixed rehashed graph-text forgery, altered page metadata, changed review-version packets, forged relationship traces, status mismatches, and resource cleanup gaps. Unicode dashes and multi-letter legal identifiers were corrected with regression tests. Model input retains canonical text and table headers while omitting repeated layout geometry; relationship lookups are cached only under the audited graph content digest.

The organizational review simplified CLI ownership/dispatch, removed duplicate argument aliases and dead/legacy branches, centralized shared test fixtures, and shortened the README. See [the review record](organization-review.md). Live account availability, Titan ranking, Nova quality, actual latency/charges, and expert correctness/completeness benchmarks remain unmeasured. The default Nova Lite model can be replaced independently for each generative role, subject to compatibility and a new paid-service approval where needed.

## Realistic scenarios and separate output views

The 15 user-supplied synthetic research scenarios are preserved verbatim in `examples/scenarios.json`. Their scope annotations are demo configuration, not gold answers. A local retrieval/expansion smoke check ran all 15 with no model or AWS calls. Every scenario returned four seed chunks, but inspection found weak or irrelevant matches for several questions, especially non-MAGI financial eligibility, work-related disregards, spousal protections, and asset transfers. Nonempty retrieval is not an answer-quality pass. Current thresholds and currentness are not established by this check. The full local report is `artifacts/scenario-retrieval-check.json`.

The former simulated `demo` was renamed `api-check`. The new `demo` runs actual installed local inference and review. Concise customer output and opt-in debugging reports share the same validated run. Regression tests cover round-by-round evidence and model access, graph-connected versus loaded documents, private-reasoning-field exclusion, retained uncertainty and exact Unicode quotations, failure diagnostics, distinct output paths, case selection, long-scenario question-clause retrieval, lossless passage selectors, unknown-selector rejection, and quote-specific page locations. The Bedrock Stubber flow now exercises the same quotation-ID protocol used locally.

An actual `gemma3:12b` run on scenario 1 was rejected for an invalid numeric quote span. A stronger installed `qwen3.6:latest` run produced a relevant draft but its review exceeded the prompt budget. Model input now omits repeated inventory details and full graph statistics, while the complete audit remains in diagnostics. Generation now selects labeled source passages; Python resolves their exact offsets. No source characters are removed from the supplied excerpts. No model download or paid AWS service was used.

The first completed selector run passed mechanical quote checks but its reviewer accepted a voluntary-disenrollment timing rule in an involuntary-disenrollment answer. This is a substantive model-review failure. Review instructions were tightened to distinguish the legal categories, remove unsupported claims, and use the additional corpus search for unanswered issues. Findings are allowed enough space to complete their sentences, and repeated metadata-proxy caveats are grouped by document. These changes are not proof that self-review establishes legal correctness. Local inference and Titan/Nova integration quality remain separate evaluation questions.

The complete follow-up review packet was measured below the existing 110,000-character limit after grouping repeated graph leads and document metadata, prioritizing bounded date-signal leads, and referencing raw hits already fully contained in supplied parent text. Exact text equality is checked before a covered hit is deduplicated; source text remains available in the retained parent and in the complete diagnostic packet. A regression test verifies the coverage map, inherited provenance, and selectable citation IDs. Final software checks: **128 oracle tests and 47 original corpus tests, 175 total**, plus compilation and whitespace checks. These counts are engineering checks, not legal-answer benchmarks.

Review explanations use a separate optional `review_notes` field; `issues` remains reserved for unresolved problems that can block a supported answer. A regression test reproduces the distinction, so positive diagnostic notes do not weaken or accidentally trigger the acceptance gate. Shared document metadata retains excerpt-specific overrides, including extraction/uncertainty qualifications.

The final local scenario-1 run used `qwen3.6:latest` for draft and separate review, one lexical retrieval/expansion round, and two actual model calls (about 68 seconds). It returned `partial`, explicitly retaining gaps about initial disenrollment notices and temporal applicability. Seven rendered quotations were independently checked against canonical source spans, URLs, and quote-specific PDF pages. The default Markdown view folds unusually long supporting excerpts while preserving their complete verbatim text; JSON and the diagnostic report retain full passages. This is a format/integrity smoke result, not an expert-approved answer: the review still records unresolved distinctions between disenrollment notices, service actions, and continued-benefit procedures.
## Local interface and AWS trial preparation

The local chat interface was exercised through real HTTP requests and local
Ollama generation, including scenario 1, persisted history, canonical citation
resolution, and a 207-page saved PDF with seven matched highlight rectangles.
Its real answer was partial. Mock-backed tests separately cover Host/Origin/CSRF,
history/context isolation, concurrent job rejection, source hashes/path confinement,
Unicode offsets, and diagnostic retention after failures.

The AWS trial controller was tested without network access for atomic budget
reservations, restart persistence, concurrent cap enforcement, failed-call upper
bounds, paid usage retained despite malformed answer JSON, model/region bounds,
and deletion of only the temporary stack's manifest keys. The preparation suite
passed 156 oracle tests plus 47 preserved corpus tests (203 total), including the explicit DNS-only upload retry, same-job ingestion continuation, and exclusion of reasoning blocks from answers and logs. Actual paid
trial receipts and query outcomes belong under ignored `artifacts/aws-eval/` and
must not be inferred from these mocked tests.
