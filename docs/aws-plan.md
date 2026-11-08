# AWS architecture and approval boundaries

This document describes the initial architecture and base adapters. A later
user-authorized $10 trial ran against real AWS services; see
[the trial record](aws-trial.md) for current results, vector export/restore, cost
controls, content failures, and outstanding cleanup. Statements below about the
initial undeployed template are historical, not the current trial status.

The user requires explicit approval before any paid AWS service use. Credential availability is not approval. No AWS credential inspection, identity request, storage request, embedding call, retrieval call, model call, deployment, or ingestion job was needed for local development.

## Proposed architecture

| Component | Local development | First approved AWS trial |
| --- | --- | --- |
| Original source archive and canonical SQLite | Existing local corpus package | Private S3 archival prefix, separately reviewed |
| Search | SQLite FTS5 | Bedrock Knowledge Bases with S3 Vectors |
| Embeddings | Not generated; managed retrieval can be mocked for local control-flow tests | User-selected Titan Text Embeddings V2; document and query embeddings use the same model |
| Ingestion | Existing exact-text extraction/chunking | Prechunked UTF-8 files and `.metadata.json` sidecars in S3; chunking `NONE` |
| Context expansion | Complete short document or bounded section/page/provision context from canonical local text | Same local Python expansion after Bedrock `Retrieve` |
| Relationship graph | Canonical source nodes, exact references, semantic candidates, and append-only interpretation/review records in local SQLite | Keep local for the demo; no Neptune dependency |
| Generation and review | Installed Ollama model | A selected Bedrock text model via boto3 `converse` |
| Harness/orchestration | Local Python | Keep local first; consider Lambda after behavior and cost evaluation |
| Citations | Canonical local chunk store | Same pinned store, resolving managed-retrieval IDs against canonical text |

S3 Vectors is supported as a Bedrock Knowledge Bases vector store. It is a candidate for the small corpus, not a claim about the cheapest option for an unmeasured workload. Check the selected region and embedding/model availability before approval. See [AWS S3 Vectors integration](https://docs.aws.amazon.com/AmazonS3/latest/userguide/s3-vectors-getting-started.html), [S3 pricing](https://aws.amazon.com/s3/pricing/), and [Bedrock pricing](https://aws.amazon.com/bedrock/pricing/).

Use separate `Retrieve` and `Converse` calls so the application retains control of the review loop, exact quotation construction, and abstention policy. The adapters use the documented [boto3 Retrieve API](https://docs.aws.amazon.com/boto3/latest/reference/services/bedrock-agent-runtime/client/retrieve.html) and [Converse API](https://docs.aws.amazon.com/boto3/latest/reference/services/bedrock-runtime/client/converse.html). They have only been tested with local mocks.

The user selected Titan for direct integration while retaining local context expansion and the relationship graph. This does not approve deployment, uploads, embeddings, retrieval, or inference charges. Stable chunk/document IDs bridge managed retrieval to local source and relationship lookups. Titan's embedding space does not constrain the graph schema. See the [selected demo architecture](architecture.md#selected-demo-architecture) and [runbook](demo.md) for implementation boundaries and current status. The SDK adapters have been checked with botocore Stubber and monkeypatched end-to-end calls, not real AWS services.

## Reviewable local artifacts

`python -m oracle export-aws` generated **4,572 text files + 4,572 metadata sidecars**, totaling **11,634,274 bytes** in this snapshot. Its local `upload-manifest.json` records every file hash, byte count, and the corpus snapshot. The manifest itself is not a source document and is not uploaded by the adapter. This design trades additional small S3 requests for stable existing chunk identities and exact quotations.

The export includes only the 58 eligible evidence documents' chunks. It omits the 24 missing targets and 26 collection/index targets. It does not fetch their descendants, infer missing source content, or archive the original raw bytes. Archive upload is a separate future scope.

The [CloudFormation template](../infra/stack.json) prepares a private encrypted S3 bucket, S3 vector bucket/index, scoped Bedrock ingestion role, vector Knowledge Base, and S3 data source. Every resource is conditioned on an explicit `PaidResourcesApproved=yes` parameter; the default is `no`. The template has not been deployed or validated against a live AWS account. It does not start ingestion, create inference endpoints, enable query logging, provision a VPC/NAT gateway, create an always-on database, or host the application.

The index marks Bedrock text and metadata keys as non-filterable, following [AWS's S3 Vectors example](https://aws.amazon.com/blogs/aws/amazon-s3-vectors-now-generally-available-with-increased-scale-and-performance/). `snapshot_sha256` remains a filterable custom attribute. Prechunked files use [the documented `NONE` chunking strategy](https://docs.aws.amazon.com/bedrock/latest/userguide/kb-data-source-customize-ingestion.html). The ingestion role follows [AWS's S3 and S3 Vectors permission requirements](https://docs.aws.amazon.com/bedrock/latest/userguide/kb-permissions.html).

The template retains document/vector data on stack deletion or resource replacement. **Retained data can continue to incur storage costs.** An approved cleanup must enumerate and delete retained objects/indexes/buckets as appropriate. Deleting the stack alone is not a cleanup plan. Titan Text Embeddings V2 is the selected architecture choice; the default 1,024 dimensions remain a proposed trial configuration. No paid AWS activity is approved by either choice.

## Approval sequence

1. Finish local harness tests and inspect local model behavior. No AWS use.
2. Select region, maximum trial spend, embedding model, generation model, and retention duration. Obtain current regional prices and estimate each separate charge below. Do not treat a free-tier assumption as authorization.
3. Ask the user to approve the concrete infrastructure and storage trial. Only after approval, deploy the reviewed stack and obtain its actual output IDs. Do not run a deployment as a price or permission test.
4. Review the exact upload manifest, bucket, prefix, bytes, request count, estimated storage duration, and costs. Record explicit approval for that scope; upload only those files.
5. Ask separately before starting ingestion/embedding if it was not included in the preceding explicit approval. First ingest a small sample in a dedicated test prefix to validate metadata, no-chunking behavior, filters, and quote identity.
6. Approve a finite retrieval/model smoke trial with exact model/KB IDs, prompt/output limits, call limits, and estimated cost. Run from the laptop before considering Lambda.
7. Evaluate quality and cost, then obtain approval before expanding ingestion, deploying hosting, or changing a model or region.

The application gate is a defense against accidental use through these adapters. It does not stop someone from using other AWS clients, tools, the console, or service-side ingestion. Use scoped IAM permissions as an additional control. A local file ledger is suitable for a single laptop trial; distributed Lambda instances require a shared atomic call budget and a separate hosting design.

## Application grant contract

`ApprovalGate` accepts a local JSON file only after explicit approval has been obtained. Never generate an enabled grant merely because credentials exist. The file must contain `user_approved: true`, an unexpired timezone-aware `expires_at`, exact `action`, `region`, `resource`, a finite `max_calls`, and `used_calls` starting at zero. It is excluded from Git as `aws-approval.json`.

| Action label | Exact resource object |
| --- | --- |
| `s3:PutObject` | `bucket`, normalized trailing-slash `prefix`, `manifest_sha256` of canonical manifest JSON |
| `bedrock:Retrieve` | `knowledge_base_id`, `max_results: 30`, `snapshot_sha256` |
| `bedrock:Converse` | `model_id`, `max_output_tokens`, `max_prompt_chars` |
| `bedrock:StartIngestionJob` | `knowledge_base_id`, `data_source_id`, `snapshot_sha256` |

These are application gate labels. `bedrock:Converse` is an API label here; IAM authorization for Converse uses `bedrock:InvokeModel` as documented by AWS. A grant for one action cannot enable another action. A failed network attempt consumes a reserved call; every retrieval page consumes an attempt. SDK retries are disabled. Construction of a boto3 client occurs only after the corresponding grant has passed. An ingestion grant approves the named data source's actual remote scope; its local snapshot hash is an audit/idempotency binding, not proof that the remote prefix contains only the reviewed files.

Calls and prompt/output limits are enforced; **a dollar spending ceiling is not**. Tokenization varies, service-side ingestion has its own activity, and storage is ongoing. AWS Budgets alerts alone should not be treated as a hard kill switch. The approval request must include an estimate and explicit retention/cleanup scope. No broad `AWS_ENABLED=true` setting exists.

The separately authorized [15-scenario trial](aws-trial.md) adds an atomic dollar
reservation ledger around these adapters, a bounded corpus ingestion, and deletion
of its temporary resources. That controller's $10 scope does not authorize general
AWS use or persistent hosting. The statement above describes the base action-grant
adapter alone, which is a call limit rather than a dollar meter.

## Cost worksheet for approval

Do not enter assumed dollar rates into the code as if they were measured charges. Complete this worksheet with current prices for the actual region and model:

| Charge | Quantity to estimate |
| --- | --- |
| S3 source upload | 9,144 PUTs for full canonical export, plus any separately approved archival objects |
| S3 storage/read | Export GB × retention duration; ingestion GET/LIST requests; any original archive |
| Embeddings | Actual source tokens for initial ingestion and changed/reingested files; query embedding tokens |
| S3 Vectors | Vector dimensions/count and metadata storage; writes; query request and processing charges |
| Bedrock draft/review | Two calls normally, three with one additional search; per-call input and output tokens |
| Hosting later | Lambda requests/runtime, API Gateway if chosen, logging, and any shared budget store |
| Optional extras | Reranking, advanced parsing, OCR, Guardrails, KMS, cross-region routing, and data transfer only if separately selected |

The sample 11.6 MB export is small, but document count alone cannot determine total cost. Avoid optional reranking, advanced parsing, managed ingestion schedules, and hosting until their benefit is demonstrated and their charges are approved. The original corpus's illustrative per-token/GPU estimates are historical planning inputs and are not AWS price quotes.
