# Independent code organization review

A separate adversarial agent reviewed the implementation after the functional graph and AWS agents. It examined ownership, duplication, module boundaries, unnecessary compatibility code, test coupling, documentation, and packaging. No AWS calls were made.

## Applied changes

| Finding | Change |
| --- | --- |
| CLI parsing, execution, and mutable connection-ownership flags were intertwined | Small explicit handlers in the same CLI module; a short dispatcher; `ExitStack` owns connections once |
| Every local command loaded AWS configuration | Configuration is now read only for AWS-backed operations or the API simulation |
| A missing graph could block scope clarification | Shared clarification logic runs before database, configuration, or model initialization |
| Context methods had unused parameters and a redundant append helper | Removed the arguments and helper; evidence insertion has one deduplication point |
| Production context code accommodated weaker synthetic legacy fixtures | Fixtures now carry real canonical text hashes; production requires that hash |
| The new review API had duplicate parameter aliases | One decision argument, one reviewer argument, and one candidate-hash binding |
| Test modules imported shared fixtures from other executable test modules | Shared synthetic data/providers moved to `tests/support.py`; specialized graph/review fixtures remain local |
| README duplicated the architecture, approval policy, and runbook | README now focuses on quickstart, current status, and links to detailed documents |
| Wheel installation would omit the preserved corpus script/config resources | Checkout-only execution is explicitly documented; standalone packaging remains deferred |

The CLI file is slightly longer because handlers are named explicitly, while its dispatcher is much shorter and resource ownership is simpler. The refactor adds no controller framework, provider registry, or repository abstraction. Context compatibility branches and documentation duplication were removed rather than introducing additional layers.

## Boundaries retained deliberately

`corpus.py` handles source selection and compact model views. `context.py` expands canonical evidence and follows graph leads. `relationships.py` builds and audits the local map; `relationship_review.py` interprets and reviews candidate relationships. `harness.py` handles answer draft/review and final qualifications. `aws.py` implements approval-gated SDK adapters. `demo.py` contains the explicit offline monkeypatches.

Validation at separate trust boundaries remains intentional. Independent functional review reproduced altered-text/page metadata, stale graph packets, and relationship-trace attacks. The fixes bind source text to the original corpus, graph metadata and review chains to content hashes, and model-facing relationship claims to effective canonical records. These checks serve different trust boundaries and should not be collapsed merely to reduce line count.

Remaining simplifications should be driven by use: expanded context can eventually replace fully covered seed text to reduce prompt duplication, but preserving stable evidence IDs across additional retrieval takes priority. A browser UI, packaging framework, hosted graph database, and generic model registry were not introduced for this demo.
