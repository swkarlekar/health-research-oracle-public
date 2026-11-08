# Offline examples for the PDF-only release

These files demonstrate lexical search, evidence-packet construction, and configurable token estimates for the corrected corpus. They are not model answers or a client benchmark. No model was invoked.

The generic Medicaid disenrollment question retrieves some Medicare Part 423 passages before more applicable Medicaid material. This is a known limitation of lexical search without enforced program/plan filtering. The `--program` and `--plan-type` arguments record research scope; they do not establish passage applicability.

| Packet | Passages | Distinct documents | Estimated input tokens | Assumed output | Estimated total |
| --- | ---: | ---: | ---: | ---: | ---: |
| `disenrollment.packet.json` | 6 | 4 | 13,789 | 1,500 | 15,289 |
| `disenrollment.compact.packet.json` | 4 | 3 | 10,685 | 1,500 | 12,185 |

Both estimates exceed the proposed, unenforced 12,000-token total target. They assume four characters per token; they are not measurements from a model tokenizer. Reducing passages also reduces evidence coverage, and no answer-quality improvement is claimed.

The estimate JSON files use illustrative rates of $3 per million input tokens and $15 per million output tokens, with 120,000 assumed annual responses. These are configurable assumptions, not Qwen or AWS prices. They exclude hosting, retrieval, training, development, expert review, extra verification calls, and retries. GPU-hour estimates elsewhere in the package describe a different accounting scenario.

Run from the corpus folder to reproduce:

```bash
python3 scripts/research.py search "MLTC involuntary disenrollment" \
  --limit 6 --output examples/mltc-disenrollment.search.json

python3 scripts/research.py packet \
  "What are the reasons that a Medicaid plan may disenroll a member?" \
  --program "New York Medicaid" --plan-type unknown --limit 6 \
  --output examples/disenrollment.packet.json

python3 scripts/research.py estimate \
  --packet examples/disenrollment.packet.json \
  --input-per-million 3 --output-per-million 15 --output-tokens 1500 \
  --characters-per-token 4 --queries-per-hour 60 --hours-per-day 8 \
  --days-per-year 250 --output examples/disenrollment.token-estimate.json
```

For the compact variant, use `--limit 4` and the `disenrollment.compact` filenames. Packet IDs and timestamps change when regenerated. The corpus snapshot field ties each packet to the packaged database; the renderer rejects a stale packet after source changes. Acquisition gaps and source qualifications remain visible in packet metadata and deterministic rendering.
