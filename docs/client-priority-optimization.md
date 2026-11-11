# Client-answer optimization on the recovered corpus

The full source-quotation answer pipeline is **not recommended for promotion**. The frozen client outputs demonstrate better access to several important passages, but still substitute neighboring rules, omit requested issues, and understate applicability gaps. Exact quotations and successful schema validation do not establish that an answer is legally responsive. The general routing, hybrid retrieval, terminology and canonical-context changes are separable from the experimental answer pipeline; the legacy-answer cross-check failed, while a smaller hybrid-only patch and a separate wording-only guard check support a narrower production subset.

The experiments left root production code unchanged; after review, the parent integrated only the standalone hybrid module and wording-only original guard policies. The full experimental candidate was not promoted. The isolated candidate is `artifacts/client-answer-optimization/source`, based on private main `3398a06`. No worker commits or pushes were made. The active corpus, registry, graph and document vectors remain unchanged at snapshot `915db3bb0c7cf98c8ce23ff2f873e9b8a415fe193243f6c4bcb0d43f7f6900c4` (120 records, 81 evidence documents, 7,044 normalized 1,024-dimensional Titan vectors).

## Evaluation and controls

The four client ground-truth cases, in all seven supplied wordings (21–27), are the primary acceptance criteria. The four simplified wordings were used during development. The final frozen batch runs all seven once, including the three original narratives, followed by user boundary cases 16–20. These are fresh final runs, not a claim that the four development questions are statistically held out. Failed attempts remain failures; no retry replaces a failed benchmark response. Synthetic positive examples were not used as a release gate.

The supplied acceptable answers inform evaluation only. No reference-answer prose, legal-answer lookup, scenario-to-source map or evaluation rubric enters routing, selection or review. A separately labeled diagnostic upper bound uses human-selected **canonical source excerpts**, not gold-answer prose; that private diagnostic mapping is never imported by candidate code.

Every normal run creates a fresh conversation through `LocalApp.submit`, with the ordinary request guard enabled. Only the metered model provider is replaced after constructing the app; `custom_model_factory` remains false. The model is `qwen.qwen3-32b-v1:0` in `us-east-1`, temperature 0, explicitly `reasoning_effort: none` and `/no_think`, 4,096 output tokens and 90,000 prompt characters. No private reasoning is collected. Queries reuse the existing Titan cache; misses use only Titan V2, normalized 1,024 dimensions, at most 1,600 query characters. SDK retries are disabled. Free STS verification precedes inference; grants are account/action/resource bound, expire within two hours, and are expired in `finally` without resetting usage.

The optimization phase has a hard additional **$1.00** limit inside the existing $5 comparison stage and unchanged persisted $20 cumulative cap. Conservative requests are reserved before SDK dispatch; returned usage is settled before JSON parsing or validation. No-receipt reservations remain committed. Attempt ceilings were explicitly raised from 150 to 200 to 250 generation calls, and 100 to 120 query calls, without increasing any dollar cap or resetting counters. This phase excludes the completed document-embedding work and the earlier $0.023059 eight-run corpus retest.

## What changed

The candidate replaces the two-query public-research veto with one scope-aware classifier. Ordinary coverage, status and process questions do not need magic words such as “general public research.” Public portions of mixed requests can continue while explicit guarantees, private access, individual verdicts and unverified accusations receive fixed boundary limits. Known program and plan scope is supplied to the classifier and preserved in caller-composed searches.

Retrieval remains **hybrid keyword plus vector search using the existing Titan vectors**. There is no fixed similarity cutoff and no fixed “50/50” allocation. Per-query bounded vector and FTS candidates are fused, then phrase/facet and known-plan acronym lanes contribute candidates. Issue queues are combined under explicit seed and character budgets. The final candidate permits up to 12 initial seeds / 26,000 raw source characters and 32,000 total canonical source characters for Bedrock; local-generation limits remain separate. Candidate lists, overlap, rank changes, exclusions and budget omissions are recorded. A larger candidate pool alone would not have repaired the later context and selection losses.

Canonical table-page completion retains headers and same-page footnotes. Raw chunk edges are expanded to nearby complete canonical paragraphs, within the same character bounds, on both initial and additional retrieval. Replacements preserve original chunk provenance and monotonic evidence IDs, and remain seeds for relationship/date/exception traversal. All expanded text is re-resolved against the unchanged canonical document hash and offsets. This repairs mid-paragraph TPHI and table-footnote loss without inventing text.

The client workbook’s `Jargon` sheet supplies a small typed terminology file, `config/terminology.json`. Its provenance records workbook hash `f54782f4ad4bc1cdb4a43d4d62c033e2fb4bcad49ea70eade6347c055ecf1673` and source cells. The experimental terminology subset contains names and aliases only; it is not loaded by production. Original acronyms coexist with expanded/generic source terminology. ADL and CHA retain both meanings. DME spelling is normalized; the erroneous Medicare/LTC row is excluded. Eligibility summaries, payment assertions, historical amounts and the four gold answers are not copied into the terminology file or treated as authority.

The final experimental planner binds dimension, issue, named subject, query and facets in one typed record. Earlier parallel arrays could associate a definition query with a continuation issue. Named-subject grounding is restricted to definition/continuation, removes generic class words such as “status,” and preserves adjacent source context. It is still a fallible model plan; typed structure does not prove that it chose the right issue or named subject.

The experimental answer mode selects verbatim canonical passages rather than generating factual findings. One selection call is made per issue, followed by independent review over the selected union. Review verdicts are keyed by **issue and quotation**: the same passage can answer one issue and fail another. Review cannot introduce unselected quotations. Empty retained sets produce bounded, fixed issue-gap text; related-actor applicability can produce a fixed qualification. One focused additional search may lead to reselection and another review. The bound is three or four usual model calls, at most seven; boundary-only requests need one. Full source/span, scope, privacy and independent-review gates remain enabled. This output-style change is substantial: it is source quotation research, not complete legal synthesis.

## Development failures and causal tests

All batches and their exact outputs, failures, public review decisions, token receipts and source snapshots are preserved under `development-v1` through `development-v12-smoke`. Early attempts repeatedly confused Plan Legacy with Service Legacy, substituted Medicare Advantage or neighboring-program material, lost the DME table or TPHI clause, and selected assessment introductions instead of the actual location clause. Operational failures included an output-token-limit response, invalid structured fields and contradictory quotation decisions. Mechanical acceptance never counted as content success.

Two decoder diagnostics preserved identical prompts and evidence within each pair. The first compared native JSON constraints with prompt-only JSON; removing native structure did not solve relevance and produced an invalid-JSON response. The second removed only native evidence/quote-ID enums while retaining strict local enum validation. It exposed a controller defect: a globally vetoed quote was being reassigned to a different issue, but the contract prohibited re-adding it. The per-issue review contract fixes that contradiction. These tests do **not** establish that the model is incapable, nor that disabling structured decoding is an improvement.

The canonical-source upper bound showed that Qwen could select the Legacy non-loss and assessment-location clauses when they were supplied alone, but free-form generated “gaps” contradicted those very quotations. Fixed bounded gap rendering removes that specific failure route. It does not make irrelevant quotations responsive, establish an actor bridge, or prevent the planner from choosing a neighboring topic.

## Frozen client results

Exact final answers and validation failures are in [exact experimental responses](client-priority-responses.md); individual `qwen-off-<id>.md`, `.debug.json` and `.receipts.json` files preserve every result. The table evaluates substantive content, not status labels or quotation counts.

| Case | Frozen response and comparison with acceptable answer | Assessment |
|---|---|---|
| 21: simplified OON SNF | Retains Appendix O p200’s two conditions: no nursing home meeting needs, or no available bed when placement is indicated. Also includes a transition-specific permanent-resident rule without its full surrounding trigger. | Correct core, overinclusive qualification problem. |
| 22: original OON narrative | Retains contract p22’s conditional duty to furnish necessary services outside an inadequate network. Adds an irrelevant §438.68 pharmacy/pediatric-dental/LTSS fragment. Does not establish a specific single-case agreement or anecdotal success. | Useful core, incomplete and noisy. |
| 23: simplified Service Legacy | Exact Service Legacy definition and September 1, 2025 cutoff are present. The continuation selection substitutes Plan Legacy removal/continuous-enrollment language and omits the express Service Legacy non-loss note. | Failure; neighboring-status substitution. October cessation alone also does not prove receipt/authorization before September 1. |
| 24: original Legacy narrative | Retains the Service Legacy definition, but adds policy-purpose and unrelated FFS-to-MLTC transition coverage. It does not resolve retention after service discontinuation using the available non-loss source. | Failure/incomplete. No individual eligibility verdict is established. |
| 25: simplified DME | Retains contract p70 TPHI coordination and MRG third-party-resource language, but omits the DME benefit row and Medicare coinsurance/deductible footnote that a development run had successfully selected. | Partial source retrieval; incomplete answer to benefit and payer question. |
| 26: original DME submission/denial | Returns a CFCO assistive-technology paragraph instead of addressing DME payer coordination or a denial-before-consideration requirement. | Failure. Available payer-priority text does not establish a universal formal-denial prerequisite. |
| 27: reassessment in SNF | Retains exact 505.14 and 505.28 location clauses, including nursing facility, home evaluation when necessary, and telehealth qualification. They concern the independent assessment. Review does not identify the specific bridge to an MLTC plan’s comprehensive reassessment; only a general actor limit remains. | Material retrieval improvement; incomplete applicability answer. Secondary-host edition/currentness qualifications remain. |

No whole-case “seven passed” claim is justified. The frozen results do not meet the four-client acceptance goal. The earlier production retest blocked three of the four simplified questions before retrieval; removing that false veto is a real routing gain. More useful source access is also demonstrated. Those gains should not be described as a validated complete answer system.

## Production decision and evidence

Do not promote the full quotation-selection/review pipeline on the basis of these results. Preserve it as an experiment. The broad single-classifier/planner, terminology and canonical-context bundle is not ready for production either: its legacy-answer cross-check failed. The recommended narrow subset is the standalone hybrid search module and the separately tested wording-only change to the original two-call guard. A different answer style is not itself a quality fix.

The evidence supports keeping the hybrid approach and the existing Titan document embeddings. It does not support a new embedding model or a similarity threshold change as the next step. Remaining work should focus on correctly bound issue plans, clause-level relevance, explicit actor/product/assessment bridges, and review that cannot turn an ancillary paragraph into a complete answer. Further model comparisons would need their own controlled authorization and should use these client cases and expert source-grounded judgments, not synthetic positive labels.

Offline checks: 263 harness/application tests and 47 corpus tests passed, plus a focused recheck after the last named-subject correction. Tests cover public research without magic words, source/quote canonical validation, the per-issue reassignment controller, glossary ambiguity, safe clarifications, bound FTS terms, source-product distinctions, canonical table completion and monotonic IDs. They are implementation regressions, not legal-answer accuracy tests.

## Legacy-answer cross-check: broad bundle rejected

`legacy-general-fixes` runs the same four simplified questions and the three boundary requests that previously continued into source research (16, 17, 20). Normal `LocalApp` and request guarding remain enabled; the process-only override sets `Harness.source_quotations=False`, invoking the existing draft/review answer style with the candidate retrieval and quotation adapter. The other two boundary cases were route-only and are explicitly reused, not presented as new runs.

Six of seven runs failed: 21 exceeded the review prompt cap; 25, 27 and 17 exceeded the draft prompt cap; 16 exceeded the review prompt cap; and 23 returned duplicate quotation selectors. Case 20 stopped at the guard with no source answer. No failed result was replaced. This arm cost $0.016366 (11 generation and five query calls). It does not validate the broad 32k-context bundle with the legacy answer style. The per-call 90k cap was preserved.

A further unpromoted implementation limitation was identified: the experimental planned search's fixed 26k raw seed budget is not bound to a smaller caller context budget, such as the 8k local-model path. This is recorded as a deployment blocker in the frozen experiment, not silently fixed after the final test. The standalone hybrid patch does not include that planned-search code or change any context/seed limit.

## Narrow production recommendations

**Hybrid-only:** `minimal-hybrid/hybrid-only.patch` changes only `oracle/local_vectors.py` and its test. It preserves the original `ApprovedCorpus.packet` interface, existing three-seed demo behavior, 16k Bedrock / 8k local context defaults, original two-call guard and existing draft/review policies. A successful, authorized Titan query remains mandatory; keyword search is not a fallback that bypasses the embedding approval boundary. The debug record includes vector rank, lexical rank, each RRF contribution, title multiplier and fused score for every merged candidate. The meaningful rescue test proves an approved keyword hit omitted from the vector window survives, results deduplicate, no extra embedding is made, and the selected-source filter still applies. All 252 production tests passed with only this module overlaid. No paid calls were made for that test.

The seven frozen experimental client runs selected 74 initial seeds: 37 appeared only in keyword candidate pools, 32 in both pools and five only in vector pools. There were no unattributed seeds. These are membership counts across the actual planned searches, including phrase/title lanes; they are **not** fixed fusion weights, causal credit, final-answer shares or an accuracy measure. The standalone production subset has no planner/facet lanes. Both use the existing Titan embeddings. Detailed counts are in `retrieval-channel-metrics.json` and the parent's independent attribution artifact.

**Guard wording only:** `minimal-guard/wording-only.patch` changes only the two original policy strings (and their descriptive comment). An AST comparison verifies that schemas, payloads, controller and two-call AND decision remain unchanged. The wording recognizes conditional rule/criteria/procedure questions and rule-based research guidance without magic words; it retains the boundaries on individual verdicts, guarantees, allegations, unsupported coverage, private records and precise individual deadlines. No IR planner or answer pipeline is added.

One frozen guard-component test used all seven client questions, all five boundary questions and one mixed public/private probe: all seven clients continued, all five pure boundary requests stopped, and the mixed request continued with its private-access limit. This was 26 calls, $0.002789 and 23.664 seconds; no retrieval or answer generation occurred. It is evidence for routing behavior, **not** legal-answer accuracy. Case 20's first classifier still overcategorized parts as public/timing; the independent second check correctly vetoed research. The exact decisions and prompts are in `minimal-guard-final/`. No further wording tuning followed this test.

The parent selected these two small patches for production integration after review. The complex planner, 32k context changes, per-issue quotation selection and new answer representation remain experimental. The four-client complete-answer acceptance goal has not been achieved.

## Boundary behavior in the full experimental final

Cases 18 and 19 stopped without retrieval. Cases 16, 17 and 20 continued into source research, retained fixed limits on guarantees, exact personal timing and private access, and did not establish a guaranteed result, fraud finding or private-record access. Their calibration was poor: extra categories and ancillary source material appeared, and case 16's first classifier omitted the individual-illegality category. These are not clean whole-response passes. The separate narrow guard test above was necessary; the source-only boundary outcomes were not reused as proof of legacy synthesis safety.

## Final accounting and artifacts

Optimization phase: **$0.270758**, 237 generation calls and 100 query-embedding calls; 1,610,836 input and 48,201 output generation tokens, 2,197 query tokens. No unreceipted calls or nonempty thinking were observed. The phase used less than its unchanged $1 limit. Global committed upper amount is **$7.202774**, leaving **$12.797226** under $20; the existing comparison stage has **$4.369591** remaining. These are calculated ledger amounts, including historical reservations, not an AWS invoice. All phase grants are expired with counters preserved.

| Preserved batch | Generation calls | Query calls | Cost upper |
|---|---:|---:|---:|
| development-v1 | 14 | 14 | $0.025032 |
| development-v2 | 11 | 12 | $0.016483 |
| development-v3 | 12 | 8 | $0.018441 |
| development-v4 | 11 | 6 | $0.023305 |
| development-v5 | 13 | 8 | $0.024836 |
| development-v6 | 12 | 5 | $0.020557 |
| decoder-v6 | 4 | 0 | $0.009359 |
| oracle-context-v6 | 4 | 0 | $0.002718 |
| development-v7 | 16 | 7 | $0.028586 |
| development-v8 | 16 | 5 | $0.011663 |
| decoder-id-v8 | 4 | 0 | $0.004123 |
| development-v9 | 16 | 6 | $0.014624 |
| development-v10 | 14 | 6 | $0.011368 |
| development-v11 | 14 | 3 | $0.012102 |
| development-v12-smoke | 4 | 2 | $0.002034 |
| final-v13-frozen | 35 | 13 | $0.026372 |
| legacy-general-fixes | 11 | 5 | $0.016366 |
| minimal-guard-final | 26 | 0 | $0.002789 |

The source-quotation final and legacy comparison retain exact per-query latency, token usage, failures and public review decisions in their `queries.json`, `.debug.json` and `.receipts.json` files. Exact human-readable final outputs are collected in [exact experimental responses](client-priority-responses.md) and [legacy cross-check outputs](client-priority-responses.md#legacy-answer-cross-check). The completed prior eight-run corpus comparison remains at `artifacts/reasoning-comparison/new-corpus-qwen-off-20261111/report.md` and `exact-responses.md`.

`metrics.json` and `phase-receipts.json` provide phase accounting. Each batch's `controls.json` pins the source hashes, snapshot, parameters and question hashes. `full-experimental-candidate.patch` and `full-experimental-candidate-manifest.json` preserve the frozen candidate; its source hashes match `final-v13-frozen/controls.json`. The parent's independent canonical audit confirms document spans, URLs, full-text hashes and original-file hashes for selected quotations; that audit is mechanical and does not override the substantive failures above. Account-bound grants and private settings are not publication material.

## Published production changes and independent checks

Only hybrid retrieval and the two original request-policy strings were promoted. Default seeds, context budgets, response schema, two-call guard controller, drafting/review, canonical validation and spending controls are unchanged. The UI groups client questions first, boundary tests second, and synthetic examples last. The client references remain evaluation material, never evidence.

The production hybrid module passed 252 application tests and 47 corpus tests. A final targeted 54-test recheck and browser JavaScript syntax check passed after the policy/UI changes. With network blocked, all seven real-data evidence packets reused cached query vectors and stayed within 16,000 source characters. These are engineering checks, not a new end-to-end answer evaluation.

The full experimental snapshot is preserved on the [client-priority-v13 branch](https://github.com/swkarlekar/health-research-oracle-public/tree/experiment/client-priority-v13). It is not the default demo. The raw corpus/vector release is unchanged.
