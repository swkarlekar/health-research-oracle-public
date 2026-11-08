# Developer demo runbook

For the collaborator workflow, use the [main README](../README.md): AWS SSO, clone, `make setup`, `make run`, then explicit spending consent in Settings. [The technical README](../README-technical.md) documents model choices, IAM, storage, costs, and local-memory controls. This page covers diagnostic commands rather than another product mode.

## Data and relationship map

`make setup` restores the pinned data Release automatically. See [the bundle contract](data-bundle.md). To inspect the installed map:

```sh
.venv/bin/python -m oracle graph-status
```

A graph rebuild requires a new output path, preserving existing append-only review decisions. The four relationship stages are:

1. **Extract:** scan canonical sections for explicit URLs, legal/policy references, identities, and relationship language. Preserve ambiguous and missing targets. Exact references do not establish legal currency.
2. **Interpret:** bounded model or external review proposes typed relationships with exact supporting spans, direction, scope, dates, and limits.
3. **Validate:** check corpus/candidate identity, canonical spans, endpoints, date evidence, and metadata-proxy restrictions.
4. **Review:** a separate model evaluates the interpretation; append model-reviewed, unresolved, or rejected records. Only explicit human review can be labeled human verification.

```sh
.venv/bin/python -m oracle relationship-packet RELATIONSHIP_ID \
  --output artifacts/relationship-packet.json
```

Opt-in relationship inference and importing prepared interpretations remain advanced CLI operations. They are not run by setup or normal chat. See `python -m oracle review-relationships --help` and `import-relationship-review --help`; do not start inference without matching authorization and memory checks.

## Evidence and output inspection

The [20 synthetic questions](../examples/scenarios.json) contain 15 substantive examples and five negative boundary cases. They are not legal expert gold answers. Adding examples did not enlarge the original 15-question AWS trial authorization.

```sh
# Actual local Titan search/context, no generation. Needs this query cached.
.venv/bin/python -m oracle ask --case 1 --retriever local-vector --evidence-only \
  --view debug --output artifacts/evidence.debug.json

# AWS adapter orchestration with API-shaped mocks, no network or inference.
.venv/bin/python -m oracle api-check --case 1 \
  --view debug --output artifacts/api-check.debug.json
```

An uncached Titan query stops unless it has an exact embedding grant. Historical lexical/evidence-only tools remain useful developer checks; there is no keyword chatbot in the UI. `api-check` does not measure semantic retrieval or answer quality.

The CLI `demo` command runs actual installed local generation/review. Its default is `gemma3:4b`; it does not download models. The same model-size and macOS memory checks used by Settings apply, with strict context/output bounds and no cloud fallback. It does not use the UI's selected model or grant. Do not repeat the stopped large-Qwen comparative evaluation automatically.

`--view demo` presents findings, exact quotes, source hyperlinks, material limits, and clarifications. `--debug-output PATH` retains diagnostics for the same run; `.md` gives a readable report, `.json` structured inspection. Debug reports include retrieval/expansion, graph connections considered and loaded, canonical evidence, model instructions, brief public review summaries, usage/timing, and validation failures. Private reasoning and credentials are not collected. Keep debug files under ignored `artifacts/`.

## Context, dates, and validation

Short sources can load whole; long sources expand relevant sections/pages and referenced provisions within explicit limits. Graph traversal is one hop. Omitted or unavailable context is reported. Lossless passage labels let models select quotes while Python resolves exact Unicode spans and source/PDF locators. Graph/corpus identities bind source text and metadata; stale or altered packets fail verification.

Program/plan scope can come from prose or explicit CLI flags. The UI infers plan type rather than asking for a duplicate field. **Rules as of** specifies the desired effective research date, including historical questions. An unspecified date is `unknown`, not today's date or a currency guarantee. Embedded document modification metadata is an allowed secondary proxy only; it never proves supersession.

Two query-only request checks precede real CLI/UI inference; API simulation and evidence-only checks remain model-free or use their explicitly mocked source calls. Public research then has a draft and separate LLM source review checking applicability, support, exceptions, conflicts, and dates, with at most one additional corpus search. Routing calls are included in the four/five-call total. Canonical citations, quote spans, schema, scope, and boundary checks run afterward. These mechanics do not establish legal correctness. See [actual prompt examples](prompts.md) and [observed failures](prompt-search.md).

## Optional managed AWS path

The earlier authorized trial used S3 corpus objects, Titan V2, Bedrock Knowledge Bases with S3 Vectors, and Bedrock Qwen generation, followed by the same local expansion/map/validation. Its temporary resources were cleaned up after export. The normal demo searches those exported document vectors locally and does not recreate cloud storage.

The managed adapter and approval-gated upload/ingestion commands remain available for future explicitly authorized experiments. Dry-run plans, model selection, AWS credentials, and UI consent do not authorize deployment or ingestion. See [the trial runbook](aws-trial.md), [AWS plan](aws-plan.md), and command `--help` before using them. Changing the response model preserves document vectors; changing the embedding model requires a compatible new index plan.

## Tests

```sh
make test
```

Stubber/monkeypatched SDK tests use dummy credentials and blocked sockets. They verify request shapes, schema/citation handling, and failure paths without paid calls. They do not measure live model access or answer quality. GitHub Actions is manual to avoid automatically using private-repository minutes.
