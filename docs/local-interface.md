# Local chat and document viewer

Run `make setup` once, then `make run`; the launcher opens the localhost page in Google Chrome. See the [main README](../README.md) for prerequisites and AWS SSO. Nothing is hosted by the project. Setup downloads the verified data Release, not model weights.

The welcome page greets the advocate; the header links to CSSNY using its official logo. **⚙ Model settings** selects Bedrock Qwen3 32B by default, with Nova Lite/Pro alternatives or installed local models from Ollama, LM Studio, and compatible loopback servers. Model discovery reads metadata without loading or downloading weights. Local inference is memory-guarded and can be slow. There is no offline/keyword chatbot option.

Source retrieval uses the exported Titan document vectors locally, followed by the local SQLite relationship map and bounded context expansion. New query vectors still need AWS Titan; repeats reuse the local cache. Check the local AWS SSO profile/account and explicitly authorize a session allowance before paid calls. Local-only authorization approves query embeddings, not later Bedrock generation. Saving a model selection alone does not enable spending. The settings page shows grant scope/expiry and the persistent per-computer $10 cap, and can disable paid calls. See [technical details](../README-technical.md).

Enter a question or expand **Example questions** in the sidebar. The four advocate cases appear first, with simplified and original wording where supplied, followed by the 20 synthetic scenarios. The advocate cases have seven question variants because case 4 has only simplified wording. Their supplied acceptable answers are held separately for evaluation and are never supplied to the answering model. Conversations persist in `artifacts/ui/history.sqlite`. Follow-ups can include earlier user questions; previous generated answers are never source evidence. **New research** starts another conversation. Avoid identifying client information. The remembered conversation is scoped to its local history database; an unavailable remembered conversation starts a new view without deleting any saved history.

Plan type is inferred from the question; there is no duplicate plan dropdown. Expand the scope button to adjust program/jurisdiction or **Rules as of**, the effective research date for historical questions. A chosen date is not proof that the corpus supplies the applicable historical edition. Blank means unspecified; follow-ups retain the date until explicitly cleared. The system can ask for missing non-identifying research attributes. Two query-only checks identify requested outputs and explicit public research before source retrieval. A boundary-only result says that no source search was run; mixed requests can continue with fixed capability limits. A normal source answer uses four model calls, up to five with an additional search/review.

Completed turns show accepted findings, supporting verbatim passages, citations, and unresolved questions. **View debug report** opens retrieved/expanded passages, graph connections, model inputs, observable structured outputs, review checks, usage, and the timeline. Failed runs retain diagnostics while withholding rejected answers. Private chain-of-thought is not requested or stored.

Click a citation or **View in document** to open the saved PDF inside the interface. Pages scroll continuously, load near the viewport, and support page jumps and zoom. Yellow overlays locate matching text; partial matches are labeled because extraction and layout can differ. Original files are hash-checked and unchanged. Non-PDF sources use a safe text view; saved HTML is never executed. Original-source hyperlinks remain available for further reading. A highlight verifies text location, not legal applicability.

Default cloud expansion is bounded to 16,000 characters; local generation uses a smaller 8,000-character expansion to control memory/context. Debug reports retain omissions. One research run is accepted at a time; the server binds only to loopback. The launcher checks checkout/code/data identity before reusing a running server. Stop an old server before restarting updated code.

Prior validation included a real HTTP/Ollama scenario and a 207-page PDF with seven matched highlight boxes. That answer was partial and does not validate the new model choices or legal completeness. Saved AWS trial answers are importable only with an exact-result content-review record and canonical quotation checks; known problematic trial answers remain withheld. There is no automatic paid replay of a saved example. Current and experimental prompt limitations are [documented separately](prompt-search.md).

## Following an official source

The viewer's official-source link opens the external publisher separately from corpus-grounded research. This screenshot shows eCFR, not an additional website searched by the LLM.

![Official eCFR source opened from a citation](screenshots/IMG_4666.png)
