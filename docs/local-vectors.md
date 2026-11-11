# Local Titan vector retrieval

The read-only local index reuses the exported 4,572 Titan Text Embeddings V2
float32 vectors, custom metadata, and corpus snapshot. It computes exact cosine
distance over permitted records. The production retriever combines those
candidates with canonical SQLite full-text matches using reciprocal-rank fusion
and plan-title weighting. Focused query results are interleaved, with at most six
initial source sections. All selected passages resolve against the pinned local
corpus. It introduces no new vector database dependency. Bounded section/parent
expansion and relationship traversal precede contract-product filtering, passage
selection, independent review, quotation validation, and the citation viewer.

The cache is `artifacts/aws-eval/vector-cache.sqlite`; its checksum/count/model,
1,024 dimensions, and cosine metric are verified before loading. The managed
source text differs from canonical text only in outer whitespace for 2,851
records; 1,721 match exactly. All source identities and selected normalized text
are checked against canonical records. Quotations always use original canonical
text and offsets, preserving source whitespace and PDF locators.

Query embeddings must come from the same Titan model and settings, not an Ollama
or Hugging Face model. `artifacts/query-embeddings.sqlite` caches exact query-text
hashes with model/settings, float32 checksums, and provenance. The 15 original
scenario query strings are cached in the maintainer's workspace. The production
planner generates focused search strings, whose exact matches reuse cached
embeddings. A repeated question can generate a different search string. New search
strings and reviewer-requested searches need a matching Titan embedding; without
an explicit grant they fail before an embedding call. Local full-text matching
complements successful semantic search and does not replace it on error.

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
persistent per-computer cap, initially $10. Local-response approval covers query embeddings
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
records the search plan, cache use, canonical vector/full-text candidates, ranking,
timing, index checksum, and contract-scope exclusions.
There is no offline/keyword chatbot option or unapproved model fallback.

The measured local self-search took 0.285 seconds after a 1.746-second index load.
This is a document-vector integrity smoke check, not an answer-quality benchmark.
The earlier 15-query raw-vector comparison with saved AWS managed retrieval returned identical
top-K chunk sets for all questions, with order differences in 3 cases. Scenario
15's historical top-K was four; changing the cutoff to three changes its set.
That measurement applies to the earlier raw-vector strategy. Current query
planning and hybrid ranking change the selected passages and have no measured
AWS ranking equivalence. AWS uses approximate nearest-neighbor search; the local
vector component is exhaustive. The generation harness still requires content review.

Sources: [Titan V2 request/response](https://docs.aws.amazon.com/bedrock/latest/userguide/model-parameters-titan-embed-text.html),
[Titan limits](https://docs.aws.amazon.com/bedrock/latest/userguide/titan-embedding-models.html),
[S3 Vectors QueryVectors](https://docs.aws.amazon.com/AmazonS3/latest/API/API_S3VectorBuckets_QueryVectors.html).
