# Local chat and document viewer

Run `make setup` once, then `make run`; the launcher opens the page in Google Chrome. See the
[main README](../README.md) for prerequisites and AWS sign-in. Nothing is hosted by the project: the server
binds only to your own computer, and one research run is accepted at a time.

## Settings

**⚙ Model settings** chooses where answers are generated. Amazon Bedrock is the default, with Qwen3 32B
planning and screening and, if its box is ticked, Mistral Large 3 writing the answer. Nova Lite and Nova
Pro are alternatives, and models already installed in Ollama, LM Studio or another local server can be used
instead. Model discovery reads metadata only; it never loads or downloads a model.

Before any paid call you check the AWS account and explicitly authorize a session allowance. The page
shows what was approved, when it expires, and the per-computer spending total, and it can disable paid
calls. See the [technical README](../README-technical.md#model-settings-and-spending).

## Asking

Type a question or pick one from **Example questions**: the four client reference cases, five boundary
tests, and 15 synthetic examples. Plan type is inferred from the question. Expand the scope button to
change the program. Follow-ups can build on your earlier questions in the same
conversation; earlier generated answers are never used as evidence. Conversations are stored locally in
`artifacts/ui/history.sqlite`.

## Reading an answer

A completed answer shows the direct answer, the findings with their sources, the supporting quotations, and
a section of uncertainties and follow-up questions. While a question is running, the page shows which step
is in progress.

**View debug report** opens the full record of a run: the search plan, the passages read, the instructions
and inputs for each model call, each call's structured output, token usage, and timings. Failed runs keep
their diagnostics and withhold the rejected answer.

## Viewing a source

Click a citation or **View in document** to open the saved PDF inside the page, scrolled to the quotation.
Yellow overlays mark the matching text; partial matches are labelled, because extraction and page layout can
differ. Other sources open in a plain text view; saved web pages are never executed. A highlight shows where
the text is, not whether it applies.

The viewer's official-source link opens the publisher's own site in a separate tab. That site is not
searched by the tool.

![Official eCFR source opened from a citation](screenshots/IMG_4666.png)
