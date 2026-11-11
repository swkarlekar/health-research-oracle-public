# Corpus-update baseline — Qwen3 32B thinking off

**The expanded corpus did not demonstrate an overall answer-quality improvement in these four single runs.** The matched experimental pipeline retained its correct core SNF quotation and retrieved newly acquired Policy 25.04, but the reviewer discarded the correct Service Legacy definition selected by the draft. DME remained unanswered; the SNF reassessment answer remained irrelevant. The separate pre-change demo check blocked three legitimate questions before retrieval and did not answer DME payment responsibility. Neither result justifies promoting the experimental pipeline or claiming that the source update fixed the four answers.

The [eight exact new responses](client-corpus-retest-responses.md) are preserved unchanged. These are qualitative observations on development-exposed questions, not estimated accuracy rates. Temperature zero does not guarantee identical provider outputs; retrieval/planning/review remain an end-to-end process, not a fixed-evidence-packet experiment.

## What was held constant, and what changed

The primary batch repeats simplified scenarios **21, 23, 25 and 27** from the authoritative prior `qwen-matched-v3/reconciled-queries.json` rows. It uses the frozen experimental v12 harness in [reasoning experiment branch](https://github.com/swkarlekar/health-research-oracle-public/tree/experiment/reasoning-comparison), normal `LocalApp.submit`, a fresh conversation per question, the ordinary request guard, hybrid retrieval, 16,000 source characters, 90,000 prompt characters, 8,192 total output tokens and temperature zero. Bedrock was `qwen.qwen3-32b-v1:0` in `us-east-1`; every request explicitly used `reasoning_effort: none` and `/no_think`.

The old snapshot was `f63013298e2f9473803a2c9ba6b3534e2f8f15649c89fbc684ab2bd0d418fd25` with 58 evidence documents and 4,572 vectors. The new snapshot is `915db3bb0c7cf98c8ce23ff2f873e9b8a415fe193243f6c4bcb0d43f7f6900c4`: 120 source records, 81 evidence documents, 38 excluded collection indexes, one unavailable source, and 7,044 chunks/Titan V2 normalized 1,024-dimensional vectors. The new corpus and relationship map are the deliberately changed inputs. Policy 25.04 and a secondary Cornell representation of §505.14 are now acquired.

All prior v12 research-source hashes match. `oracle/aws.py` differs only through later provider-adapter additions and reasoning-presence detection; an offline captured-request check verified identical serialized Qwen-off requests for the old and current adapters. That difference is recorded, not concealed. Source snapshots, runner copies, hashes and data-file hashes are in each batch's controls. Corpus, registry, graph and vector-file hashes were unchanged throughout both runs. No production or worktree code, prompt, ranking parameter or source was edited.

The **secondary production cross-check is a different pipeline**, run in a separate process with the then-current main code: actual 4,096-token output default, pure local-vector retrieval, three seeds per search, the same 16,000 source characters and normal guard. The transport explicitly supplied `reasoning_effort: none` alongside its existing `/no_think`. Its differences from v12 must not be attributed solely to data. Only the metered model factory was replaced; `custom_model_factory` remained false. A runner-only ledger reservation wrapper enforced the combined phase cap without changing research behavior or persisted limits.

Reference answers and evaluation criteria did not enter routing, retrieval, drafting or review inputs. There were no answer retries, replacement results, access probes, new document embeddings, other response models or external research calls.

## Old answer → new answer → supplied acceptable answer

| Case | Prior Qwen-off / old corpus | Matched v12 / new corpus | Assessment against the supplied acceptable answer |
| --- | --- | --- | --- |
| **21 — out-of-network SNF** | One correct MLTC Appendix O VII paragraph. | The same paragraph, preserving both unavailable suitable nursing-home and unavailable-bed conditions. | **Core answer retained; no demonstrated improvement.** It matches the reference's core placement condition. It does not establish present contract applicability or an individual's entitlement. The simplified question does not require anecdotal single-case-agreement success. |
| **23 — Service Legacy after October disenrollment** | No-answer response because the named policy was missing. | Six introductory/Plan Legacy paragraphs from newly acquired Policy 25.04; the actual Service Legacy definition was discarded by review. | **Retrieval improved; final answer did not become correct.** The definition was available and initially selected. The later non-loss-on-disenrollment sentence is now in the corpus but absent from model-shown evidence. Public reviewer notes conflate Plan and Service Legacy, assume pre-September enrollment and assert individual qualification. These notes are in debug, not the displayed rule text, but reveal a failed review. The supplied reference's September 1 cutoff cannot be replaced by its broader “before October” conclusion. October cessation alone does not establish when receipt/authorization began. |
| **25 — Medicare/MLTC DME payer** | No-answer response despite some indexed benefit/coordination material. | Still no answer. Conditional third-party-insurance coordination text was supplied, but none was retained. | **No demonstrated improvement.** Benefit inclusion, identifying other liable payers, Medicare payment priority and a denial-before-consideration prerequisite are distinct propositions. The reference's third-party-liability quotation alone does not establish every processing prerequisite. The new result supplies neither the benefit rule nor a useful qualified coordination answer. |
| **27 — UAS-NY reassessment in SNF** | Irrelevant initial-assessment, service-reduction and CDPAS excerpts. | Irrelevant legacy-status/reassessment and service-reduction/notice excerpts. No §505.14 assessment-location passage. | **Still fails the location question.** The recovered location provision concerns an independent assessment and preserves home/residence and telehealth qualifications. It does not automatically establish the same duty for every MLTC plan reassessment. A source-supported actor/type bridge or explicit gap remains necessary; copying the reference's categorical “yes, a plan is required” would not fix that problem. |

No case demonstrates newly established current legal applicability. Modified-file dates were often cited for sources that did not answer the issue. The 2012 Medicaid Reference Guide and the recovered regulation's secondary-source/edition qualifications must not disappear merely because they are indexed.

## Where the failures occur

Both runtimes use **bounded top-K selection, not a fixed similarity-score cutoff**. Each vector search considers 30 nearest canonical candidates. Experimental v12 also fuses up to 30 full-text candidates and selects up to six distinct seeds across at most two initial planned queries. Main selects three vector seeds. Expansion and additional retrieval remain bounded by 16,000 source characters. A document being acquired, a chunk entering the candidate pool, a seed being selected, text reaching the model and a quotation surviving review are separate stages.

| Case | Observed stage path | What a larger candidate pool would and would not solve |
| --- | --- | --- |
| **21** | Contract page 200 entered the initial model payload as E3; the responsive paragraph survived draft and review. | No demonstrated recall problem for this wording. |
| **23** | Policy 25.04's first chunk, document offsets 0–3150, reached both models as E2. Draft selected the correct **E2.Q9** Service Legacy definition. Review replaced it with **E2.Q1–Q6** and misidentified the latter as the Service Legacy rule. Parent expansion was omitted with “Context budget reached; evidence was not truncated.” The later non-loss clause was not shown. | More candidates cannot repair the reviewer discarding already supplied correct evidence. Targeted context/section coverage is also needed for the later retention condition; merely raising K still competes for the same context budget. |
| **25** | Contract page 70 conditional TPHI coordination text reached draft and review as E6, alongside historical/general material. Draft and review retained no quotation. The reviewer used the governing-date clarification question itself as `additional_query`, spending the remaining search on the wrong subject. | There are ranking/coverage gaps for the full benefit and payer authority, but also a selection failure and an ineffective additional-search query. More neighbors alone would not repair those latter failures. |
| **27** | No §505.14 text reached the model. Other assessment-related passages were retrieved; draft and review treated them as answering an SNF-location question. Review asserted that the selected sources addressed SNF settings although they did not. | Recall of the **actual location passage** needs investigation, separately from the best rank of any chunk in the large §505.14 document. Larger K could help only if that responsive passage enters and survives later selection/context. It would not fix the applicability judgment or the substitution of unrelated assessment rules. |

The independent cached-vector audit now supplies the exact distinction: for the original reassessment question, the best-ranked §505.14 chunk was **64**, while the actual assessment-location passage was **243**; for the planned rewrite they ranked **241** and **496**. Increasing the vector pool from 30 to 100 would therefore still miss the responsive clause in both queries. Conversely, the Service Legacy non-loss chunks ranked **3 and 22** for the focused query and entered both vector and lexical candidates, but did not survive hybrid seed selection. DME page 156 entered lexical/hybrid hits for the first query but did not enter the final assembled seed packet; the page 70 coordination chunk did reach the model. The correct SNF coverage clause ranked 72 for its original query and was successfully recovered through lexical retrieval. These are cached-query observations, not a new embedding experiment; the full audit is `artifacts/updated-corpus-retest-review/retrieval-stage-audit.json`.

These traces do **not** establish that Titan must be replaced. First measure recall of the responsive clause at candidate, seed, model-context and final-answer stages, and evaluate targeted section expansion, source diversity/reranking and faithful review of supplied evidence. A separate controlled embedding comparison would be needed before claiming another embedding model is better. There is no current score threshold to lower. Main's pre-retrieval routing failures are unaffected by K or embedding changes.

## Pre-change demo results

| Case | Pre-change main result | Diagnosis |
| --- | --- | --- |
| 21 | `not_found`, no source search | First classifier labeled public research; the independent explicit-public check returned false. The added sources were never consulted. |
| 23 | `not_found`, no source search | Same routing blocker, despite Policy 25.04 now being available. |
| 25 | `clarification_needed`, two waiver quotations plus generated enrollment summaries | General MLTC/MMMC eligibility and 120-day-service material does not establish DME payer responsibility. It asks whether the individual has both Medicare and MLTC, already stated in the question. This is not an improved DME answer. |
| 27 | `not_found`, no source search | Same routing blocker. Neither the vector index nor the recovered regulation was consulted. |

The production cross-check is evidence about the **demo before the client-priority changes**, not proof that the experimental pipeline is deployed. No experimental promotion was performed or recommended.

## Tokens, timing and cost

| Batch | Case | Status / quotes | Generation / new embeddings | Input / output tokens | Seconds | Cost (USD) |
| --- | --- | --- | ---: | ---: | ---: | ---: |
| Prior v12, old data | 21 | partial / 1 | 4 / 0 | 19,271 / 506 | 15.487 | $0.003196 |
| Prior v12, old data | 23 | not_found / 0 | 5 / 0 | 37,814 / 1,179 | 22.787 | $0.006381 |
| Prior v12, old data | 25 | not_found / 0 | 5 / 0 | 34,924 / 1,099 | 26.496 | $0.005900 |
| Prior v12, old data | 27 | partial / 3 | 4 / 0 | 19,728 / 640 | 12.646 | $0.003345 |
| Matched v12, new data | 21 | partial / 1 | 4 / 0 | 18,952 / 473 | 18.190 | $0.003129 |
| Matched v12, new data | 23 | partial / 6 | 4 / 0 | 22,065 / 804 | 17.975 | $0.003794 |
| Matched v12, new data | 25 | not_found / 0 | 5 / 0 | 37,859 / 1,115 | 28.727 | $0.006350 |
| Matched v12, new data | 27 | partial / 6 | 4 / 0 | 20,697 / 635 | 16.185 | $0.003488 |
| Pre-change main, new data | 21 | not_found / 0 | 2 / 0 | 739 / 53 | 5.499 | $0.000144 |
| Pre-change main, new data | 23 | not_found / 0 | 2 / 0 | 779 / 101 | 3.595 | $0.000179 |
| Pre-change main, new data | 25 | clarification_needed / 2 | 5 / 1 | 33,517 / 1,302 | 24.274 | $0.005812 |
| Pre-change main, new data | 27 | not_found / 0 | 2 / 0 | 749 / 83 | 3.039 | $0.000163 |

The prior four cost $0.018822; the matched new four cost **$0.016761**. Their summed elapsed times were 77.416 and 81.077 seconds; medians were 19.137 and 18.083 seconds. These are single-run normal-app timings with warm query caches, not stable performance estimates. The lower new cost is not an accuracy result.

The pre-change demo cross-check cost **$0.006298**. The combined phase cost **$0.023059**, with 28 generation calls, one 19-token query embedding, **zero unreceipted calls** and **zero observed nonempty thinking calls**. All eight submissions returned accepted outputs; acceptance is not semantic correctness. Only reasoning presence, final public decisions and usage were recorded; no private reasoning text or raw provider responses were saved.

The strict shared phase allowance was $0.50 inside the existing isolated $5 comparison stage and persisted $20 global cap. Before SDK dispatch, the runner checked the combined phase delta and per-batch/combined attempt limits, then used the unchanged atomic ledger reservation. Returned generation usage was settled before JSON/schema validation. At the end of this baseline, the global commitment was **$6.932016**, with **$13.067984** remaining; the existing comparison stage has **$4.640349** remaining. No caps, prior charges or counters were reset. Both batches' separate exact account-bound grants were expired in `finally`, preserving used-call counts, and their UI authorizations were cleared. Costs are conservatively rounded token-rate ledger calculations, not an AWS invoice.

## Audit artifacts and recommendation

- `matched-v12/` and `production-main/`: controls, copied runner/source snapshots, corpus/registry/graph/vector hashes, all exact outputs, full source/decision debug records, immutable per-query receipts and budget snapshots.
- `adapter-parity.json`: offline old/current Qwen-off request equivalence check.
- `metrics.json`, `phase-receipts.json`, `phase-state.json`: exact accounting and bounded dispatch counts; no previous incremental-corpus-embedding charge is included.
- `exact-responses.md`: all eight complete exported responses.
- Parent independent quotation audit: `artifacts/updated-corpus-retest-review/independent-quote-validation.json`; all 15 selected source quotations matched canonical document offsets, raw hashes and source URLs. That validates quotation identity, not legal relevance.

Keep the recovered source data, which demonstrably removes acquisition gaps. Do not equate that recovery with answer correctness or promote v12 on these results. The next evaluation should isolate routing, responsive-clause recall/context, and reviewer retention/applicability separately; preserve source and actor/edition qualifications and verify improvements on untuned questions. The existing evidence supports targeted diagnosis before replacing the embedding model or broadly increasing context.
