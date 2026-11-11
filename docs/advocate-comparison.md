# TRACE advocate question comparison

Completed November 10, 2026 using Bedrock Qwen3 32B and the production TRACE pipeline.

## Quick findings

**None of the seven runs produced a substantive finding, quotation, or citation.** All seven returned status not_found. Six received a fixed capability message after the second research-intent check denied public-research intent. Those six did not search the corpus. Only the simplified DME question reached retrieval and source review, where the selected passages did not address the payment question.

For the wording comparison, the SNF and Service Legacy pairs produced identical displayed responses. The DME pair differed in routing and response text, but both left the question unanswered. Case 4 has no original question to compare. These results expose an overly restrictive intent gate and a retrieval miss. The absence of a researched answer on six runs prevents assessing substantive answer stability across their wordings.

| Case | Simplified versus original | Difference from the supplied acceptable answer |
| --- | --- | --- |
| 1 — Out-of-network SNF | Identical boundary responses. Neither searched. | All substantive coverage conditions and citations omitted, despite relevant indexed sources. |
| 2 — Service Legacy | Identical boundary responses. Neither searched. | Legacy criteria, interruption rule, and policy citation omitted. Policy 25.04 is also missing from indexed evidence. |
| 3 — DME | Simplified searched and reported insufficient evidence. Original stopped before search. | Neither supplied the DME benefit or payment-coordination explanation. Relevant corpus passages were missed by the simplified run. |
| 4 — SNF assessment | Only simplified wording supplied. It stopped before search. | No assessment-location answer or citation. The cited regulation text is also unacquired. |

## Execution, timing, and cost

| Case | Wording | Source search | Model calls | Seconds | Calculated cost, USD |
| --- | --- | --- | ---: | ---: | ---: |
| 1 | simplified | Not run | 2 | 6.190 | $0.000144 |
| 1 | original | Not run | 2 | 3.932 | $0.000204 |
| 2 | simplified | Not run | 2 | 4.172 | $0.000172 |
| 2 | original | Not run | 2 | 3.522 | $0.000197 |
| 3 | simplified | Ran; not_found | 4 | 15.450 | $0.004131 |
| 3 | original | Not run | 2 | 3.875 | $0.000186 |
| 4 | simplified | Not run | 2 | 3.928 | $0.000161 |

Total measured time across submissions was **41.069 seconds**, with **16 model calls** and **1 Titan query-embedding call**. Total calculated cost was **$0.005195**, approximately half a cent. All calls returned valid usage receipts. Latency runs from UI submission through its completed result and includes local index initialization for the first request.

The approved evaluation ceiling was $2 within the unchanged global $10 cap. The ledger now records **$5.897307 committed** and **$4.102693 remaining**. The isolated evaluation grants were disabled after completion.

Costs include routing, drafting/review when reached, and query embeddings. The ledger rounds each calculated charge upward to a microdollar. The US East (N. Virginia) standard rates were verified against the public catalog before execution: $0.15 per million Qwen input tokens, $0.60 per million Qwen output tokens, and $0.02 per million Titan V2 input tokens. These are token-rate calculations, not an AWS invoice. [AWS public price catalog](https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonBedrock/current/us-east-1/index.json).

## Method and comparison criteria

Each supplied question ran once in its own new conversation through LocalApp.submit, the same entry point used by the UI. Controls were Bedrock qwen.qwen3-32b-v1:0, temperature 0, 4,096 maximum output tokens per call, local search over the existing 4,572 Titan V2 vectors, top three initial results, the SQLite document map, and a 16,000-character context-expansion budget. Rules as of remained unspecified for every question. No previous conversation answers were used as evidence.

The supplied acceptable answers were held out from retrieval, generation, and review. Production prompts and source data were frozen for these runs. No failed result was replaced with a retry. Assessment is a qualitative review of claim coverage, quotation support, plan/service/date/assessor scope, uncertainty, and observable routing/retrieval differences. Three pairs and one single question provide examples of behavior, rather than a reliability estimate. The supplied wordings also change facts or requested outputs, so differences cannot be attributed solely to phrasing.

All seven requests were labeled public_rules by the first classifier. The second check returned explicit_public_research=false for six. The application requires both checks to agree before research continues. The resulting six displayed answers were fixed capability messages selected by those routing decisions. The supplied general SNF-coverage and SNF-assessment questions demonstrate clear false negatives in this gate. [Routing policy and decision code](../oracle/request_routing.py).

The question variants are stored with stable IDs 21–27 at the top of the UI list. Original synthetic IDs 1–20 retain their identities. The separate reference file maps each advocate case to its question variants. [Questions](../examples/scenarios.json), [supplied acceptable answers](../examples/advocate-ground-truth.json).

## Case 1 — MLTC single case agreement for SNF discharge

### Comparison summary

Both wordings produced exactly the same displayed boundary response. Neither reached document retrieval. The simplified question clearly asks a general coverage condition, so its rejection is a false negative in the research-intent gate. The original also asks for advocacy experience and arguments, which adds a capability-boundary question, but the first classifier labeled its requested output public_rules and recorded no prohibited category.

Neither response supplies the required out-of-network coverage condition, the network-capacity requirements, the unavailable-bed provision, or any supporting quotation or citation. Neither explains the specific lack of SNF single case agreement authority. The difference from the acceptable answer is complete absence of substantive research, rather than a disagreement about the SNF rule.

### What changed in the question

The original adds Anthem, hospital discharge, one accepting facility after more than 50 referrals, a denied single case agreement, and a request for successful advocacy arguments. The simplified question asks only when an MLTC plan must cover an out-of-network SNF. This pair changes requested outputs as well as length.

### Corpus and reference checks

The local MLTC model contract contains the two-provider provision at PDF page 83, the out-of-network necessary-services provision at page 84, and Appendix O(VII) at page 200. The federal availability provision is also indexed. These source locations were confirmed in a separate local audit. They were never retrieved for either run.

A single case agreement provision exists in the mainstream MMC/HIV SNP/HARP model contract for residential behavioral-health programs. It does not establish an MLTC SNF procedure. No version of that provision was used by these runs.

### Supplied acceptable answer

> An MLTC plan is required to cover out-of-network SNF if no in-network SNF is able to provide necessary services. However, the available sources do not contain any specific reference to “single case agreements” for SNF services.
> MLTC plans must be able to demonstrate to the state that they maintain “a network of providers that is sufficient in number, mix, and geographic distribution to meet the needs of the anticipated number of enrollees in the service area.”  42 CFR 438.207(b)(2).
> “If the provider network is unable to provide necessary services, covered under the contract, to a particular enrollee, the MCO, PIHP, or PAHP must adequately and timely cover these services out of network for the enrollee, for as long as the MCO, PIHP, or PAHP's provider network is unable to provide them.”  42 CFR 438.206(b)(4).
> “The Contractor shall have a minimum of two (2) providers that are accepting new Enrollees in each county in its service area for each covered service in the benefit package unless the county has an insufficient number of providers licensed, certified or available in that county as determined by the Department.”  MLTC Model Contract, Art. VII(D)(3) at p.83.
> “If the Contractor is unable to provide necessary services through its contracted network for a particular Enrollee, the Contractor agrees to adequately and timely furnish these services outside of the Contractor’s network for as long as the Contractor is unable to provide them within the network.” MLTC Model Contract, Art. VII(D)(7) at p.84.
> “If an MLTCP does not have a nursing home to meet the needs of its Enrollees, the MLTCP must authorize out of network placement. If beds are not available at the time placement is indicated, the plan must authorize out of network placement.”  MLTC Model Contract, Appendix O(VII) at p.200.

### Simplified question — scenario 21

When would an MLTC plan be required to cover an out-of-network skilled nursing facility?

### Actual TRACE response to the simplified question

This request is limited to public-rule research; no source search was run.

**Limits and unresolved questions**

-  Public-rule research remains available using non-identifying program and decision attributes.

Request capability check only; no corpus search was run.

### Original question — scenario 22

I got a call on the helpline earlier today from the family of a man who is awaiting discharge from a hospital to a skilled rehab placement, but the one rehab that accepted him (out of the 50+ that the hospital has requested) is out of network with his plan, Anthem MLTC. The rehab submitted a single case agreement to Anthem, and Anthem denied it. My first thought was that I should make a network adequacy argument to the Anthem G&A [grievance & appeals] team to try to get them to reconsider the denial. I haven't actually seen the denial yet. I'm wondering if anyone has been successful in getting a plan to agree to a single case agreement for a rehab, with a network adequacy argument or any other argument?

### Actual TRACE response to the original question

This request is limited to public-rule research; no source search was run.

**Limits and unresolved questions**

-  Public-rule research remains available using non-identifying program and decision attributes.

Request capability check only; no corpus search was run.

## Case 2 — Service Legacy for Minimum Needs Requirement

### Comparison summary

Both wordings produced exactly the same displayed boundary response, with no source search. The routing classifier labeled both requests public_rules, while the second check denied explicit public-research intent. These outputs do not reveal how generation would handle the policy if it were supplied.

Neither response identifies the September 1, 2025 criterion, explains whether interrupted enrollment affects Service Legacy, or asks a focused question about the relevant service/authorization period. Neither cites MLTC Policy 25.04. The acceptable answer’s substantive claims are entirely absent.

### What changed in the question

The original says services ended approximately one year ago and began before the minimum needs change. The simplified question specifies disenrollment in October 2025. A specific date has been added, and the facts are not an identical paraphrase. Both runs used an unspecified Rules as of value.

### Corpus and reference checks

No indexed source contains Service Legacy or Policy 25.04. The acquired MLTC policy index is excluded from answer evidence and does not provide the missing policy text. This is an additional coverage obstacle established by the independent audit. It was not the reason given by either model run, because neither searched.

The supplied reference quotes a before-September-1-2025 criterion but concludes using before-October-2025 wording. Those cutoffs differ. Disenrollment in October alone does not establish when receipt or authorization began. The no-continuous-enrollment claim also needs the policy text for independent verification. The original acceptable answer is preserved below without silently correcting it.

### Supplied acceptable answer

> Service Legacy is “a status for individuals receiving or authorized for PCS or CDPAS before September 1, 2025. Service Legacy will be assessed and reassessed using PCS/CDPAS Legacy Criteria.” MLTC Policy 25.04. Service Legacy does not require continuous enrollment, so it would apply to someone who received personal care services before October 2025.

### Simplified question — scenario 23

For purposes of the Minimum Needs Requirement, does Service Legacy status apply to a person who received personal care services through an MLTC plan until being disenrolled in October 2025?

### Actual TRACE response to the simplified question

This request is limited to public-rule research; no source search was run.

**Limits and unresolved questions**

-  Public-rule research remains available using non-identifying program and decision attributes.

Request capability check only; no corpus search was run.

### Original question — scenario 24

The client previously received home care services through an MLTC plan but discontinued services approximately one year ago. They had been receiving home care before the minimum needs requirement changed. Recently, the client completed a new NYIAP assessment before contacting ICAN and was found ineligible for services.
Based on the information, I feel the client qualifies for service legacy status. I would appreciate any guidance on whether service legacy status applies in this situation and whether there are any additional factors or questions I should consider before moving forward with informal advocacy.

### Actual TRACE response to the original question

This request is limited to public-rule research; no source search was run.

**Limits and unresolved questions**

-  Public-rule research remains available using non-identifying program and decision attributes.

Request capability check only; no corpus search was run.

## Case 3 — Coverage of DME for dual eligible

### Comparison summary

This is the only pair with different displayed responses and execution paths. The simplified question passed both routing checks and ran retrieval, drafting, and review. The original was stopped by the second routing check and received the same fixed boundary response as the other five stopped runs. Neither wording produced substantive findings or citations.

The simplified response says its supplied evidence does not establish which payer covers DME. It omits both the Appendix G DME benefit and the third-party-liability provision. The original gives no answer about Medicare-first submission or whether a denial is required before MLTC consideration. Thus neither establishes the coordination described in the acceptable answer.

### What changed in the question

The simplified question asks which payer covers DME. The original names separate Medicare and MLTC plans and asks whether Medicare submission and denial must precede MLTC consideration. Payment responsibility and a prerequisite for considering a request are distinct questions.

### Corpus and reference checks

Both the Appendix G DME benefit at MLTC contract PDF page 156 and the current 42 U.S.C. § 1396a(a)(25)(A) passage are indexed. The simplified run selected an appeals-presentation cover page, an MLTC enrollment provision, and Policy 17.02 on plan transitions. Parent expansion stayed near those passages. The DME benefit chunk appeared at rank 26 in the saved 30-candidate list, outside the initial top three. It was not supplied to drafting. The statutory payment passage was also absent from the supplied evidence. The reviewer recorded the missing subject evidence but requested no additional search.

The reference’s quoted statute requires reasonable measures to ascertain legally liable third parties. The quoted extract alone does not explicitly establish a universal written-denial-before-consideration requirement. The acceptable answer’s payment-order conclusion and the original question’s processing prerequisite should be assessed separately.

### Supplied acceptable answer

> The MLTC benefit package includes coverage of DME. MLTC Model Contract, Appendix G at p.156. A Medicaid state plan must provide “that the State or local agency administering such plan will take all reasonable measures to ascertain the legal liability of third parties (including health insurers, self-insured plans, group health plans (as defined in section 607(1) of the Employee Retirement Income Security Act of 1974 [29 U.S.C. 1167(1)]), service benefit plans, managed care organizations, pharmacy benefit managers, or other parties that are, by statute, contract, or agreement, legally responsible for payment of a claim for a health care item or service) to pay for care and services available under the plan.” 42 U.S.C. § 1396a(a)(25)(A). The Medicare plan’s possible coverage of DME must be exhausted before the MLTC plan will pay for it.

### Simplified question — scenario 25

For someone with Medicare and Medicaid MLTC, which plan should cover DME, the Medicare or MLTC?

### Actual TRACE response to the simplified question

I could not establish a suitable answer from the supplied evidence.

**Limits and unresolved questions**

- The provided evidence does not contain information about which plan (Medicare or MLTC) should cover Durable Medical Equipment (DME).
- Some connected source material remains unresolved or could not be reviewed. The answer does not establish that the sources are complete or current.

Based on the supplied corpus; current legal applicability has not been independently verified.

### Original question — scenario 26

I am assisting a client with faxing a Service Authorization Request for durable medical equipment (DME) to Fidelis Care at Home. The client has Fidelis Care at Home as their MLTC plan and UnitedHealthcare Dual Complete as their Medicare plan.
Does the DME request need to be submitted to the Medicare plan first and denied before Fidelis Care at Home, as the MLTC plan, can consider covering the equipment?

### Actual TRACE response to the original question

This request is limited to public-rule research; no source search was run.

**Limits and unresolved questions**

-  Public-rule research remains available using non-identifying program and decision attributes.

Request capability check only; no corpus search was run.

### Documents selected for the simplified DME run

| Initial retrieval | Source | PDF page |
| ---: | --- | ---: |
| 1 | Part 438 Workgroup Presentation, cover | 1 |
| 2 | MLTC Partial Capitation Model Contract, eligibility/enrollment | 18 |
| 3 | MLTC Policy 17.02, plan-transition process | 1 |

Expansion added presentation pages 1–2 and contract context at pages 17–18. Its graph record included two unresolved references and a self-reference to Policy 17.02. No additional related document supplied the missing DME/payment rule. Five context or relationship omissions were recorded. The quoted DME benefit is instead on contract PDF page 156. [MLTC model contract](https://health.ny.gov/health_care/medicaid/redesign/mrt90/2023/docs/part_cap_model.pdf#page=156).

## Case 4 — UAS-NY Assessment in SNF

### Comparison summary

Only simplified wording was supplied. No original wording was created, so this case has no paired comparison. The supplied question was classified public_rules by the first check and rejected by the second, with no source search.

The response provides no answer about assessment in a SNF and no quotation or citation. It also omits the residence-evaluation condition and telehealth language in the supplied acceptable passage.

### What changed in the question

The available question asks whether an MLTC plan is permitted or required to conduct a UAS-NY comprehensive reassessment in a SNF.

### Corpus and reference checks

18 NYCRR § 505.14 is present in the source registry with processing status not_acquired. The specific quoted assessment-location text is unavailable as corpus evidence. Other assessment-related passages would require their own retrieval and scope checks. The actual run did not perform those searches.

The question concerns a plan’s comprehensive reassessment, while the quoted reference text names an independent assessment. An explanation should establish the applicable assessor and assessment type, or preserve that limitation. This report does not infer that bridge from the quoted words alone.

### Supplied acceptable answer

> Yes, a plan is required to assess the member in a SNF.  “The independent assessment must assess the individual where the individual is located including the individual’s home, a nursing facility, rehabilitation facility or hospital, provided that the individual’s home or residence shall be evaluated as well if necessary to support the proposed plan of care and authorization or to ensure a safe discharge. This provision shall not be construed to prevent or limit the use of telehealth in the assessment of an individual.”  18 NYCRR § 505.14(b)(2)(i)(c).

### Simplified question — scenario 27

Is an MLTC plan permitted/required to conduct a UAS-NY comprehensive reassessment in a SNF?

### Actual TRACE response to the simplified question

This request is limited to public-rule research; no source search was run.

**Limits and unresolved questions**

-  Public-rule research remains available using non-identifying program and decision attributes.

Request capability check only; no corpus search was run.

### Original question and response

An original question was not supplied for this case.

## What this comparison establishes

The immediate production obstacle is research-intent recognition. Clear public-rule questions can be stopped before the document map, substantive LLM review, or quotation checks are reached. The only source-answering run then missed relevant indexed sections, and its reviewer did not use the available additional-search step. Matching the supplied acceptable answers will also require source coverage for Policy 25.04 and the unacquired assessment regulation, together with careful treatment of the date and scope issues recorded above.

No model finding in these runs can be assessed for quotation entailment because every findings and quotes array is empty. The recorded lack of substantive answers is the primary result. The two audited missing authorities are additional corpus limitations, not explanations supplied by the six runs that never searched.

## Audit provenance

- Public source revision before execution was 1ff836c, with identical production source in private revision 37c748d.
- Corpus snapshot was f63013298e2f9473803a2c9ba6b3534e2f8f15649c89fbc684ab2bd0d418fd25.
- Relationship content digest was e2ed9e4443be13c37057fdb1563855962b7d926c24e3b10a04433a6e3c840adb.
- Local records are retained under artifacts/advocate-evaluation/ as queries.json, controls.json, scenario-NN.debug.json, scenario-NN.receipts.json, source-coverage.json, and criteria.json. They include the observable routing and review decisions, evidence records, and usage receipts.
- The local preflight report is retained separately as a preparation record. This completed report supersedes its pending-execution status.
