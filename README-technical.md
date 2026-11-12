# TRACE technical setup and architecture

Start with the [main README](README.md) for the demo workflow. The supported demo runs from a source checkout on each collaborator's laptop; it does not require deploying an AWS Knowledge Base or running a hosted application.

## First-time setup

Install Python 3.12 or newer, Git, [AWS CLI v2](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html), Make, and Google Chrome. On Windows without Make, use `python scripts/setup_demo.py` and `python scripts/run_demo.py` in place of the Make commands. The scripts choose the appropriate virtual-environment Python path.

Configure the AWS profile once, using your own organization's IAM Identity Center start/issuer URL, SSO region, account, and permission set:

```sh
aws configure sso --profile oracle
aws sso login --profile oracle
```

`oracle` is just a local profile nickname. The application region is `us-east-1`; the SSO region may differ. Boto3 uses the named profile and its local SSO cache. AWS CLI can refresh role credentials while the SSO session permits; after the SSO session expires, run the login command again. Do not copy credentials, cache files, or approval grants between collaborators.

Your permission set needs `bedrock:ListFoundationModels` for the account/catalog check and `bedrock:InvokeModel` for Titan Text Embeddings V2 and the generation models you use (the selected model and, if enabled, the answer-writing model). The demo invokes generation through Bedrock Converse. Restrict resource permissions to the selected regional foundation models where the action supports it. The current catalog is:

| Model | Model ID |
| --- | --- |
| Qwen3 32B, default | `qwen.qwen3-32b-v1:0` |
| Amazon Nova Lite | `amazon.nova-lite-v1:0` |
| Amazon Nova Pro | `amazon.nova-pro-v1:0` |
| Mistral Large 3, optional answer writer | `mistral.mistral-large-3-675b-instruct` |
| Query embedding | `amazon.titan-embed-text-v2:0` |

Catalog presence does not prove your account has model access. A restricted account without ListFoundationModels currently fails the metadata check. No S3, Lambda, vector-store, or Knowledge Base permissions are needed for the default local-retrieval demo. See the [Bedrock Converse API](https://docs.aws.amazon.com/bedrock/latest/APIReference/API_runtime_Converse.html) and [SSO configuration](https://docs.aws.amazon.com/cli/latest/userguide/cli-configure-sso.html).

After setup, clone the repository and run `make setup`, then `make run`. Setup creates `.venv`, installs `requirements-demo.txt`, downloads the pinned public Release asset with an unauthenticated HTTPS download, verifies its SHA-256 and manifest, and installs the data without overwriting differing existing files. There are no model downloads or AWS calls during setup. The launcher waits for `/api/config` before opening Chrome, checks that a running server belongs to this checkout and code/data version, and refuses a stale server. Stop that server or choose another port:

```sh
python3 scripts/run_demo.py --port 8766
```

## What is local and what calls AWS

| Component | Location and behavior |
| --- | --- |
| Original PDFs and saved source files | Local `text_corpus/raw/`; hashes verified before viewing |
| Canonical text, sections, and chunks | Local `text_corpus/processed/` and its SQLite index |
| Chunk vectors | Local `artifacts/aws-eval/vector-cache.sqlite`; Titan V2, 1,024 dimensions |
| Paragraph vectors | Local `artifacts/aws-eval/paragraph-vectors.sqlite`; Titan V2, 512 dimensions, float16 |
| Keyword indexes | Chunk index in the corpus database; a stemmed paragraph index built in memory at start |
| Query embeddings | Titan V2 on AWS for each new search text; cached in `artifacts/query-embeddings.sqlite` |
| Optional document map | Local `artifacts/relationships.sqlite`; not used for Bedrock answers by default |
| Planning, reading, answering, checking | The selected Bedrock model(s), or a local model server |
| Chat history, debug reports, settings | Local `artifacts/ui/`; never part of the data release |
| Grants and spending ledger | Local, ignored by Git; never distributed |

The data release contains the sources, processed corpus, both vector indexes, and the document map. It excludes query caches, chat history, credentials, grants, model weights, and experiment outputs. See the [bundle contract](docs/data-bundle.md). Searching is exhaustive cosine comparison on the laptop; numpy makes it fast and is installed by setup. If numpy or the paragraph-vector file is missing, search still works with chunk vectors and keywords, with weaker recall.

## Model settings and spending

Settings starts with Bedrock Qwen3 32B selected and paid calls disabled. **Check account and available models** reads identity and catalog metadata without paid inference. Explicit, account-specific consent then creates a four-hour grant for query embeddings, one for the selected generation model, and, if its box is ticked and the account has access, one for the answer-writing model. All share one session allowance. Consent for a local answer model covers query embeddings only.

A Bedrock answer makes about 50 small calls (two request checks, one plan, about 40 parallel passage-screening calls, then triage, answer, a support check and a second reader) and up to about 25 query embeddings, for roughly $0.07 and one minute. The default $0.25 allowance therefore covers three or four questions; raise it in Settings as needed. Each grant allows up to 5,000 calls; the dollar allowance is what limits spending.

A persistent ledger enforces a cumulative cap per installation ($10 unless explicitly raised). Each call reserves a conservative upper cost before it is sent and settles to the reported token usage afterwards. SDK retries are disabled. **Disable paid AWS calls** expires the current grants. These are application controls using saved regional prices, not an AWS billing limit; set an AWS budget separately if you want one. Generation prices and the model menu are in `config/bedrock-models.json`; the Titan price is in `config/aws-eval-prices.json`.

For local generation, install and run Ollama, LM Studio, or another compatible server yourself. Settings discovers installed models without loading them, accepts only loopback URLs, and blocks models too large for the machine's memory. Local models use the simpler single-search loop described below, because they cannot make fifty calls quickly.

## Harness

[How the harness works](docs/harness.md) is the reference. In brief:

- `oracle/policy.py` checks the question and scope before anything else; `oracle/request_routing.py` makes two query-only calls that separate public-rule research from requests the tool must decline.
- `oracle/pipeline.py` runs plan → search → read → triage → answer → check and second reader. Its instructions are in `oracle/pipeline_prompts.py` and its settings in `config/research-pipeline.json`.
- `oracle/paragraphs.py` provides the paragraph keyword and vector indexes; `oracle/local_vectors.py` the chunk vectors and query-embedding cache.
- `oracle/harness.py` validates the result: exact quotations, citations, unchanged scope, and the research-only boundary. Invalid output is withheld.
- `oracle/output.py`, `oracle/viewer.py`, and `oracle/web.py` produce the answer view, the debug report, and the document viewer.
- `oracle/library.py` backs the sidebar's Documents tab: the approved-source list, cleaned titles and categories, and keyword search over the local FTS index. `GET /api/documents` lists sources (or searches with `?q=`), and `/api/documents/<id>` opens one, with `?find=` marking matches, `/page/<n>.png` for PDF pages, and `/original` for the saved file. These routes read verified local data only: they never load the vector indexes and make no model or AWS call. The document list is built once per server start and cached in memory.

Local models, keyword-only mode, and the CLI use the original loop in `oracle/harness.py`: one search, bounded context expansion (`oracle/context.py`), a draft, and a skeptical review with at most one additional search. That loop can use the optional document map (`oracle/relationships.py`), which records references between documents and reviewed interpretations of them. Following the map's references is available in the research pipeline as a setting (`limits.reference_cap`) and is off by default.

## Developer commands

```sh
.venv/bin/python -m pip install -r requirements-dev.txt
make test

# API-shaped mocked responses; no credentials, sockets, or inference.
make api-check

# Inspect cached vector retrieval without generation (original loop).
.venv/bin/python -m oracle ask --case 1 --retriever local-vector --evidence-only \
  --view debug --output artifacts/evidence.debug.json

# Rebuild the paragraph-vector index after a corpus change (about $0.10 of embeddings).
.venv/bin/python scripts/build_paragraph_index.py --profile oracle --approve-spend 0.25
```

CLI commands do not inherit consent given in the app. A corpus change needs new canonical identities, embeddings for the changed chunks, a rebuilt paragraph index, and a new pinned data release; a model change needs none of that. Commit code, config, and docs in Git; publish data snapshots as versioned release assets. Do not commit `artifacts/`, source PDFs, credentials, or local settings.

The TRACE logo is `oracle/web_assets/trace-logo.svg`. The CSSNY logo is the unchanged [official website SVG](https://smhttp-ssl-58547.nexcesscdn.net/nycss/images/CSS_Logo-Primary_RGB.svg), saved as `oracle/web_assets/cssny-logo.svg`; the header links to [CSSNY](https://www.cssny.org/).
