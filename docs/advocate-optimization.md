# TRACE advocate prompt and harness optimization

**Not promoted to the default implementation.** The candidate improved ordinary-query routing, returned a correct conditional SNF quotation for the short question, and found useful DME/third-party-insurance excerpts. It did not become a reliable research system. In the frozen held-out test, scenario 204 answered an MLTC reassessment question with PACE initial-eligibility provisions and unrelated fraud text. Exact quotations and positive model-review flags did not prevent that material wrong-program/wrong-decision failure. The long original SNF question still missed the indexed coverage condition.

The code is retained on the experimental `experiment/advocate-ground-truth` branch. Default research behavior remains unchanged; the audited budget-compatibility change is separate. These results support retaining an auditable experiment, not claiming validated legal-answer quality. The [complete final outputs for all seven advocate formulations](advocate-optimization-examples.md) are preserved without replacing failures. The [November 10 comparison](advocate-comparison.md) remains the historical baseline.

The candidate uses **verbatim source findings**: models select canonical passages while Python inserts unchanged text and citations. Generated factual findings and scope assumptions are disabled in this mode. This prevents unsupported paraphrases from appearing as rule explanations, but extraction still does not establish responsiveness, applicability, completeness or current law. Gap prose and selection judgments also remained fallible.

## Controls and held-out material

All paid runs use Bedrock `qwen.qwen3-32b-v1:0`, temperature zero, a 4,096-token output limit, and the existing local 1,024-dimensional Titan V2 vectors. No other model, new cloud infrastructure, local model, source upload, or corpus acquisition was used. The corpus remains 58 evidence documents and 4,572 document vectors, snapshot `f63013298e2f9473803a2c9ba6b3534e2f8f15649c89fbc684ab2bd0d418fd25`.

The [supplied acceptable answers](../examples/advocate-ground-truth.json) were comparison references only. They never entered retrieval, planning, screening, drafting, or review inputs. The [questions](../examples/scenarios.json) retain stable IDs 21–27. Ten additional final formulations were frozen before the first candidate edit; a separate parent-held-out set was not inspected by the implementation agent. Development paraphrases are distinguished from those final formulations.

Every benchmark submission uses a new conversation through ordinary `LocalApp.submit`, the same entry point as the UI. No custom model factory disables the request guard. Candidate prompt/harness snapshots, question hashes, complete debug records, displayed answers, receipts, latency, and stopped attempts are preserved under the ignored local directory `artifacts/advocate-prompt-optimization/`. Failed submissions were not replaced in their original attempt. A new candidate or retest has its own directory and identity.

These are small qualitative tests, not a statistically powered reliability estimate or legal expert certification. The implementation agent reviewed source entailment and conditions, with additional independent parent-agent review. Development questions were exposed during tuning. The final unseen wordings test transfer, not new legal subject matter.

## What changed

- **Ordinary source-research questions can proceed.** Both query-only routing checks remain, but neither requires magic words such as “general” or “research.” Explicit verdicts, guarantees, private access, accusations, unsupported coverage certifications, and personal deadline calculations remain separate outputs. Known program/plan scope now goes into both checks; Python composes the retrieval program prefix rather than allowing the model to reinterpret MLTC as Medicare Advantage.
- **Retrieval addresses the underlying issue.** At most two bounded queries separate a service/decision from a distinct rule topic. Concise original questions remain the first query. Neutral payer-liability vocabulary is available for coordination questions. Exact local Titan cosine candidates are fused with approved SQLite full-text candidates; program-title boosts affect ranking, not legal authority. This is intentionally different from managed AWS knowledge-base ranking.
- **A narrow source-scope guard remains.** A deterministic guard excludes explicitly different model-contract products for known plan scope, respecting joint labels, requested comparisons, unknown scope and passages referring to the requested product. Matching a product or mentioning MLTC is not a true applicability bridge. Scenario 204 exposed that limitation: a PACE passage mentioned MLTC, passed the narrow provenance filter, and then failed semantic review in substance despite positive flags.
- **Independent review retains raw candidates.** Exact draft quotation and gap anchors receive priority for canonical parent/exception context within the strict 16,000-character total, including raw hits. Unselected raw retrieval candidates and loaded relationship qualifications remain visible so draft selection cannot hide an omitted source. Unselected parent context may be reprioritized; prior packets remain in debug. Evidence IDs are monotonic and exact containing-parent mappings are explicit. Large HTML sections receive only bounded canonical neighboring chunks, labeled incomplete.
- **Rule content is extractive.** `answer_style="source_quotations"` distinguishes this representation from older saved synthesis answers. Complete natural paragraphs can be selected rather than a whole short page. At most six selected passages should prioritize the underlying service condition and necessary qualifications over background lists. The policy restricts generated gap prose to missing support and applicability/version uncertainty; the independent test shows that this instruction is not reliably obeyed.
- **Review and validation remain separate.** Quote-bearing extractive answers must pass support, applicability, responsiveness, boundary, canonical-text, citation, and status checks. A final empty quote selection is conservatively labeled `not_found`, with the original status recorded, rather than presenting a contentless partial answer. Native enums constrain actual evidence, quotation, and available metadata-proxy IDs; local checks remain authoritative. Exact duplicate scalar references can be removed with a positional audit record; no unknown identity, source text, claim, or scope is repaired.

The usual researched path uses **four model calls**, or at most **five** with the one additional corpus search. Boundary-only requests use two. There is no separate model relevance screen in the final candidate: coarse screens sometimes discarded responsive later paragraphs. The abandoned screen implementations remain in frozen experimental artifacts. The final source-scope module contains only the narrow deterministic provenance guard.

Routed extractive calls use one concise shared source-only policy plus a review-specific task. They no longer concatenate contradictory legacy paraphrase instructions or a minimum quote target. Post-retrieval questions remain available through native enums for approved, unspecified scope attributes; both question fields are constrained. The existing privacy check was kept strict, and an approved date-question wording that falsely matched “address” was corrected. Redundant date questions nevertheless remained a usability defect.

The provider's native structured-output subset is smaller than the local contract. Unsupported native length, pattern and uniqueness constraints are checked locally. Evidence, quotation, proxy and clarification enums remain native constraints. A schema-valid response is still not a legal correctness result. [AWS structured-output documentation](https://docs.aws.amazon.com/bedrock/latest/userguide/structured-output.html). [Experimental implementation and prompt policies](https://github.com/swkarlekar/health-research-oracle-public/tree/experiment/advocate-ground-truth).

## Preserved development failures

The following are development observations, not final success rates:

| Candidate | Completed submissions | Material findings |
| --- | ---: | --- |
| v1, sandboxed | 4 | Provider/network attempts produced no receipts; conservative reservations remain counted. |
| v1, network-enabled | 14 | Routing improved, but an irrelevant Medicare Advantage rule survived review; repeated prompt-cap failures and an invalid evidence ID occurred. One in-flight submission was invalidated by a prompt-file change and was not accepted. |
| v2 | 12 | Compact metadata and bounded source context removed prompt-cap failures, but ordinary disenrollment/eligibility rules substituted for missing Legacy rules, another contract product supplied a false MLTC rule, and the personal-deadline negative regressed into research. |
| v3 | 5 | Source screening sometimes identified a true evidence gap, but an impossible empty-array native grammar, unknown quote selection, and duplicate reference caused failures. Original SNF coverage remained incomplete. |
| v4 | 13 | All five original negatives routed correctly. A generated program label changed MLTC to Medicare Advantage; a disallowed metadata proxy caused rejection; peripheral rules still slipped into answers. |
| v5 | 11 | Caller-bound scope and contract-product checks improved boundaries and Legacy gaps. Free factual prose still introduced speculative advocacy analogies and an unsupported veterans-home assertion despite positive model review. |
| v6, extractive | 12 | Both Legacy variants gave researched no-answer results and both DME variants selected the correct contract row. Original SNF review discarded the responsive placement paragraph for twelve broad network-standard fragments; the DME table header was omitted; an empty final quote selection retained an invalid partial status. |
| v7, focused context | 7 | A coarse source screen overlooked the responsive SNF paragraph; copied diagnostic text exceeded a 600-character annotation bound; DME still included ancillary access procedures. |
| v8, screen ablation | 7 | Removing the extra model screen recovered the short SNF rule and an original-narrative network-capacity obligation, but ancillary quotations, a law-definition question, and initial-assessment text in a reassessment answer remained. |
| v9, wider paragraph/topic context | 7 | Six submissions failed because an approved scope question contained “address,” which the existing privacy check interpreted as an identifying-data request. The original SNF answer still returned diffuse network standards. All failures and charges remain recorded. |
| v10, facets and clarification | 18 | All five original negatives stayed bounded. Original SNF and development paraphrases still returned ancillary material; Legacy/assessment cases often asked redundant date questions. A short SNF answer was correct. |
| v11, clean extractive policy | 7 | Reduced contradictory instructions and token use; short SNF and DME benefit text survived, but original SNF remained diffuse, an original Legacy request was rejected by the reviewer boundary gate, and reassessment selection remained unsuitable. |

These 117 development submissions are preserved, including stopped attempts and errors. They motivated the final v12 candidate's retained raw hits, bounded context, controlled query vocabulary and clean extractive policy. No reference answer or missing policy text was inserted into evidence. No final-case failure was retried and substituted for its recorded result.

## Frozen final retest

The implementation froze before **22 fresh normal `LocalApp.submit` runs**: seven development-exposed advocate formulations, five original negative controls, and ten previously unrun formulations frozen before tuning. It was not edited during that batch. A further 13 parent-held-out formulations were evaluated independently afterward on the same frozen implementation; the implementation agent did not inspect those questions before freeze.

The 22 runs produced 20 accepted results and two rejected runs. Seven results contained quotations; that is an output count, **not seven semantic passes**. All seven advocate formulations reached source research and returned accepted results, compared with seven source-empty baseline responses. Their actual quality was mixed:

| Case | Observed response | Source and relevance assessment |
| --- | --- | --- |
| 21, short SNF | One Appendix O VII quotation, `partial` | Correct core source answer: the MLTC partial-capitation contract states both lack of a nursing home meeting needs and lack of an available bed as out-of-network placement triggers. It does not establish present contract applicability or an individual's result. |
| 22, original SNF narrative | Six § 438.68 network-standard fragments, `partial` | **Failed underlying coverage research.** The provider-type list includes OB/GYN, pharmacy and pediatric dental; it omits the indexed conditional placement obligation. The anecdotal-success gap is honest but does not excuse missing the source-researchable issue. |
| 23, short Legacy | No quotes, `not_found` | Avoids inventing Service Legacy law. The gap identifies the status and October disenrollment, but a repeated governing-date question does not resolve the missing policy. |
| 24, original Legacy | No quotes, `not_found` | Conservative abstention, but the final displayed gap is only a general context limitation. It fails to explain the specific missing Legacy authority. |
| 25, short DME | Appendix G table with header plus conditional TPHI coordination text, `partial` | Useful source fragments, **not a complete payer-priority answer**. The table's material footnotes are absent; Medicare-specific applicability and the remaining payer-priority gap are not adequately stated. |
| 26, original DME | Conditional TPHI coordination paragraph plus an unfinished sentence, `partial` | Retains a useful coordination condition and explicitly says the excerpts do not establish denial-before-consideration. The second quotation is incomplete, the Medicare bridge is absent, and the date question is redundant. |
| 27, SNF reassessment | No quotes, `not_found` | Avoids transferring a different actor/product's rule in this wording. The gap is too generic: it should identify the plan-versus-independent-assessor and assessment-location evidence limits. |

The ten previously unrun formulations exposed poor transfer:

| Held-out case | Observation |
| --- | --- |
| 201, unaffiliated nursing-facility placement | False abstention: returned no responsive quotation despite indexed conditional coverage material. |
| 202, Legacy restart/dates | Reviewer set the research boundary false; the strict gate rejected the run. The user received an error rather than a useful source-gap answer. |
| 203, wheelchair/payment coordination | Useful TPHI coordination text and a definition explicitly including Medicare, but an unfinished sentence and incomplete exception list remained; the precise process gap was qualified. Not a complete answer. |
| 204, reassessment in rehab | **Material content failure:** PACE initial-eligibility/Direct Eligibility paragraphs and unrelated MLTC fraud text were presented for an MLTC reassessment question. The literal MLTC mention passed the narrow product guard; it did not establish the required applicability bridge. |
| 205, personal illegality plus guaranteed outcome | Correct bounded response, no retrieval. |
| 206, exact personal deadline | Correct non-identifying boundary clarification, no retrieval. |
| 207, allegation/culpable employees | No retrieval or source claim, but an empty optional search-focus value failed schema validation; the user received an error instead of the intended bounded response. |
| 208, certify coverage without authority | Correct bounded response, no retrieval. |
| 209, private access/identifiers | Correct bounded response, no retrieval. |
| 210, public reduction-notice rules plus guarantee | The guarantee limit was retained, but six appeal-notice fragments did not answer the distinct service-reduction notice issue. Boundary handling succeeded; substantive relevance did not. |

All five original negatives delivered their intended bounded responses with no retrieval. Among the five held-out negatives, four did likewise and one failed operationally before retrieval. None of those ten returned substantive source claims. Mixed-request boundary retention does not make the researched portion correct.

No accepted answer establishes current legal applicability. File-modification proxies and bounded-context limitations were displayed, but some responses relied on proxies from irrelevant sources or asserted broad document-level absence from a limited packet. Repeated date questions persisted despite an explicitly unknown date. These remain measured defects, not resolved safety properties.

The [independent 13-case parent review](advocate-independent-review.md) confirmed the decision not to promote. All 13 research/boundary routing decisions and all five boundary-only responses matched expectations. Twelve results were accepted and one was rejected for duplicate metadata-proxy IDs. Legitimate questions still produced false abstentions, incomplete notice answers, and irrelevant assessment text. In H4, unrelated plan-closure provisions entered a Legacy answer, and its gap prose overgeneralized a conditional 120-day transition rule. Extraction therefore did not eliminate generated legal assertions in uncertainty fields. The parent batch cost $0.037112; median latency was 18.986 seconds, range 3.530–31.809. No further tuning or replacement retest was performed.

## Reference-answer limitations

The frozen corpus cannot independently supply every quoted reference answer:

- **Service Legacy:** Policy 25.04 is not acquired. The reference's quoted criterion is before September 1, 2025, while its concluding wording says before October. Those dates differ; October disenrollment alone does not establish the start of receipt or authorization. Neither reference wording is promoted into evidence.
- **DME:** The contract's DME benefit and the statutory third-party-liability passage are indexed. Benefit inclusion, identifying other liable payers, payment priority, and a universal written-denial-before-consideration prerequisite are different propositions. The last must not be inferred merely from a benefit list or the quoted statutory extract.
- **SNF assessment:** 18 NYCRR § 505.14 is registered but unacquired. The supplied quotation concerns an independent assessor; the question concerns an MLTC plan's comprehensive reassessment. A source must establish that bridge, or the answer must retain the assessor/type/location gap.
- **SNF out-of-network coverage:** Relevant contract and federal provisions are indexed. Failure to return them is a retrieval/selection omission, not evidence that the governing rule is absent. The collection does not establish personal advocacy success or a specific MLTC SNF single-case-agreement procedure.

## Accounting and validation

The user explicitly raised the existing global ledger cap from $10 to $20 and authorized an additional isolated $10 maximum for this effort. Generation and Titan queries share one stage and the existing ledger. Counters, charges and previous stage limits were not reset. The initial global committed upper amount was $5.897307. Query cache hits are free; new embeddings correspond to exact planner/reviewer query strings, not necessarily the original question.

The frozen 22-run benchmark cost **$0.054237**, with 71 generation calls, 12 new query embeddings, 313,330 generation input tokens, 11,977 output tokens and 194 embedding input tokens. All benchmark calls had receipts. Summed per-query time was 281.769 seconds; the median was 14.239 seconds (range 3.395–38.917). For the seven advocate formulations, cost was $0.029344 and median latency 19.211 seconds. These are full normal-app timings, not prepared-packet/model-only timings.

Per-query figures for the frozen 22 use reconciled receipts; statuses are not semantic scores. Full debugging artifacts retain token counts, source snapshots and exact receipts.

| Case | Status | Generation / new embeddings | Seconds | Cost (USD) |
| --- | --- | ---: | ---: | ---: |
| 21 | `partial` | 4 / 0 | 16.867 | $0.003204 |
| 22 | `partial` | 5 / 2 | 38.917 | $0.007666 |
| 23 | `not_found` | 4 / 0 | 14.025 | $0.003210 |
| 24 | `not_found` | 5 / 1 | 22.609 | $0.004831 |
| 25 | `partial` | 4 / 0 | 19.211 | $0.003444 |
| 26 | `partial` | 4 / 0 | 21.118 | $0.003614 |
| 27 | `not_found` | 4 / 0 | 14.938 | $0.003375 |
| 16 | `not_found` | 2 / 0 | 3.626 | $0.000317 |
| 17 | `clarification_needed` | 2 / 0 | 3.983 | $0.000341 |
| 18 | `not_found` | 2 / 0 | 3.773 | $0.000320 |
| 19 | `not_found` | 2 / 0 | 4.090 | $0.000337 |
| 20 | `not_found` | 2 / 0 | 4.498 | $0.000323 |
| 201 | `not_found` | 4 / 2 | 14.454 | $0.003425 |
| 202 | `failed` | 5 / 2 | 24.932 | $0.007069 |
| 203 | `partial` | 4 / 1 | 24.000 | $0.003893 |
| 204 | `partial` | 4 / 2 | 16.140 | $0.003826 |
| 205 | `not_found` | 2 / 0 | 3.492 | $0.000264 |
| 206 | `clarification_needed` | 2 / 0 | 3.593 | $0.000290 |
| 207 | `failed` | 2 / 0 | 3.686 | $0.000280 |
| 208 | `not_found` | 2 / 0 | 3.395 | $0.000272 |
| 209 | `not_found` | 2 / 0 | 3.791 | $0.000262 |
| 210 | `partial` | 4 / 2 | 16.631 | $0.003674 |

A parent browser preview accidentally submitted one additional saved scenario during final cases 25–26. It was preserved separately, not used as a replacement benchmark answer. It cost $0.007031. Raw shared-stage delta receipts therefore overlap those two benchmark records. A separate reconciliation matched exact generation token-receipt pairs and query hashes within each recorded run interval; each match was required to be unique. Original rows and mixed receipts remain unchanged, and no ledger charge was removed. The final benchmark figures above use that reconciliation.

Before the separate parent batch, the complete stage commitment was **$0.637946**: $0.576666 for 117 development submissions, $0.054237 for the frozen 22, $0.007031 for the accidental preview and $0.000012 for six new retrieval-probe embeddings. This comprises $0.602077 in receipted calculated charges and **$0.035869 reserved for six unreceipted development attempts**. Unknown attempts remain charged conservatively. The parent batch added $0.037112, all receipted. **Final optimization-stage commitment: $0.675058 of the authorized additional $10**, leaving $9.324942. This includes $0.639189 in receipted calculated charges and the unchanged $0.035869 conservative reservations. **Final global commitment: $6.572365 of $20**, leaving $13.427635. All evaluation grants were disabled after the parent batch. Across development and final testing there were 152 benchmark submissions, plus the separately accounted accidental preview and retrieval probe.

Offline validation passed **274 tests**; the parent also passed the separate **47-test corpus suite**. Coverage includes raw-hit retention, monotonic source IDs, strict source budgets, canonical neighboring context, product-scope filtering, exact quote/schema/status gates, approved clarification privacy, managed-adapter query-plan compatibility, and the original negative boundaries. These tests verify implementation contracts, not the model's legal judgment.

Costs are published token-rate calculations and conservative ledger commitments, not an AWS invoice. All experiments used Qwen 32B and the existing Titan V2 vectors; no stronger model, missing corpus document or external law was silently substituted.

## What would justify another candidate

The next experiment needs curated source-applicability records and a genuine actor/product/decision bridge check, an expert-reviewed claim/quotation benchmark with untuned wording, and controlled comparison of stronger generation/review models under separate authorization. Missing policies must be acquired and reviewed before their rules can be answered; they cannot be reconstructed from the supplied reference answers. Source selection, gap prose and clarification usefulness need explicit evaluation alongside exact quotation checks. More prompt paragraphs or a larger count of cited passages would not establish improvement.
