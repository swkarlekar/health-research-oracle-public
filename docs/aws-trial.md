# SSO and the bounded AWS evaluation

AWS CLI v2 is installed for the current user under `~/.local`. Its bin directory
is included in `.zshrc`. The `oracle` profile uses a modern refreshable SSO session
in `us-east-1`. Configuration and cached credentials remain outside this repository.

Sign in again when the SSO session expires:

```sh
aws sso login --profile oracle
```

The evaluation uses `boto3.Session(profile_name='oracle')` explicitly. Expired
access-key exports in an older shell therefore do not select the trial's
credentials. Interactive sign-in/MFA can still be required after the SSO session
expires; this does not create permanent access keys.

The user authorized one actual 15-scenario trial with a strict $10 maximum.
`python -m oracle.aws_evaluation` only prepares an offline plan and export.
Execution additionally requires `--execute --profile oracle --expected-account
ACCOUNT_ID`, using the verified account. These flags are not a substitute for
obtaining explicit user approval. The local chat UI never runs this controller.

The controller uses Qwen3 32B (`qwen.qwen3-32b-v1:0`), Titan Text Embeddings V2
with 1,024 dimensions, Bedrock Knowledge Bases, S3 Vectors, private S3 source
objects, and the existing local SQLite relationship/context pipeline. It creates
a uniquely named temporary CloudFormation stack. The trial template changes the
review template's retention policies to deletion. No provisioned inference,
OpenSearch cluster, Lambda hosting, advanced parsing, OCR, or reranker is enabled.

The single ledger at `artifacts/aws-eval/budget.sqlite` survives restarts and
cannot have its cap changed. Atomic reservations precede inference/retrieval.
Failed calls retain their upper bounds, SDK retries are disabled, and any receipt
that exceeds its reservation stops subsequent paid work. The trial allows at most
75 generation calls and 60 retrieval pages, plus one bounded ingestion. These are
the original trial's cumulative limits, including rejected attempts and retries.
It reserves
$1 for setup/ingestion and $2 for storage, cleanup, and safety. The reviewed export
contains 9,144 files totaling 11,634,274 bytes. Source text is below 10 MB, with no
chunk over 3,435 bytes. Corpus size guards block larger trials. An opt-in upload retry is limited to a DNS resolution failure before a socket opens; it reserves another action-grant attempt. Other upload errors are not retried.

Public US-East pricing is pinned in [aws-eval-prices.json](../config/aws-eval-prices.json).
Qwen costs $0.15 per million input tokens and $0.60 per million output tokens;
Titan embeddings cost $0.02 per million tokens. Before each Converse request, the
runner reserves against the serialized request's UTF-8 byte count plus 1,024
formatting tokens and the 4,096-token output cap. This deliberately conservative
byte-based bound avoids assuming a characters-per-token ratio. Unreceipted calls
retain their reservations; the interrupted legacy call has a $0.084 reservation.
Each retrieval reserves $0.002 for query embeddings/vector operations. These are
published-rate calculations and conservative estimates, not AWS invoice receipts.
AWS Budgets does not provide a universal instantaneous spending kill switch.

Local outputs under `artifacts/aws-eval/` include:

- `queries.csv` and `queries.json`: each scenario's status, full wall time,
  model/retrieval seconds, call counts, token totals, calculated model cost,
  conservative query cost, and any failure.
- `scenario-NN.md` and `.debug.json`: accepted answers and exact evidence/review
  diagnostics. Rejected answers retain diagnostics without an accepted demo answer.
- `sdk-receipts.json` and `budget.json`: usage recorded before parsing, including
  calls whose answer JSON or citations are rejected. Query costs exclude one-time
  corpus setup; setup and safety reservations are reported separately.
- `ingestion.json`, `state.json`, `failure.json`, and `trial-template.json`:
  ingestion statistics, ownership/cleanup state, failure details when relevant,
  and the exact deployed template. No credentials or private reasoning are saved.

Cleanup runs even on failure, deleting only the new trial stack and its manifest's
uploaded object keys. Original documents, graph, UI history, and local reports
remain. The approved export preserves all 4,572 Titan vectors and metadata in
`vector-cache.sqlite`, with dimension/count/checksum verification. The separately
approved restore reused those vectors in a new KB and successfully retrieved them
without document re-embedding or an ingestion job. Source bindings and data-source
IDs must be rebound on restore; this is not an export of Bedrock's sync state.
The additional restore/retest allowance is a persisted $0.60 stage ceiling within
the original $10 cap; resumes cannot reset it. Future trial data sources use
`RETAIN` while their owned backing index is deleted afterward by CloudFormation.
Ingestion progress is saved on each poll and can take tens of minutes for this
pre-chunked corpus. The runner allows one hour for the existing job.
If the local monitor stops before evaluation begins, stop the old monitor first,
then use `--resume-ingestion --profile oracle --expected-account ACCOUNT_ID` to
continue that same job and the original 15 scenarios. Resume does not upload or
start another ingestion job, and refuses to repeat any evaluation call.

If interrupted, use `--cleanup --profile oracle --expected-account ACCOUNT_ID` to
resume cleanup of the recorded stack. A recorded trial blocks a second automatic
deployment. `--retry-setup` permits one deliberate retry only after verified cleanup
and only when ingestion never started; the original ledger and first attempt
records are retained. It does not repeat a completed model evaluation. Verify `state.json` says `cleaned_up: true`; unresolved cleanup must be
handled before leaving paid storage unattended. The ledger is an application
control; IAM restrictions and successful cleanup remain part of the cost boundary.

Sources: [AWS SSO setup](https://docs.aws.amazon.com/cli/latest/userguide/cli-configure-sso.html),
[Qwen3 32B model card](https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-qwen-qwen3-32b.html),
[official Bedrock regional price catalog](https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonBedrock/current/us-east-1/index.json),
[S3 pricing](https://aws.amazon.com/s3/pricing/).

The Qwen adapter requests `/no_think` and discards separate Bedrock
`reasoningContent` blocks, including the empty block documented by AWS when
thinking is off. Only answer text is parsed as strict JSON. Tool calls and other
unsupported content remain rejected; private reasoning is never logged.

## Observed trial results and content limits

All 15 scenarios were attempted: 13 passed the mechanical response contract as
partial answers and 2 were rejected. Manual comparison of findings against their
selected quotations found material content failures, including Medicare Advantage
rules applied to Medicaid renewal, a complaint directory cited for spend-down,
and a study/report passage treated as an operative eligibility rule. The same
model's second pass missed these problems. Several native structured responses
also contained punctuation-only findings: the decoder treated a sentence-ending
substring pattern too restrictively. Native decoding now omits patterns; complete
local validation remains, and a separate deterministic check rejects findings
without alphabetic text. These changes have offline regression coverage; no
subsequent paid inference has confirmed their output quality.

`content-audit.json` records review outcomes bound to exact result hashes. Saved
AWS examples are withheld from the UI unless their exact result has a recorded
content review. Original answers, debug traces, costs, and attempt archives remain
available for inspection. No current AWS answer is labeled content-reviewed.
The local Qwen3.6 and Bedrock Qwen3 32B have not had a controlled 15-case comparison
using identical evidence and prompts; no quality-equivalence claim is justified.

The local [trial report](../artifacts/aws-eval/report.md) and
[CSV](../artifacts/aws-eval/queries.csv) record every query's latency and cost.
Calculated model charges with receipts total $0.160183; one interrupted call has
unknown usage. The committed application upper amount is $4.672210, including
conservative setup/export/restore and safety/storage reservations, not an invoice.

The restored and original populated trial stacks are now deleted. The original
data source initially remained `DELETING` after a CloudFormation timeout; the
cleanup monitor detected its eventual removal and completed the stack deletion.
`state.json` records `cleaned_up: true` and `prior_cleanup_complete: true`. The
verified local vector backup is retained. The later local-retrieval evaluation
creates no managed KB, vector bucket, source bucket, ingestion, or hosting.

For the small demo, local retrieval with Bedrock generation is a practical next
configuration: SQLite FTS5, canonical text, and the relationship graph already run
locally. A local search adapter for the saved Titan vectors is still missing.
It would require matching Titan query embeddings, which remain paid requests
unless already cached. The graph alone is not a vector index or source of new
embeddings. The Bedrock model receives the selected evidence through Converse;
it cannot directly read a laptop's SQLite files.
