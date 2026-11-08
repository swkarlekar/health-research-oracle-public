# Shared demo data

The application code lives in Git. The corpus, original sources, Titan vectors,
and relationship map are distributed as a separate **GitHub Release asset**. The normal
setup command downloads and installs the pinned asset; manual unzipping is not
required.

The first snapshot is `data-2026-11-08`, with asset
`oracle-data-public-f63013298e2f-v2.zip`. Its companion manifest and SHA-256 file describe
every included file. `config/data-release.json` pins the archive hash for setup.

The archive contains only:

- Canonical SQLite corpus, document/section/chunk JSONL, and processing summaries.
- The 84 original files referenced by this snapshot, including PDFs and HTML.
- The local SQLite relationship map and the exported Titan vector database.
- The source registry and temporal policy that bind those data together.

It excludes AWS credentials/profiles, spending grants, budgets, query-embedding
caches, chat history, evaluation answers, application code, model weights, logs,
and obsolete/unreferenced files. Each laptop creates its own AWS authorization,
budget, query cache, and history. The distribution vector database includes only
canonical source fields; managed AWS account, bucket, index, knowledge-base, and
data-source identifiers are removed. Its float32 embeddings are unchanged.

One unavailable source had a SharePoint sharing URL. The public copy replaces that
access-bearing URL with an explicit `example.invalid/redacted-source/` placeholder.
Its tenant hostname in aggregate acquisition counts is relabeled
`redacted.invalidprovider`.
That source had no acquired content, sections, chunks, or embeddings. This metadata
change produces a new corpus snapshot and graph/vector bindings. The manifest
records the derivation. All 90 historical relationship reviews retain their actors,
dates, decisions, evidence, and candidate hashes; their derived records explicitly
retain the original review ID, record hash, and snapshot and state that no new
review was performed. Available source bytes, text, quote offsets, relationships,
and all 4,572 embedding blobs remain unchanged. Maintainers retain the original
private backup separately; it is not an asset for public distribution.

This snapshot has 108 source records, 58 evidence-producing documents, 4,572
chunks/vectors, and 10,991 relationship signals. Many relationships are unresolved;
neither the dataset version nor a checksum certifies legal currency or correctness.
New questions still require Titan query embeddings unless already cached. Local
generation does not turn Titan into an offline embedding model.

For manual inspection or an already downloaded asset:

```sh
python -m oracle.data_bundle verify path/to/oracle-data-public-f63013298e2f-v2.zip --sha256 PINNED_HASH
python -m oracle.data_bundle install path/to/oracle-data-public-f63013298e2f-v2.zip --root . --sha256 PINNED_HASH
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
Before publication, the real snapshot was
exported, verified, and installed under a different temporary root with every
socket connection blocked. All 4,572 vector identities and 84 original-source
hashes passed; a 13-page PDF opened through the relocated citation viewer. No LLM,
GPU, or AWS call was involved.
