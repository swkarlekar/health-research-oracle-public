# Public distribution audit

The public repository starts from a clean source snapshot, rather than a fork or push of the original private Git objects. The original private repository and its history remain private.

Before publication, Gitleaks 8.30.1 scanned all eight original reachable commits without credential findings. An independent scan covered 195 unique blobs, commit metadata, and the data archive. It found an unavailable source's potentially access-bearing sharing URL, old AWS resource identifiers in vector provenance, and an unnecessary personal filesystem path. These were withheld from the public copy. A history rewrite alone was not used to claim GitHub's cached objects were purged.

The final public data candidate was independently checked across all 95 archive members (94 manifest files plus the manifest). No original sharing URL, tenant/opaque-route identifiers, AWS account/resource identifiers, credential-pattern hits, or runtime credential/history/budget tables remained. All three SQLite databases passed integrity checks with no free pages containing stale values.

The sanitized copy preserves all 84 original source files, 108 canonical document texts, 4,572 embedding blobs, section/relationship records, and 90 historical review decisions. Metadata redaction creates a derived snapshot; review records explicitly retain historical provenance and state that no new review was performed. No re-embedding or new legal review was implied. The asset's exact hash is pinned in `config/data-release.json`.

Screenshots were checked for credentials and client identifiers; optional screenshot comment metadata was removed without changing image-data bytes; their questions are synthetic examples. Public commit authorship uses the GitHub noreply identity. Chat histories, grants, budgets, query caches, original private backups, and experiment receipts remain ignored local artifacts.

This is a scoped audit with automated patterns and independent inspection, not a guarantee that no conceivable sensitive value exists. New corpus additions or releases require another check. Local audit evidence remains under ignored `artifacts/publication-audit/`; it is not shipped as data.
