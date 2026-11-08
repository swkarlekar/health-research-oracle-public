# Prompt-search findings

## First experiment: completed; no candidate promoted

A GPT-6.1 Sol agent at high reasoning independently explored prompt and call-structure changes using Bedrock Qwen3 32B. It tested 20 configurations across 184 case/configuration evaluations and 365 model calls. Strategies included per-issue drafting, applicability screening, claim-to-quote review, exceptions/date checks, adversarial criticism, independent reconstruction, smaller payloads, request routing, and quote-led synthesis. Production prompts remain unchanged because the selected candidate did not pass all validation gates.

The six original positive questions held out before development were cases 2, 5, 8, 11, 13, and 15. One frozen pass produced:

| Measure | Baseline | Selected candidate |
| --- | --- | --- |
| Exploratory mean score, 0–100 | 26.4 | 55.9 |
| Accepted outputs | 4/6 | 5/6 |
| Operational rejections | 2/6 | 1/6 |
| Accepted outputs with flagged critical errors | 2/6 | 1/6 |
| Original negative examples | 3/5 | 5/5 |
| Frozen negative paraphrases | 2/5 | 4/5 |

These scores are not percentages correct. They use an agent-prepared reference and a coarse single-assessor rubric, not legal expert validation. The assessor also developed candidate prompts, so independence/blinding is limited. Six heterogeneous questions and one run per arm do not establish statistical superiority or optimality.

The selected pipeline combined a query-only request router, per-source applicability/role screening, and synthesis with recorded metadata. It avoided using Medicare Part D subsidy definitions as Medicaid resource rules, and expressed more evidence gaps. However, it omitted the serious-health-risk transition condition in case 2 and rejected case 8, where the baseline produced an accepted answer. Its router mishandled a fraud/employee-identification paraphrase by explaining private-record access limits instead of explicitly recognizing an unverified allegation. The quote-led alternative retained some conditions better but still transferred historical or wrong-program requirements to the wrong question.

Every variant used the same initial local Titan retrieval/expanded packet, with at most 16,000 canonical source characters. Screening could remove sources before synthesis. The controlled experiment did not perform the production harness's optional additional search, so these results are not a complete online-service benchmark. A final equivalence audit found that seven training outputs would additionally fail the production `supported`-with-unresolved-review-issues gate; none of the six positive held-out outputs were affected. Training acceptance counts must not be presented as exact production equivalence.

## Cost and timing

Calculated token cost was **$0.70112235**, and the conservatively rounded prompt-stage ledger commitment was **$0.701296**. All 365 provider receipts matched distinct ledger charges, with no pending reservations. The original $10 ledger then stood at **$5.465344 committed**, including earlier trial costs; these are calculated application records, not an AWS invoice.

On the six prepared-packet positive held-out questions, mean model-pipeline costs were $0.005191/query for baseline and $0.004388/query for the candidate. Mean measured pipeline times were 6.738 and 5.604 seconds respectively. These measurements exclude embedding, retrieval/packet preparation, and local hardware cost. Lower cost/latency does not compensate for failed correctness checks.

The law and relationships came only from the selected corpus. No new embedding, AWS infrastructure, or local LLM/GPU inference was used for this experiment. Full local records are under `artifacts/prompt-search/`: `report.txt`, `spend-audit.json`, frozen prompts/driver, per-query metrics, judgments, and original SDK receipts. They are intentionally excluded from Git and the data bundle. [Concrete prompt excerpts and current source files](prompts.md) are included in the repository.

## Second experiment: completed; narrow boundary guard adopted

A separate GPT-6 Astra agent at extra-high reasoning tested five new substantive call structures, the baseline and Sol comparator, and three routing designs. Its 499 paid completions covered 136 case/configuration evaluations. Fresh cases were frozen before calls: eight substantive reformulations and ten boundary/mixed formulations, reusing existing evidence packets. Original questions and earlier holdouts were exposed development material.

The typed source/trigger/effect/qualification candidate retained some conditions better. On eight fresh substantive reformulations, accepted outputs with no flagged critical error were 3/8 for baseline, 4/8 for Sol, and 5/8 for the new candidate; operational rejects were 1/8, 2/8, and 1/8. All arms retained substantive defects. More structured calls and same-model audits did not reliably resolve wrong-program applicability. Production drafting/review prompts remain unchanged.

The two-call query-only request guard made the intended **continue/block decisions on 18/18 fresh formulations**, versus 17/18 for the earlier Sol router. It preserved explicit public research in mixed requests, including public notice rules plus a private-record access request. This is narrower than 18/18 complete answer correctness: category overclassification and two wording defects remained. Application-written capability wording was refined after inspection, and 31 saved classifier outputs were replayed through schema/boundary checks. Those post-validation corrections are explicitly not untouched end-to-end validation.

The narrow guard is adopted as an incremental improvement relative to the baseline's documented boundary failures. It makes two calls before retrieval; a boundary-only response stops with fixed limits and no source search, while a public/mixed request retains the ordinary research flow. Mixed-request limits are attached only after the original substantive acceptance gates, and the result is revalidated. No citation, quotation, review, or date-proxy guard is weakened. Normal research now uses four model calls, or five with the existing additional search/review. Provider-neutral adapters use the same prompts; evaluation was on Bedrock Qwen, not local models.

Astra's rounded stage commitment was **$0.426768** of its $4 allowance. All 499 receipts matched distinct settled charges; two pre-inference SSO/network attempts remained counted without paid SDK receipts. The shared ledger finished at **$5.892112 committed of $10**, with no pending reservations or reset counters. These remain calculated application records, not an AWS invoice. Raw records are locally under ignored `artifacts/prompt-search-astra2/`.
