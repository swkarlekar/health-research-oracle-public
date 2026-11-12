# The prompts behind the research harness

Each step of the pipeline in [How the harness works](harness.md) has one instruction text. They live in
[`oracle/pipeline_prompts.py`](../oracle/pipeline_prompts.py); this page quotes the parts that carry the
design. A prompt states intended behaviour. Models do not always follow it, which is why the program
validates every quotation and why a separate step looks for doubt.

## Request check

Two calls in [`oracle/request_routing.py`](../oracle/request_routing.py) see only the question. They
separate research about public rules from requests the tool must decline:

```text
A request about which requirements or criteria apply, when an action is permitted or required,
or what rules can inform a proposed request or argument is public-rule research. This remains
research when phrased briefly, with anonymized circumstances, past events or informal wording.
```

A question that asks only for private case access, a guaranteed outcome, a verdict on an individual, or
confirmation of an accusation gets a fixed explanation and no search. A mixed question continues to research
with the limit stated.

## Plan

The planner is told how search works, so that it writes search texts in the sources' voice:

```text
passages are declarative legal text ("The Contractor shall...", "A State plan for medical assistance must
provide...", "The assessment must..."). They never contain the advocate's story, plan brand names, or
question words. Short, single-idea search texts in the sources' own vocabulary work best.
```

and to think one level more general than the question:

```text
3. legal_concepts: 5-8 terms of art that a statute, regulation or contract would use for the rules that
   control these issues - headings you would look up in an index. Think one level more general than the
   question, and think about every party involved:
   - a question about a specific service, facility or setting is governed by general rules about covered
     benefits, provider networks, access, and where/how the activity is carried out;
   - a "reassessment" or "renewal" is governed by the rules for the underlying assessment or determination;
   - a question about which of two payers, programs or plans is responsible is governed by the rules that
     order payers and assign liability among insurers;
   - a status or label is governed by its definition and by the rules for gaining, keeping and losing it.
   Each concept is 2-6 words, no acronyms that are not in the sources.
```

## Read

The reader screens small batches of numbered passages. It is told which mistake is worse, and what counts
as a governing rule even when the passage never mentions the question's subject:

```text
A later step writes the answer from whatever you keep, and it will see nothing else - so missing a
useful passage is much worse than keeping an extra one. But the later step also reads everything you keep,
so do not keep passages that only share a topic word with the question.
```

```text
2 = directly answers a later issue, or states a more general rule that governs the situation even though
    it does not name the specific service, facility, plan brand or label in the question. This includes: a duty about "providers",
    "services" or the plan's "network" when the question is about one kind of provider or service; a rule
    about "third parties", "other insurance" or who pays first when the question involves a second payer;
    rules for an "assessment" when the question is about a reassessment; and the definition of a status or
    term in the question, how it is granted, kept or lost, and the definition of any similarly named status
    it could be confused with. It also includes: a benefit list or table row showing whether the service or
    item in the question is covered (keep the row and the table's heading); a duty to maintain, demonstrate
    or document enough providers, capacity or access; and any rule stated under one of the listed
    legal_concepts - the general doctrines a researcher flagged for this question - even when the passage
    never mentions the question's own subject.
```

## Triage

Triage chooses what the writer will see:

```text
First write needs: one to three short questions, in your own words, that the answer has to resolve. Think
about what would settle the advocate's problem, not just the literal wording:
- If the question names a tactic, label or document that the sources may not mention, the need is the
  general duty that governs the underlying situation.
- If two payers, programs or plans could each be responsible, there are two needs: whether the item is in
  the benefits of the plan asked about, and which payer has to go first.
- If the question is whether a status applies, the needs are how the status is defined and gained, and
  whether the event in the question causes it to be lost.
```

```text
- Breadth beats repetition: do not spend more than two picks on the same rule from the same document, and
  do not pick a string of sub-paragraphs from one list when one or two carry the rule.
- Leave out background and procedure the advocate did not ask about (notice contents, appeal steps,
  timeframes, record keeping).
```

## Answer

The writer sets out the findings before the conclusion, and is told how to handle the two reasoning errors
seen most often:

```text
   - Keep similarly named terms apart: apply only the rules written for the status or term the question
     asks about. A condition attached to a different status (for example a continuous-enrollment
     requirement) does not carry over unless a passage says so; if a passage states the difference
     directly, it controls.
   - Where two payers, programs or plans could be responsible: say whether the item is in the benefits of
     the plan asked about, then which payer goes first. If the passages make one payer the last resort or
     another payer primary, the primary payer's coverage has to be pursued first, whatever words the
     question uses; note what the passages leave open about how (for example whether a formal denial is
     needed).
```

```text
3. answer - one to four sentences that answer the question as asked, resolve each need, and follow from the
   basis. Lead with the conclusion and its conditions ("Yes - ...", "No - ...", "X pays first; Y covers ..."). Check it
   against your findings before writing it: it must not contradict any of them. State the rule as it
   applies to facts like those in the question ("a person who ... before September 1, 2025 would ...");
   do not decide a real person's case, predict an outcome, or tell the advocate what to do.
```

## Support check

Each finding is compared only with the passages it cites:

```text
For each finding, compare its text with its passages, sentence by sentence, and give a verdict:
- "supported": every statement in the finding is stated in its passages. A faithful paraphrase is
  supported. Naming the source more or less specifically than the label is fine.
- "overstated": the passages state the core rule, but the finding adds something they do not say - a
  condition, exception, actor, setting, number, or a wider group than the passage covers. Give
  corrected_text: the finding rewritten so it says only what the passages state (one or two sentences).
  corrected_text must differ from the original.
- "unsupported": the passages do not state the rule in the finding at all. corrected_text is empty.
```

This step cannot rewrite the answer.

## Second reader

A separate call looks for what should be flagged before an advocate relies on the answer:

```text
Look for:
- a passage whose wording can fairly be read two ways, where the readings would change the answer - for
  example a sentence where it is unclear which group an exemption, an "unless", or a "must continue"
  clause covers. If the draft answer has adopted one reading, say so and give the other;
- an exemption, exception, suspension or special rule in any passage that touches the situation in the
  question (a setting, a status, a type of enrollee, a time period) and that the answer either relies on or
  passes over;
- two passages that point in different directions, or give different numbers or timeframes for what looks
  like the same requirement, where neither says which controls;
- a passage the answer applies to the plan or program in the question although the passage is written for
  a different actor, plan type or program, or it is unclear whom it covers;
- a passage that may be out of date on its face (a temporary measure, a dated transition rule).
- something the advocate asked, or a label or tactic they named, that no passage addresses. Say plainly
  that the passages do not mention it.
```

Each doubt comes with the possible readings and a follow-up question, and appears under the answer with
the passages involved. The instruction is to raise a doubt rather than leave it out.

## The original loop

Local models use an earlier, simpler flow with one drafting prompt
([`text_corpus/config/document_only_system.txt`](../text_corpus/config/document_only_system.txt)) and one
review prompt (`REVIEW_POLICY` in [`oracle/harness.py`](../oracle/harness.py)).
