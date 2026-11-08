# Technical setup and architecture

Start with the [main README](README.md) for the demo workflow. The supported demo runs from a source checkout on each collaborator's laptop; it does not require deploying an AWS Knowledge Base or running a hosted application.

## First-time setup

Install Python 3.12 or newer, Git, [AWS CLI v2](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html), Make, and Google Chrome. On Windows without Make, use `python scripts/setup_demo.py` and `python scripts/run_demo.py` in place of the Make commands. The scripts choose the appropriate virtual-environment Python path.

Configure the AWS profile once, using your own organization's IAM Identity Center start/issuer URL, SSO region, account, and permission set:

```sh
aws configure sso --profile oracle
aws sso login --profile oracle
```

`oracle` is just a local profile nickname. The application region is `us-east-1`; the SSO region may differ. Boto3 uses the named profile and its local SSO cache. AWS CLI can refresh role credentials while the SSO session permits; after the SSO session expires, run the login command again. Do not copy credentials, cache files, or approval grants between collaborators.

Your permission set needs `bedrock:ListFoundationModels` for the account/catalog check and `bedrock:InvokeModel` for Titan Text Embeddings V2 and whichever generation model you select. The demo invokes generation through Bedrock Converse. Restrict resource permissions to the selected regional foundation models where the action supports it. The current catalog is:

| Model | Model ID |
| --- | --- |
| Qwen3 32B, default | `qwen.qwen3-32b-v1:0` |
| Amazon Nova Lite | `amazon.nova-lite-v1:0` |
| Amazon Nova Pro | `amazon.nova-pro-v1:0` |
| Query embedding | `amazon.titan-embed-text-v2:0` |

Catalog presence does not prove your account has model access. A restricted account without ListFoundationModels currently fails the metadata check. No S3, Lambda, vector-store, or Knowledge Base permissions are needed for the default local-retrieval demo. See the [Bedrock Converse API](https://docs.aws.amazon.com/bedrock/latest/APIReference/API_runtime_Converse.html) and [SSO configuration](https://docs.aws.amazon.com/cli/latest/userguide/cli-configure-sso.html).

After setup, clone the repository and run `make setup`, then `make run`. Setup creates `.venv`, installs `requirements-demo.txt`, downloads the pinned public Release asset with an unauthenticated HTTPS download, verifies its SHA-256 and manifest, and installs the data without overwriting differing existing files. There are no model downloads or AWS calls during setup. The launcher waits for `/api/config` before opening Chrome, checks that a running server belongs to this checkout and code/data version, and refuses a stale server. Stop that server or choose another port:

```sh
python3 scripts/run_demo.py --port 8766
```

## What is local and what calls AWS

| Component | Location and behavior |
| --- | --- |
| Original PDFs and saved source files | Local `text_corpus/raw/`; original hashes verified before viewing |
| Canonical text, sections, chunks, and provenance | Local `text_corpus/processed/` and its corpus SQLite index |
| Document embeddings | Local `artifacts/aws-eval/vector-cache.sqlite`; exported Titan V2, 1,024-dimensional vectors |
| Query embeddings | Titan V2 on a cache miss; local `artifacts/query-embeddings.sqlite` caches exact query/model/settings matches |
| Relationship map and review ledger | Local `artifacts/relationships.sqlite`; source-linked relations and reviewed interpretations |
| Answer and skeptical review | Selected Bedrock model or selected loopback local model server |
| Chat history, debug, and settings | Local `artifacts/ui/`; never included in the data release |
| Grants, counters, and spend ledger | Local ignored artifacts; never committed or distributed |

The data release contains source documents, processed corpus, vectors, and graph. It excludes query caches, chat history, credentials, grants, model weights, and experiment outputs. See the [bundle contract and restore process](docs/data-bundle.md). The archive is about 208 MB; the relationship database is about 50.5 MB and document-vector cache about 34.7 MB. The corpus SQLite database is about 36.9 MB. These are distinct files, not separate copies of a hosted knowledge graph.

Local vector search performs exhaustive cosine comparison over the same exported vectors and resolves every hit to canonical local text. The 15 saved AWS searches returned the same top-K chunk sets locally in all 15 cases, with ranking differences in three. This is measured parity for those questions, not a guarantee of identical future results. The same local graph and expansion harness follow either retrieval adapter. No re-embedding of documents is required for a new laptop or a generation-model change. Replacing Titan with another embedding model would require compatible document and query vectors and a new index plan.

## Model settings and spending

The settings page starts with Bedrock Qwen3 32B selected and paid calls disabled. **Check account and available models** uses STS and Bedrock catalog metadata, without paid inference. Explicit account-specific consent creates a four-hour grant and a shared query/generation session allowance. Query-only authorization for a local response model does not authorize later Bedrock generation.

The persisted ledger enforces a strict **$10 cumulative cap per installation**, alongside the chosen smaller session allowance. Calls reserve a conservative upper cost before client creation; successful calls settle from reported token usage, while uncertain usage retains its reservation. Grant attempts are persisted and SDK retries are disabled. Repeated identical queries reuse cached embeddings. A normal research run has two query-only routing calls, one draft, and one source review (four calls). One additional corpus search and final review can bring the total to five. A boundary-only result uses two calls and no source search. All calls use the selected model and the same metering/grant controls. Source or model failures do not trigger an unapproved cloud fallback. **Disable paid AWS calls** expires the current grants; an already dispatched request cannot be undone.

These are application controls, not an AWS billing service or an account-wide cap. They cover this application's metered requests using saved regional prices, not unrelated tools, taxes, changed AWS prices, or another laptop. The original trial's ledger is preserved locally; it is not shipped to collaborators. Set an AWS budget independently if desired. Generation rates and the curated model menu are in `config/bedrock-models.json`; Titan embedding rates come from `config/aws-eval-prices.json`.

For local generation, install and run Ollama, LM Studio, or another compatible server yourself. Settings discovers model metadata without loading or downloading weights. Only loopback URLs are accepted. Known models exceeding 35% of physical RAM are blocked (8 GiB fallback when RAM cannot be determined); unknown size requires explicit acknowledgment. macOS memory-pressure checks, conservative context limits, a 2,048-token output cap, and Ollama `keep_alive=0` reduce risk but do not guarantee peak memory use. The earlier large local Qwen evaluation exhausted this laptop's memory and was stopped; it must not resume automatically.

## Harness and document map

The [original diagram](mermaid-diagram.png) maps to:

1. Input and scope checks in `oracle/policy.py`; missing research attributes yield clarification. The UI infers plan type from the question. **Rules as of** sets the research date for historical applicability, not the ingestion date or proof of currency. Blank means unspecified; follow-ups retain an explicitly selected date until cleared.
2. Two query-only structured request checks in `oracle/request_routing.py`, before source retrieval. Prohibited-only requests receive fixed capability limits or enum-derived clarification questions. Mixed requests continue to public-rule research with fixed limits attached only after the original answer gates pass. These checks add no legal authority.
3. Approved-source retrieval in `oracle/local_vectors.py` or the optional managed adapter in `oracle/aws.py`.
4. Bounded parent/section context expansion and one-hop relationship traversal in `oracle/context.py`. Short sources can be included whole; long sources load relevant context with explicit omissions.
5. Drafting, skeptical LLM review, and at most one additional search in `oracle/harness.py`. The reviewer checks support, applicability, responsiveness, exceptions, dates, and conflicts. There is no corpus-wide currency certification.
6. Passage selection and exact canonical quotation resolution in `oracle/quotations.py`, followed by schema, citation, scope, source-integrity, and boundary checks. Invalid output is withheld.
7. Concise responses and separate debug traces in `oracle/output.py`; PDF viewing and text highlights in `oracle/viewer.py` and `oracle/web.py`.

The map's four stages are extract, interpret, validate, and review. Programmatic extraction records explicit references, identities, relationship language, ambiguous matches, and missing targets. Bounded model interpretation can propose typed relationships with canonical supporting spans. Python validates snapshot identity, endpoints, quotes, and date evidence. A separate review records model-reviewed, unresolved, or rejected interpretations in an append-only ledger. Model review is never human verification. Metadata dates do not prove supersession; a reference to another document is not proof that it amends that document.

Live requests use [these prompt layers](docs/prompts.md). The [prompt searches](docs/prompt-search.md) support the narrow two-call boundary guard as an incremental improvement. The source-screening and typed legal-answer pipelines remain experimental; production drafting/review prompts are unchanged. Neither current nor experimental pipelines have met the brief's 95% correctness/completeness target. The guard was tested on Bedrock Qwen; local-model behavior is not equivalently validated.

## Developer commands

The lightweight demo does not need the full extraction/development dependencies. To work on tests or regenerate corpus artifacts:

```sh
.venv/bin/python -m pip install -r requirements-dev.txt
make test

# API-shaped mocked responses; no credentials, sockets, or actual inference.
make api-check

# Inspect real cached vector retrieval without generation.
.venv/bin/python -m oracle ask --case 1 --retriever local-vector --evidence-only \
  --view debug --output artifacts/evidence.debug.json

.venv/bin/python -m oracle graph-status
```

The evidence-only command needs a previously cached query embedding; distributed bundles intentionally omit personal query caches. An uncached query stops without a matching explicit grant. Historical keyword/mock tools remain developer checks, not an alternative chatbot mode. CLI trial workflows are documented in [the developer runbook](docs/demo.md) and [AWS trial record](docs/aws-trial.md); those commands do not inherit UI consent.

Rebuilding a graph requires a new output path so existing review decisions are preserved. Corpus changes require new canonical identities, embeddings for changed documents, graph reconciliation, and a new pinned data Release. A model change does not rebuild the corpus. Commit code/config/docs in Git; publish large approved data snapshots as versioned Release assets. Do not commit `artifacts/`, source PDFs, credentials, or local settings.

The CSSNY logo is the unchanged [official website SVG](https://smhttp-ssl-58547.nexcesscdn.net/nycss/images/CSS_Logo-Primary_RGB.svg), saved locally as `oracle/web_assets/cssny-logo.svg`; the header links to [CSSNY](https://www.cssny.org/). No external image request is needed to load the page.
