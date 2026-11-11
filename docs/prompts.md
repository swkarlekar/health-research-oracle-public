# The prompts behind TRACE

These are the experimental `experiment/advocate-ground-truth` candidate policies. They were not promoted to the default answer pipeline. They describe intended behavior, not proven legal correctness. Valid JSON, an exact quotation, and a positive model-review flag do not establish that a result answers the question. The [original advocate comparison](advocate-comparison.md) is the dated baseline; the [optimization report](advocate-optimization.md) preserves later candidates, failures, and the frozen final retest.

## Request routing and search planning

The [request guard](../oracle/request_routing.py) uses two separate structured calls. The first classifies requested outputs, including the parts of a mixed request. The second independently checks public-research intent and proposes search terms. Both receive the question and validated scope; neither receives source text or reference answers. Both must allow research before retrieval proceeds.

Ordinary questions about coverage conditions, exceptions, payment responsibilities, and assessment procedures can proceed without saying “general” or “research.” A narrative or tentative belief is not itself a request for a personal verdict. Requests solely for private access, guarantees, allegation confirmation, unsupported coverage certification, or exact individual outcomes still stop before retrieval. Public research in a mixed request proceeds with fixed capability limits attached only after the ordinary answer gates pass.

The second call separates `service_or_decision` from `rule_topic`. Python binds the first query's program/plan prefix to the selected scope and literal coordination programs in the question; the model cannot reinterpret MLTC as Medicare Advantage. A concise original question remains the first query. The second query uses the broader program and crosscutting topic so statutes need not repeat the service or plan name. Explicit out-of-network and reassessment terms survive a lossy rewrite. Controlled query-only vocabulary normalizes skilled rehabilitation to a skilled-nursing-facility search term and coordination of benefits to third-party-liability vocabulary. These are fallible retrieval leads, not assertions of service equivalence, coverage or payer priority. The original question and scope remain unchanged.

At most two planned queries, each at most 180 characters, pass input and generated-URL checks. The original user input limit is 1,600 characters. Routing `request_text` is only a diagnostic annotation and permits that same length; it does not authorize research independently of the two classifications.

## Hybrid retrieval and canonical context

The [local retriever](../oracle/local_vectors.py) fuses up to 30 exact Titan cosine candidates with up to 30 approved SQLite full-text candidates. Reciprocal-rank fusion uses a constant of 20. An explicit plan-title match boosts lexical ranking by 1.8 and the fused score by 1.5. Distinct source sections are selected across the planned queries, with up to six initial seeds in the UI. This deliberately differs from managed AWS knowledge-base ranking. A failed or unapproved semantic query does not silently fall back to lexical search.

[Context expansion](../oracle/context.py) retains whole canonical passages within a 16,000-character total source budget, including raw hits. Initial expansion reserves 4,000 characters for later retrieval. Whole sources that do not fit are omitted with reasons, not truncated. Canonical parent sections/pages, table context and bounded graph relationships supply qualifications. For a large HTML section that cannot fit, only the exact anchor and bounded neighboring chunks may be loaded; that window is explicitly incomplete. No entire long document is dumped into the model.

After drafting, selected quotation and gap anchors receive priority for parent expansion. **Unselected raw hits and loaded relationship qualifications remain available to independent review**, so a draft omission cannot hide a useful retrieved source. Unselected parent context may be reprioritized within the same bound. Previous packets and omissions remain in debug history. Evidence IDs use a monotonic high-water mark; a raw excerpt fully contained in a parent is represented once in model input, with an explicit raw-to-parent mapping. No source identity or text is invented.

The [contract-product guard](../oracle/source_scope.py) conservatively excludes explicitly different model-contract products when the selected plan is known, the question does not request the other product, and the passage contains no explicit reference to the requested product. It respects joint labels, requested comparisons, unknown scope, and general regulations. Unqualified MLTC uses the project's partial-capitation research scope; a matching product never proves applicability. This is a narrow provenance check, not a legal determination. Excluded snippets and reasons stay in the audit.

There is **no separate model relevance screen** in the final pipeline. Earlier screened candidates remain in the experimental artifacts. A coarse chunk-level screen sometimes discarded a responsive later paragraph, so exact quote selection and independent review now perform semantic relevance selection.

## Extractive source findings

Normal routed research uses one shared [`EXTRACTIVE_POLICY`](../oracle/harness.py), followed by a short review-specific task in the independent review call. It does not stack the legacy paraphrase instructions with a contradictory extractive override. The [legacy document-only prompt](../text_corpus/config/document_only_system.txt) remains for nonrouted synthesis integrations.

The extractive policy's central instruction is:

```text
Select the SMALLEST set of exact passages that directly answers a requested issue.
One complete responsive paragraph is enough. Select zero if nothing answers it.
Six is a maximum, not a target.
```

`answer_style="source_quotations"` identifies these results. `findings` and `scope.assumptions` are empty; canonical quotations carry the substantive rule text and are displayed as quotations. The model cannot move rule paraphrases or advice into uncertainty fields. Legacy saved results retain their prior rendering.

The [quotation adapter](../oracle/quotations.py) accepts only supplied passage IDs such as `E1.Q2`. Python inserts the unchanged text, offsets and source citations. Paragraph units can extend to 4,000 characters to avoid splitting a table or conditional paragraph mid-row. The model must still select needed headers, material footnotes, conditions and exceptions. Extraction alone does not prove that these were selected correctly.

The provider's native schema retains the actual evidence-ID and quote-ID enums; complete local schema and canonical-span checks remain authoritative. Only identical scalar references may be deduplicated, with the removed positions recorded in usage. Unknown IDs, altered scope, invalid spans and unsupported source substitutions remain errors. There is no substantive repair or unmetered retry.

## Independent review, gaps and clarifications

The reviewer sees the draft and actual canonical evidence, including retained raw candidates. It must vet each quotation's contribution to the requested issue, preserve responsive conditions, remove unrelated material, and record short public source-based keep/drop/gap conclusions. Support, applicability, responsiveness, dates/versions, conflicts/exceptions and research-boundary checks remain separate. Quote-bearing results cannot pass when support, applicability or responsiveness is false. A `supported` result cannot retain unresolved review issues. Boundary rejection still prevents an accepted answer, even if the system could otherwise display a useful gap.

The reviewer may request **one** additional corpus search for a missing issue or cross-reference, followed by one final review. Normal researched requests therefore use four model calls, or at most five with the second search. Boundary-only requests use two; evidence-only requests use none. Empty/excluded evidence can omit a draft. The same selected model may perform routing, drafting and review in separate calls; these are not independent legal experts.

The policy distinguishes ordinary eligibility from a named exception, benefit inclusion from payer priority or a denial prerequisite, initial assessment from reassessment, and one assessor from another. Gaps must describe missing support, never a negative legal rule inferred from absence. An empty final extractive selection cannot be labeled supported or partial; it is normalized conservatively to `not_found`, retaining its uncertainty and tracing the original status.

[Initial scope collection](../oracle/policy.py) remains model-free. Post-retrieval clarification is limited by native enums to approved non-identifying questions for unspecified program, plan type or governing date. Both `clarifying_questions` and uncertainty `question_for_user` are constrained. The model cannot request a legal definition, missing policy, private facts or records through those fields. A date explicitly marked unknown usually calls for a currency limitation, not a repeated question; final tests assess whether the model follows that instruction.

Dates in document text take priority. Only actual available embedded last-modified metadata candidates can be selected as secondary proxies, and every reliance is displayed. Proxies do not establish effective dates or supersession. Download dates, a newer file, a source inventory and graph leads cannot establish operative law. The final Python proxy check remains independent. Missing sources and unavailable provisions remain explicit research limitations.
