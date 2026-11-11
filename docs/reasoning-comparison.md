# Bedrock reasoning and alternative-model comparison

**Status: trials completed; no alternative has demonstrated a reliable replacement.** This is a new, matched four-question comparison on the frozen original corpus and experimental v12 harness. It does not enable the experimental pipeline in the default demo. All trials below used the original snapshot. The later source recovery is documented separately in [the corpus update](https://github.com/swkarlekar/health-research-oracle-public/blob/main/docs/source-recovery.md); it did not enter these model comparisons.

Enabling Qwen reasoning did not produce four correct answers. It improved the DME source selection in this sample, while three other submissions failed operational or schema checks. Haiku 4.5 has not produced a benchmark answer: its access probe returned `AccessDeniedException`, and read-only checks showed no Anthropic first-time use-case registration and an unavailable model agreement. Its result is **not evaluated**, not a model-quality failure. A separate probe of the documented Bedrock Mantle endpoint also returned permission denied for `bedrock-mantle:CreateInference`; no permissions were changed.

## Controls

- The four simplified advocate questions are stable scenarios 21, 23, 25 and 27. Each starts a fresh conversation. The longer original narratives were not retested here.
- The evaluated corpus was the original 58 evidence documents and 4,572 Titan document vectors. No reference answer, new source, or outside law entered a model input.
- Both Qwen arms use `qwen.qwen3-32b-v1:0`, the same prompts, 16,000-character source budget, hybrid retriever, graph, request guard, quotation checks and separate review. Both have an 8,192-token total output cap and temperature zero. Planning and review use the selected mode too, so retrieved passages can differ between arms. This is an end-to-end comparison, not a fixed-evidence-packet comparison.
- Thinking off explicitly sends `additionalModelRequestFields={"reasoning_effort":"none"}` and `/no_think`; thinking on sends `{"reasoning_effort":"high"}` and `/think`. A probe verified nonempty reasoning blocks at high effort. The soft `/think` instruction alone did not establish that reasoning was enabled on this endpoint. [AWS sample investigation](https://github.com/aws-samples/sample-per-model-bedrock/issues/272).
- The blocked Haiku arm was `global.anthropic.claude-haiku-4-5-20251001-v1:0`, with a 2,048-token thinking allowance inside the same 8,192-token total cap. Its required provider sampling default replaces temperature zero. Both providers use native structured JSON output plus full local validation. [AWS extended-thinking documentation](https://docs.aws.amazon.com/bedrock/latest/userguide/claude-messages-extended-thinking.html), [structured-output support](https://docs.aws.amazon.com/bedrock/latest/userguide/claude-messages-structured-outputs.html).
- Private reasoning text is discarded. Only its observed presence, final responses, tokens, timing, source records and receipts are recorded.

These are single runs on development-exposed questions, not accuracy rates or proof that reasoning helps or harms the model generally. An earlier saved non-thinking answer differed from this fresh baseline. Provider nondeterminism, the increased output ceiling relative to the previous experiment, schema compatibility and operational failures limit interpretation. The matched off/on arms share the same new output ceiling.

## What changed

| Question | Thinking off | Thinking on | Assessment |
| --- | --- | --- | --- |
| 1. Out-of-network SNF coverage | Correct core contract quotation, including unavailable suitable homes or beds. | Rejected: `response.findings: invalid item count`. | No improved accepted answer. The thinking response violated the extractive schema and was withheld. |
| 2. Service Legacy | Researched no-answer result; the missing named policy remains a source gap. | Failed after 301.519 seconds with no token receipt. | No substantive comparison possible for the thinking arm. The duration is consistent with the configured 300-second read timeout, but the displayed error did not preserve the underlying exception type. |
| 3. Medicare/MLTC DME | No suitable answer; missed available benefit/coordination evidence. | Quoted the MLTC DME benefit table and a conditional third-party-insurance coordination provision. | Useful improvement in retrieval/selection, still incomplete: no explicit Medicare applicability bridge, table footnotes are absent, and the remaining payer-priority gap is not adequately explained. |
| 4. SNF reassessment | Irrelevant initial-assessment, service-reduction and CDPAS provisions; no responsive SNF-location answer. | Rejected: `response.response.uncertainties[0].question_for_user: expected string or null`. | Neither produced the expected supported answer. A schema rejection is not evidence that substantive applicability reasoning improved. |

The four questions therefore remain unresolved as a complete set. Policy 25.04 and the cited §505.14 provision are absent from the evaluated snapshot. The later recovery acquired Policy 25.04 and a secondary representation of §505.14; neither entered this comparison. Exact quotes and positive model checks remain insufficient for source applicability.

## Per-query timing and cost

| Mode | Question | Result | Model calls | Observed reasoning calls | Seconds | Committed cost (USD) |
| --- | --- | --- | ---: | ---: | ---: | ---: |
| qwen-off | 1 | `partial` | 4 | 0 | 15.487 | $0.003196 |
| qwen-off | 2 | `not_found` | 5 | 0 | 22.787 | $0.006381 |
| qwen-off | 3 | `not_found` | 5 | 0 | 26.496 | $0.005900 |
| qwen-off | 4 | `partial` | 4 | 0 | 12.646 | $0.003345 |
| qwen-on | 1 | `failed` | 3 | 3 | 20.136 | $0.002486 |
| qwen-on | 2 | `failed` | 1 | 0 | 301.519 | $0.005722 |
| qwen-on | 3 | `partial` | 4 | 4 | 31.258 | $0.004298 |
| qwen-on | 4 | `failed` | 4 | 4 | 28.165 | $0.004402 |

**qwen-off:** $0.018822 for the four submissions; median 19.137 seconds.

**qwen-on:** $0.016908 for the four submissions; median 29.712 seconds.

The timeout has no usage receipt; its conservative reservation remains counted. Costs are rate-based ledger calculations, not an AWS invoice. Output charges include reasoning tokens. A single timeout dominates this small timing sample.

An initial setup attempt omitted the query grant's account field. The existing account guard blocked those embedding requests; the attempt was stopped and its records retained. Its soft-switch reasoning probe did not demonstrate thinking. A second attempt verified high-effort reasoning and encountered Haiku's access denial before benchmark calls. The final Qwen-only attempt retained all four off and all four on outcomes, without substituting retries for failures. Original metric rows counted prior same-arm/case charges in the call-count field; separate reconciled rows derive call counts from each immutable receipt file. Per-query costs and token totals already used the correct current-run receipts and are unchanged.

## Additional models requested

After the Haiku access problem, the user requested alternatives. All successful access probes used the same frozen experimental harness and four simplified questions. Model size is not a quality result: the runs below mostly failed the structured-response contract before a complete research answer could be assessed.

| Model | Access and outcome | What this establishes |
| --- | --- | --- |
| Claude Haiku 4.5 | Bedrock access denied; Mantle also denied. No benchmark answers. | Not evaluated. Read-only checks found the Anthropic first-use registration absent and agreement unavailable. No forms or account permissions were changed. |
| Grok 4.7, global and US profiles | Both access probes returned `AccessDeniedException`. No benchmark answers. | Not evaluated; the exact policy/entitlement cause was not established. |
| Llama 3.3 70B | Access probe passed; all four submissions failed required JSON fields. | This adapter/prompt combination did not deliver an accepted answer; substantive legal accuracy remains unmeasured. |
| Llama 4 Maverick | Access probe passed; all four submissions failed JSON syntax or required fields. | Same limitation; missing clarification arrays and review issues were not silently supplied. |
| GPT-OSS 120B, high reasoning | Probe passed after handling the provider's closed reasoning prefix; all four research submissions failed JSON decoding on the first routing call. | Provider/adapter structured-output compatibility remains unresolved. Reasoning was observed, but there was no accepted research answer to grade. |

Llama 3.3 uses a 4,096-token output cap supported by that endpoint; Maverick and GPT-OSS use 8,192. Llama responses use JSON instructions and full local validation without the native constrained decoder used for Qwen/GPT-OSS. Consequently these are compatibility and end-to-end trials, not a controlled ranking of reasoning ability. The private reasoning content is discarded for every provider. GPT-OSS here is `openai.gpt-oss-120b-1:0` through Bedrock, with no local model download.

One additional Llama 3.3 formatting attempt also failed all four cases. Its overly broad reminder introduced an answer-only field into two routing responses. That harness-side defect was corrected by restricting the reminder to answer schemas before the Maverick trial; those failed rows remain recorded rather than being hidden or interpreted as legal-quality failures. The initial GPT-OSS probe that failed before the prefix handling is likewise retained.

### Alternative-model per-query record

| Model / attempt | Question | Result | Model calls | Seconds | Committed USD | Rejection |
| --- | ---: | --- | ---: | ---: | ---: | --- |
| Llama 3.3 baseline | 1 | `failed` | 3 | 11.236 | $0.006254 | `response: missing fields ['clarifying_questions']` |
| Llama 3.3 baseline | 2 | `failed` | 4 | 15.586 | $0.015067 | `response.response: missing fields ['clarifying_questions']` |
| Llama 3.3 baseline | 3 | `failed` | 4 | 14.534 | $0.013792 | `response.response: missing fields ['clarifying_questions']` |
| Llama 3.3 baseline | 4 | `failed` | 4 | 9.934 | $0.013397 | `response.response: missing fields ['clarifying_questions']` |
| Llama 3.3 formatting | 1 | `failed` | 3 | 12.825 | $0.007203 | `response.findings: invalid item count` |
| Llama 3.3 formatting | 2 | `failed` | 2 | 4.167 | $0.001217 | `response: unexpected fields ['clarifying_questions']` |
| Llama 3.3 formatting | 3 | `failed` | 2 | 3.740 | $0.001162 | `response: unexpected fields ['clarifying_questions']` |
| Llama 3.3 formatting | 4 | `failed` | 4 | 10.583 | $0.013501 | `response.response: missing fields ['clarifying_questions']` |
| Llama 4 Maverick | 1 | `failed` | 4 | 19.179 | $0.005099 | `response.response: missing fields ['clarifying_questions']` |
| Llama 4 Maverick | 2 | `failed` | 3 | 10.235 | $0.002578 | `Bedrock returned invalid structured JSON` |
| Llama 4 Maverick | 3 | `failed` | 4 | 16.689 | $0.004793 | `response: missing fields ['issues']` |
| Llama 4 Maverick | 4 | `failed` | 4 | 11.997 | $0.004895 | `response.response: missing fields ['clarifying_questions']` |
| GPT-OSS 120B high | 1 | `failed` | 1 | 7.134 | $0.000480 | `Bedrock returned invalid structured JSON` |
| GPT-OSS 120B high | 2 | `failed` | 1 | 4.744 | $0.000447 | `Bedrock returned invalid structured JSON` |
| GPT-OSS 120B high | 3 | `failed` | 1 | 6.801 | $0.000667 | `Bedrock returned invalid structured JSON` |
| GPT-OSS 120B high | 4 | `failed` | 1 | 3.934 | $0.000429 | `Bedrock returned invalid structured JSON` |

Failed and denied calls remain counted. Budget reporting separates these model trials from the later incremental document-embedding category; both consume the same authorized $5 stage within the cumulative $20 cap. Full private receipts are retained locally, without publishing account identifiers, grants or private reasoning. No further paid model calls are scheduled.

## Exact displayed responses

Failed submissions have no accepted research response. The rejection shown below is preserved instead of inventing or reconstructing an answer.

### Question 1

**Prompt:** When would an MLTC plan be required to cover an out-of-network skilled nursing facility?

#### qwen-off

**Qualified research result**

**Source findings — verbatim quotations**

[MLTC Partial Capitation Model Contract](https://health.ny.gov/health_care/medicaid/redesign/mrt90/2023/docs/part_cap_model.pdf), page 200

> VII.  If an MLTCP does not have a nursing home to meet the needs of its Enrollees, the MLTCP
>      must authorize out of network placement. If beds are not available at the time placement is
>       indicated, the plan must authorize out of network placement.
> 

**Limits and unresolved questions**

- The document's last-modified metadata (pdf\_modification\_date\_raw = D:20250131120205-05'00') was used as a secondary date proxy. It does not establish a legal effective date or supersession. ([MLTC Partial Capitation Model Contract](https://health.ny.gov/health_care/medicaid/redesign/mrt90/2023/docs/part_cap_model.pdf))
- Some connected source material remains unresolved or could not be reviewed. The answer does not establish that the sources are complete or current.

Based on the supplied corpus; current legal applicability has not been independently verified.


#### qwen-on

**No accepted answer.** response.findings: invalid item count

### Question 2

**Prompt:** For purposes of the Minimum Needs Requirement, does Service Legacy status apply to a person who received personal care services through an MLTC plan until being disenrolled in October 2025?

#### qwen-off

I could not establish a suitable answer from the supplied evidence.

**Limits and unresolved questions**

- No passage directly addresses the Service Legacy status applicability after disenrollment from MLTC plans.
- Some connected source material remains unresolved or could not be reviewed. The answer does not establish that the sources are complete or current.

**More information needed**

- Which governing date or period should this research cover? Use YYYY-MM-DD or unknown; do not provide personal dates.

Based on the supplied corpus; current legal applicability has not been independently verified.


#### qwen-on

**No accepted answer.** Research run failed; no answer was accepted

### Question 3

**Prompt:** For someone with Medicare and Medicaid MLTC, which plan should cover DME, the Medicare or MLTC?

#### qwen-off

I could not establish a suitable answer from the supplied evidence.

**Limits and unresolved questions**

- No passage directly addresses DME coverage coordination between Medicare and MLTC plans.
- The document's last-modified metadata (pdf\_modification\_date\_raw = D:20250131120205-05'00') was used as a secondary date proxy. It does not establish a legal effective date or supersession. ([MLTC Partial Capitation Model Contract](https://health.ny.gov/health_care/medicaid/redesign/mrt90/2023/docs/part_cap_model.pdf))
- The document's last-modified metadata (pdf\_modification\_date\_raw = D:20161117145515-05'00') was used as a secondary date proxy. It does not establish a legal effective date or supersession. ([MLTC Policy 16.07: Guidance on Task-based Assessment Tools for Personal / Care Services and Consumer Directed Personal Assistance Services](https://www.health.ny.gov/health_care/medicaid/redesign/mrt90/mltc_policy/docs/16-07.pdf))
- Some connected source material remains unresolved or could not be reviewed. The answer does not establish that the sources are complete or current.

**More information needed**

- Which governing date or period should this research cover? Use YYYY-MM-DD or unknown; do not provide personal dates.

Based on the supplied corpus; current legal applicability has not been independently verified.


#### qwen-on

**Qualified research result**

**Source findings — verbatim quotations**

[MLTC Partial Capitation Model Contract](https://health.ny.gov/health_care/medicaid/redesign/mrt90/2023/docs/part_cap_model.pdf), page 156

<details>
<summary>Read the full supporting quotation</summary>

>                           APPENDIX G
> 
>               COVERED &amp; NON-COVERED SERVICES
> 
> 
> Services When Provided, Would be Covered by the Capitation1, 2        Non-Covered Services; Excluded from the
>                                                                        Capitation; Can Be Billed Fee-For-Service
> Care Management                                                             Inpatient Hospital Services
> Nursing Home Care (Residential Health Care Facility)                       Outpatient Hospital Services
> Home Care                                                               Physician Services including services provided in an
> a.     Nursing                                                                  office setting, a clinic, a facility, or in the home.3
> b.   Home Health Aide
> c.     Physical Therapy (PT)
> d.     Occupational Therapy (OT)
> e.     Speech Pathology (SP)
> f.     Medical Social Services
> Adult Day Health Care                                                  Laboratory Services
> Personal Care                                                        Radiology and Radioisotope Services
> DME – including Medical/Surgical Supplies, Enteral and Parenteral         Emergency Transportation
> Formula,4 and Hearing Aid Batteries, Prosthetics, Orthotics, and
> Orthopedic Footwear
> Personal Emergency Response System                                    Rural Health Clinic Services
> Non-emergent Transportation                                           Chronic Renal Dialysis
> Podiatry                                                            Mental Health Services
> Dentistry                                                            Alcohol and Substance Abuse Services
> Optometry/Eyeglasses                               OPWDD Services
> PT, OT, SP or other therapies provided in a setting other than a home.        Family Planning Services
> Limited to 40 visits of physical therapy and 20 visits each for OT, SP and
> other therapies per calendar year, except for children under 21 and the
> developmentally disabled.5 MLTC plan may authorize additional visits.
> Audiology/Hearing Aids                                                      Prescription and Non-Prescription Drugs, Compounded
>                                                                                Prescriptions
> Respiratory Therapy                                                       All other services listed in Title XIX State Plan
> Nutrition
> Private Duty Nursing
> Consumer Directed Personal Assistance Services
> Community First Choice Option services6
> Services Provided Through Care Management:
> Home Delivered or Congregate Meals
> Social Day Care
> Social and Environmental Supports
> 

</details>

[MLTC Partial Capitation Model Contract](https://health.ny.gov/health_care/medicaid/redesign/mrt90/2023/docs/part_cap_model.pdf), page 70

> making diligent efforts to determine if Enrollees have TPHI and
>               to maintain third party information on the WMS/eMedNY Third Party Resource
>             System. If TPHI coverage is known at the POS, the MLTCP shall use the TPHI
>              information to coordinate benefits (e.g., alert the provider and ask them to bill the TPHI
>               that should be primary to the MLTCP).
> 

**Limits and unresolved questions**

- The document's last-modified metadata (pdf\_modification\_date\_raw = D:20250131120205-05'00') was used as a secondary date proxy. It does not establish a legal effective date or supersession. ([MLTC Partial Capitation Model Contract](https://health.ny.gov/health_care/medicaid/redesign/mrt90/2023/docs/part_cap_model.pdf))
- Some connected source material remains unresolved or could not be reviewed. The answer does not establish that the sources are complete or current.

Based on the supplied corpus; current legal applicability has not been independently verified.


### Question 4

**Prompt:** Is an MLTC plan permitted/required to conduct a UAS-NY comprehensive reassessment in a SNF?

#### qwen-off

**Qualified research result**

**Source findings — verbatim quotations**

[MLTC Policy 16.06: Guidance on Notices Proposing to Reduce or Discontinue / Personal Care Services or Consumer Directed Personal Assistance Services](https://www.health.ny.gov/health_care/medicaid/redesign/mrt90/mltc_policy/docs/16-06.pdf), page 4

>               3)   A prior authorization for PCS or CDPAS is not a mistake if it was
>              based on the UAS-NY assessment that was conducted at that time but,
>              based on the subsequent UAS-NY assessment, the enrollee is determined
>                to need fewer hours of PCS or CDPAS than were previously authorized.
> 

[MLTC Partial Capitation Model Contract](https://health.ny.gov/health_care/medicaid/redesign/mrt90/2023/docs/part_cap_model.pdf), page 25

> 		
> 2. Non-Mandatory Counties		
> a. In non-Mandatory counties, the Contractor will use the assessment instrument specified
> by the Department (currently the UAS) to assess each Applicant for eligibility for MLTC
> enrollment. The MLTCP may use additional tools that have been approved by the
> Department in its evaluation of Applicants.
> b. The initial assessment for MLTC eligibility must be conducted within thirty (30) days of
> first contact by an individual requesting enrollment or of receiving a referral from the
> Enrollment Broker or other source. This assessment must be performed by an RN in the
> individual’s home. A Person Centered Service Plan must be developed based on the
> findings of this assessment.		
> 	a. In non-Mandatory counties, the Contractor will use the assessment instrument specified	
> 	by the Department (currently the UAS) to assess each Applicant for eligibility for MLTC	
> 	enrollment. The MLTCP may use additional tools that have been approved by the	
> 	Department in its evaluation of Applicants.	
> 		

[MLTC Partial Capitation Model Contract](https://health.ny.gov/health_care/medicaid/redesign/mrt90/2023/docs/part_cap_model.pdf), page 47

> 5.  The Contractor is responsible for comprehensive assessment and development of a person
>     centered service plan for all MLTC services for Enrollees using CDPAS. However, the
>     Contractor must permit CDPAS Enrollees (or an Enrollee’s representative) to have decision
>     making authority regarding CDPAS staff:
> 

**Limits and unresolved questions**

- The document's last-modified metadata (pdf\_modification\_date\_raw = D:20161117143120-05'00') was used as a secondary date proxy. It does not establish a legal effective date or supersession. ([MLTC Policy 16.06: Guidance on Notices Proposing to Reduce or Discontinue / Personal Care Services or Consumer Directed Personal Assistance Services](https://www.health.ny.gov/health_care/medicaid/redesign/mrt90/mltc_policy/docs/16-06.pdf))
- The document's last-modified metadata (pdf\_modification\_date\_raw = D:20250131120205-05'00') was used as a secondary date proxy. It does not establish a legal effective date or supersession. ([MLTC Partial Capitation Model Contract](https://health.ny.gov/health_care/medicaid/redesign/mrt90/2023/docs/part_cap_model.pdf))
- Some connected source material remains unresolved or could not be reviewed. The answer does not establish that the sources are complete or current.

Based on the supplied corpus; current legal applicability has not been independently verified.


#### qwen-on

**No accepted answer.** response.response.uncertainties[0].question_for_user: expected string or null

## Reproducibility

Adapter changes and offline tests remain isolated on `experiment/reasoning-comparison`, based on the earlier experimental candidate. The default answer pipeline is unchanged. The reasoning adapter has dedicated offline tests for authorization, provider fields, schemas and exclusion of private reasoning; the full offline application suite passed 284 tests. Complete source hashes/snapshots, exact responses, original and reconciled metrics, failed attempts and receipts remain under the ignored local `artifacts/reasoning-comparison/` directory. The [earlier optimization report](advocate-optimization.md) provides the preceding experiment and source-coverage audit.
