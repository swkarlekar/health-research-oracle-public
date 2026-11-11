# Corpus recovery and incremental vectors — November 11, 2026

The updated shared demo uses **81 evidence documents, 7,044 chunks and 7,044 Titan
V2 vectors**. Its 120 source records also include 38 collection indexes and one
unavailable source. Indexes help identify documents but are excluded from evidence
retrieval. The relationship map contains 11,807 reference/relationship signals
and 198 review entries; these are not counts of verified amendments.

## Which supplied links are represented?

| Supplied inventory | Distinct links | Acquired representation | Evidence-bearing links | Other outcomes |
| --- | ---: | ---: | ---: | --- |
| Original dataset/resource PDF | 108 | 107 | 72 | 35 indexes; one unavailable private workbook |
| Client authority XLSX, both worksheets | 96 | 96 internally; 95 in the shared package | 71 internally; 70 in the shared package | 25 indexes; one confidential training document withheld from distribution |

These are link counts, not additive counts of unique documents. The PDF contains
130 hyperlink occurrences. The workbook contains 231 occurrences: its 96 distinct
URLs comprise 80 exact overlaps with the PDF, eight newly acquired documents and
eight verified aliases of acquired documents. Aliases resolve to existing canonical
records and do not create duplicate evidence. All supplied links were audited and
accounted for; acquisition of a collection page does not mean that all its linked
documents were acquired.

Of the original 24 acquisition gaps, 23 now have a representation: two moved
official indexes, 15 Cornell LII secondary copies and six archived
original-publisher copies. The remaining private plan-directory/rating workbook
is a different document from the newly supplied authority XLSX.

The full internal derivative has 121 records, 82 evidence documents and 7,062
chunks/vectors. One training document expressly marked confidential contributes
18 chunks. It and all its derivatives are excluded from the shared corpus, graph,
vector cache and Release. The supplied XLSX, acquisition logs and private sharing
URLs are not distributed.

## What changed in search?

The original 4,572 canonical chunk records and their float32 vector values were
preserved exactly. Only the 2,490 new internal chunks were embedded, once, with
`amazon.titan-embed-text-v2:0`, 1,024 dimensions and normalized output. Filtering
out the confidential document gives 2,472 new shared vectors, without a second
embedding run. The shared cache therefore combines the original managed export
with direct Titan embeddings using the same model/settings. Its manifest records
both origins. It supports local cosine search; it is not an export of updated
managed Knowledge Base synchronization state.

Canonical text, quote offsets, source registry, graph and vector metadata share
one snapshot:
`915db3bb0c7cf98c8ce23ff2f873e9b8a415fe193243f6c4bcb0d43f7f6900c4`.
The same local retrieval and map feed whichever response model the user selects.
New query text still needs a Titan query embedding unless already cached.

## What is newly available, and what remains qualified?

Recovered material includes MLTC Policy 25.04 and its distinction between Service
Legacy and Plan Legacy, MLTC Policy 26.01, and a secondary representation of
18 NYCRR §505.14. Policy 26.01 references 25.04; its explicit supersession statement
names Policy 24.02, which remains an unresolved target. The assessment-location
provision does not by itself settle whether an independent-assessor requirement
also governs every plan reassessment. These additions address evidence gaps;
they do not establish that the earlier four model answers are now correct.

Cornell copies retain secondary-source/edition qualifications. Archived notices
retain their historical versions. The 1,006-page Medicaid Reference Guide is
historical 2012 material, not evidence of current income thresholds. Dates inside
the waiver bundle take precedence over its filename. Neither a later date nor a
reference automatically establishes supersession. Unresolved map signals remain
unresolved; model review is not human legal verification.

Independent checks covered canonical text and offsets, all saved raw-file hashes,
SQLite integrity, graph endpoints and review hash chains, the preserved old chunk
records, 12 known-gap quotations, and nine source-span relationship decisions.
Vector checks additionally verify counts, dimensions, checksums, canonical source
identity and preservation of every old embedding value. Retrieval smoke checks
are separate from answer-quality evaluation. The [model comparison](reasoning-comparison.md)
used the older corpus and is not a benchmark of this updated snapshot.

See [the data Release contract](data-bundle.md) for installation and distribution.

## Embedding cost and retrieval checks

The authorized incremental run made 2,490 calls, with no retries or failures, and
reported 951,714 input tokens. At the saved Titan rate its calculated cost was
**$0.01903428**; the conservatively rounded ledger charge was **$0.020267**.
It took **688.923 seconds** (about 11.5 minutes). All original document vectors
were reused, and the public subset required no second embedding run.

Four original question embeddings were reused for a network-blocked retrieval
check. Each search completed in about 0.6 seconds after loading the index.
The Service Legacy question now retrieves Policy 25.04 first. Other broad
questions still show ranking gaps: the SNF reassessment question puts §505.14
below the initial candidate window, and DME results can be crowded by waiver
pages. These checks demonstrate working canonical/vector retrieval, not improved
answer accuracy. No new live answers were generated on this snapshot; query
planning, source diversity and relevance remain evaluation priorities.
