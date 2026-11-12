# TRACE

**Trusted Research for Advocates with Citations & Evidence**

<img src="oracle/web_assets/trace-logo.svg" width="80" alt="TRACE logo, a pencil tracing a heart">

**[▶ Watch the demo video](https://github.com/swkarlekar/health-research-oracle-public/releases/download/demo-video-2026-11-08/design_sprint_demo_video.mov)** (MOV download, 119 MiB; recorded on an earlier version)

A research assistant for public health insurance advocates, based on the Community Service Society of New York (CSSNY) design brief. Ask a question in plain language, read the answer with its supporting quotations, see what the sources leave uncertain, and open any cited document inside the chat with the passage highlighted. The advocate remains responsible for interpreting the rules and applying them to a client's situation.

![Welcome page with the Research and Documents sidebar tabs](docs/screenshots/ui-welcome.jpg)

**This prototype focuses on New York Medicaid**, especially managed long term care, managed care, and non-MAGI eligibility. The collection also holds some Medicare, integrated Medicare–Medicaid, and health-privacy sources; their presence does not mean those topics are fully covered.

## Start the demo

You need Python 3.12+, Git, AWS CLI v2, Make, Google Chrome, and an AWS SSO profile with Bedrock permissions. No GPU is required. See [first-time AWS and tool setup](README-technical.md#first-time-setup) if these are not configured.

```sh
aws sso login --profile oracle

git clone https://github.com/swkarlekar/health-research-oracle-public.git
cd health-research-oracle-public
make setup
make run
```

`make setup` installs a small Python runtime and downloads and verifies the 364 MB [data release](https://github.com/swkarlekar/health-research-oracle-public/releases/tag/data-2026-11-11-paragraphs): documents, PDFs, search indexes, and an optional document map. It does not download an LLM or create AWS resources. `make run` opens the local page in Chrome. Keep the terminal running; Ctrl+C stops the server.

Open **⚙ Model settings**, check your AWS account, and explicitly authorize a spending allowance before asking a question. Opening the app or saving a model choice does not authorize paid calls. Each collaborator uses their own AWS account; no keys are shared with the project.

![Model settings with Bedrock selection and explicit spending consent](docs/screenshots/model-settings.jpg)

## Using the workspace

The sidebar has two tabs. **Research** holds your conversations and the example questions; **Documents** is a lookup for the sources themselves.

- **Ask a research question.** The sidebar offers the four client reference cases, five boundary tests, and 15 synthetic examples. While a question runs, a step tracker shows where it is (check request, plan, read, choose passages, write, verify). Follow-up questions can build on your earlier questions; the **Follow-up** switch in the message box controls this, and earlier generated answers are never treated as evidence.
- **Read the answer.** The direct answer comes first in its own card, with one chip per source and a button for each cited page. Supporting findings follow, each naming its sources, then the exact quotations. The status above the answer says what is open, for example "Answered · 3 points to check", and links to the list.

![Answer card with source chips and page buttons](docs/screenshots/ui-answer.jpg)

- **Read the uncertainties.** **What remains open** lists what the sources leave unresolved: wording that can be read two ways, exceptions that might apply, sources that disagree, and things the sources do not address. Each comes with a suggested follow-up question under **Ask a follow-up**; select one to send it in the same conversation. Scope questions are collapsed under **Narrow the scope (optional)**. The tool is designed to raise a doubt rather than hide one.

![Open points and suggested follow-up questions](docs/screenshots/ui-open-points-followups.jpg)

- **Check a citation.** Click a page button to open the document at that passage, highlighted.

![Source viewer with the cited passage highlighted](docs/screenshots/ui-citation-viewer.jpg)

- **Look up a source.** The **Documents** tab lists all 81 sources by type (federal regulations and statutes, New York statutes and regulations, State guidance, other). Typing filters by title and also searches the text of every source, showing the matching passage and page. Press `/` anywhere to jump to the box, and use the arrow keys and Enter to move through results. Lookup is entirely local and never calls AWS.

![Documents tab: browse by type](docs/screenshots/ui-documents-browse.jpg)

![Phrase search across all sources](docs/screenshots/ui-documents-search.jpg)

- **Read a whole document.** Opening a source shows it in the same viewer used for citations. **Find in document** marks every match and steps through them, on PDF pages and in saved web pages alike.

![PDF opened from a search result with every match highlighted](docs/screenshots/ui-document-pdf-find.jpg)

![Saved web page with find-in-document](docs/screenshots/ui-document-text-find.jpg)

- **See what an answer used.** Sources cited in the current conversation are listed first in the Documents tab and open at the quoted passage.

![Documents tab with the sources cited in this research listed first](docs/screenshots/ui-documents-cited.jpg)

- **Use it on a phone or narrow window.** The sidebar becomes a drawer behind the menu button.

<img src="docs/screenshots/ui-mobile-answer.jpg" width="260" alt="Answer on a narrow screen"> <img src="docs/screenshots/ui-mobile-documents.jpg" width="260" alt="Documents tab on a narrow screen">

- **Choose the models.** By default Qwen3 32B on Amazon Bedrock plans the search and screens passages, and Mistral Large 3 writes the answer. Settings also offers Nova Lite and Nova Pro, or a model already installed in Ollama, LM Studio, or another local server.
- **Inspect a run.** **View debug report** shows every step: the search plan, the passages read, the instructions given to the model, and what each step returned.

### More examples

| | |
| --- | --- |
| ![Progress steps while a question runs](docs/screenshots/ui-progress.jpg) | ![Service Legacy question: direct answer with its source](docs/screenshots/ui-answer-service-legacy.jpg) |
| Step tracker while a question runs | Service Legacy status (Minimum Needs Requirement) |
| ![Medicare and Medicaid DME question with three sources](docs/screenshots/ui-answer-dme.jpg) | ![Follow-up turn in the same thread](docs/screenshots/ui-thread-follow-up.jpg) |
| Medicare or MLTC for DME: one chip per source, a button per page | A follow-up sent from a suggested question, same thread |
| ![Supporting findings with source chips](docs/screenshots/ui-supporting-findings.jpg) | ![Title matches highlighted in the Documents tab](docs/screenshots/ui-documents-title-match.jpg) |
| Supporting findings, each with its sources | Search terms highlighted in matching titles |
| ![Statute opened with find-in-document](docs/screenshots/ui-document-statute-find.jpg) | ![Documents search with no results](docs/screenshots/ui-documents-no-results.jpg) |
| Statute opened from the Documents tab, match highlighted | No-match state |

The answer screenshots use the sample questions from the CSSNY design brief, answered on Bedrock. The model settings screenshot above is from an earlier version of the interface.

## How the harness works

A model answers well from a dozen clean passages and badly from fifty mixed ones, and the passage that answers a question is usually written in words the advocate did not use. The harness is built around getting the right dozen passages in front of the model, then checking what it wrote.

```mermaid
flowchart TD
    Q["Advocate question"] --> G["Check the request: research, or something the tool must not do?"]
    G -->|Outside the tool's limits| N["Explain the limit; no search"]
    G -->|Research| P["Plan: restate the question and write search texts in the sources' vocabulary"]
    P --> S["Search the approved sources on the laptop"]
    S --> R["Read: many small calls keep the passages worth quoting"]
    R --> T["Triage: choose the key passages"]
    T --> A["Answer from those passages: findings first, then the direct answer"]
    A --> C["Check each finding against the passages it cites"]
    A --> D["Second reader: raise doubts and follow-up questions"]
    C --> O["Validate exact quotations and citations"]
    D --> O
    O --> F["Answer, findings, quotations, uncertainties"]
```

Models only ever return passage identifiers; the program resolves each one to exact source text, so a quotation cannot be invented. Searching runs on your laptop over the downloaded indexes. Answer generation sends the question and selected source text to Amazon Bedrock. [How the harness works](docs/harness.md) describes each step, and [the prompts](docs/prompts.md) shows the instructions.

## Current limits

This is a research prototype, not a validated legal advice system. On the four client reference cases the answers reach the expected conclusion with the expected sources in most runs, but they were judged by the developers, not by a legal reviewer, and the same question can produce somewhat different supporting passages from one run to the next. A matching quotation and a passed support check do not prove an answer is legally correct or complete.

Research is limited to the supplied sources: 81 documents, about 7,000 searchable passages. The tool does not verify that a source is current, and some sources contain old figures. Do not enter client identifiers, and do not ask the tool to access private case records, decide whether an individual's rights were violated, guarantee an outcome, or recommend client-specific action.

## Further reading

- [How the harness works](docs/harness.md): each step of the pipeline and its settings.
- [The prompts](docs/prompts.md): the instructions behind each step.
- [Technical README](README-technical.md): AWS setup, permissions, storage, spending controls, and developer commands.
- [Chat and document viewer](docs/local-interface.md).
- [Data release and verification](docs/data-bundle.md).
