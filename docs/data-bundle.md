# Shared demo data

The application code lives in Git. The corpus, original sources, Titan vectors,
and relationship map are distributed as a separate **GitHub Release asset**. The normal
setup command downloads and installs the pinned asset; manual unzipping is not
required.

The current release is `data-2026-11-11-paragraphs`, with asset
`oracle-data-public-915db3bb0c7c-paragraphs.zip`. It is the `data-2026-11-11` snapshot,
byte for byte, plus the paragraph-vector index used by the research pipeline; an
existing `data-2026-11-11` installation can install it in place. Its companion manifest
and SHA-256 file describe every included file. `config/data-release.json` pins the
archive hash for setup.

The archive contains only:

- Canonical SQLite corpus, document/section/chunk JSONL and processing summaries.
- The 119 acquired originals referenced by this snapshot, including PDFs and HTML.
- The Titan document-vector database and the optional document map (a local SQLite file).
- The paragraph-vector index (35,151 Titan V2 vectors, 512-dimensional float16), an
  optional file that the installer checks against the same canonical snapshot.
- The source registry and temporal policy that bind those data together.

It excludes AWS credentials/profiles, spending grants, budgets, query-embedding
caches, chat history, evaluation answers, application code, model weights, logs,
and obsolete/unreferenced files. Each laptop creates its own AWS authorization,
budget, query cache and history. One confidential training document and all its
derivatives are excluded. The supplied XLSX and acquisition audit files are not
part of the Release. An unavailable source's access-bearing sharing URL remains
replaced with an explicit `example.invalid/redacted-source/` placeholder.

This public snapshot has 120 source records: 81 evidence-producing documents,
38 indexes and one unavailable source. It contains 7,044 chunks/vectors and
11,807 relationship signals, with 198 review entries. Many relationships remain
unresolved; a checksum does not certify legal currency or correctness. Historical
reviews retain their actors, decisions and supporting spans with explicit
snapshot-rebinding provenance; rebinding does not represent a new human review.

The vector cache combines 4,572 preserved managed-export embeddings with 2,472
new direct Titan V2 embeddings. All use the same normalized 1,024-dimensional
model/settings. The manifest retains the mixed origin counts and baseline hashes
while removing AWS account, bucket, index, knowledge-base and data-source bindings.
It is a local retrieval copy, not managed Knowledge Base synchronization state.
New questions still require Titan query embeddings unless already cached.

The earlier `data-2026-11-08` Release remains available for reproducing historical
model comparisons. Those results were not rerun on the new data. The current
package does not change the default generation/review prompts.

For manual inspection or an already downloaded asset:

```sh
python -m oracle.data_bundle verify path/to/oracle-data-public-915db3bb0c7c.zip --sha256 PINNED_HASH
python -m oracle.data_bundle install path/to/oracle-data-public-915db3bb0c7c.zip --root . --sha256 PINNED_HASH
```

Use the project's virtual-environment Python if `python` is not that interpreter.
The installer verifies the archive hash, exact file inventory, per-file hashes,
canonical corpus/graph/vector bindings, source bytes, and relocated PDF resolution
before touching destination data. Existing identical files are preserved. Changed
data are never overwritten; install a different snapshot in a fresh checkout.
Unrelated files such as history and AWS credentials are untouched. Installation is
idempotent, and the final `oracle-data-manifest.json` records the installed snapshot.

Maintainers can build another data-only snapshot without network or inference:

```sh
python -m oracle.data_bundle export --root . --output artifacts/distribution/NEW_VERSION.zip
python -m oracle.data_bundle verify artifacts/distribution/NEW_VERSION.zip
```

Export uses an explicit allowlist plus original-source paths referenced by the
pinned graph. It refuses symbolic-link escapes and databases with pending writes.
The archive is stored without compression to limit CPU and memory use; all hashing
and file copying stream in 1 MiB blocks. Export validates the original first, builds
sanitized databases in temporary staging, verifies their new bindings, and checks
that every original file remains unchanged. Fresh vector pages and vacuumed
corpus/graph databases prevent removed metadata from surviving in unused SQLite
pages. Export never rewrites the working corpus or historical experiment data.
Before publication, the new snapshot was exported, verified and installed under
a separate temporary root with socket connections blocked. Verification checked
all 7,044 canonical vector identities, all 119 raw-file hashes and relocated PDF
viewing. The 4,572 old vector values are also compared byte-for-byte against the
preserved original cache. These checks do not require an LLM, GPU or AWS call.
