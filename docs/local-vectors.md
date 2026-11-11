# Local Titan vector retrieval

The read-only local index searches 7,044 Titan Text Embeddings V2 float32
vectors: 4,572 preserved from the managed export and 2,472 new shared-corpus
chunks embedded directly with the same model/settings. Its manifest records both
origins. See [the recovery audit](source-recovery.md) for source coverage and
qualifications. Historical AWS ranking comparisons below used only the original
4,572-vector snapshot.

The index computes exact cosine distance over permitted records, orders ties by
canonical chunk ID and resolves results against the pinned local corpus. It adds
no vector database dependency. Context expansion, relationship traversal,
drafting, review, quotation validation and the citation viewer follow retrieval.

The cache is `artifacts/aws-eval/vector-cache.sqlite`; its checksum/count/model,
1,024 dimensions and cosine metric are verified before loading. Source identities
and normalized source text are checked against canonical records. Quotations use
the original canonical text and offsets, preserving whitespace and PDF locators.
New direct embeddings do not establish managed Knowledge Base sync equivalence.

Query embeddings must come from the same Titan model and settings, not an Ollama
or Hugging Face model. `artifacts/query-embeddings.sqlite` caches exact query-text
hashes with model/settings, float32 checksums, and provenance. The 15 original
scenario queries are now cached. Repeated questions need no AWS call. New queries
and model-requested additional searches need a matching query embedding; without
an explicit grant they fail before any AWS call, with no keyword fallback.

```sh
# Start the configured local workspace (default generation: Bedrock Qwen3 32B).
make run

# Inspect cached semantic retrieval without generation.
.venv/bin/python -m oracle ask --case 1 --retriever local-vector --evidence-only

# Plan query embedding for the original 15 scenarios; no paid execution.
.venv/bin/python -m oracle cache-query-embeddings
```

The UI's **Model settings** selects Bedrock or a local response model and explicitly
authorizes account-bound, expiring AWS calls under a session allowance and the
persistent $10 per-computer cap. Local-response approval covers query embeddings
only; switching to Bedrock requires generation approval. No grant or query cache
is distributed in the data release. The original 15 queries are cached in the
maintainer's workspace, not automatically present on every new laptop.

Advanced CLI embedding commands accept `--query-embedding-approval PATH`. UI
consent and CLI trial grants are separate. Titan is fixed to V2, 1,024 dimensions,
normalized output, and at most 1,600 input characters. Requests have no automatic
retries; conservative cost is reserved before client creation and settled from
input-token usage. Unknown usage retains its reservation, invalid vectors never
enter the cache, and concurrent misses are serialized. Credentials and grants
remain outside Git. See [spending controls](../README-technical.md#model-settings-and-spending).

The same local retrieval/map serves all UI response models. The debug drawer
records cache use, canonical candidates, cosine scores, timing, and index checksum.
There is no offline/keyword chatbot option or unapproved model fallback.

On the original snapshot, the measured local self-search took 0.285 seconds after a 1.746-second index load.
This is a document-vector integrity smoke check, not an answer-quality benchmark.
The real 15-query comparison with saved AWS managed retrieval returned identical
top-K chunk sets for all questions, with order differences in 3 cases. Scenario
15's historical top-K was four; changing the cutoff to three changes its set.
This evidence supports using local retrieval for the demo, not claiming a fully
identical managed implementation. AWS uses approximate nearest-neighbor search;
local search is exhaustive. The generation harness still requires content review.

Sources: [Titan V2 request/response](https://docs.aws.amazon.com/bedrock/latest/userguide/model-parameters-titan-embed-text.html),
[Titan limits](https://docs.aws.amazon.com/bedrock/latest/userguide/titan-embedding-models.html),
[S3 Vectors QueryVectors](https://docs.aws.amazon.com/AmazonS3/latest/API/API_S3VectorBuckets_QueryVectors.html).
