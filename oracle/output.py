"""Two views of one validated run: a concise answer and opt-in diagnostics."""
from __future__ import annotations

import copy
import json
from time import monotonic

from .corpus import base


def demo_result(result):
    """Keep material qualifications; exclude operational inventory from the answer."""
    keys = ("status", "findings", "quotes", "uncertainties", "clarifying_questions")
    view = {key: copy.deepcopy(result.get(key, [])) for key in keys}
    view["status"] = result["status"]
    if result.get('answer_style') == 'source_quotations':
        view['answer_style'] = 'source_quotations'
    if result.get('request_boundary_only'):
        view['request_boundary_only'] = True
    def citation(item):
        return {key: item.get(key) for key in ("evidence_id", "title", "source_url", "page", "page_end", "slide", "source_locator")}
    for item in view["findings"] + view["uncertainties"]:
        if 'citations' in item:
            item['citations'] = [citation(c) for c in item['citations']]
        if item.get('kind') == 'context_and_relationship_limits':
            item['description'] = 'Some connected source material remains unresolved or could not be reviewed. The answer does not establish that the sources are complete or current.'
    view['quotes'] = [{"text": q['text'], "citation": citation(q['citation'])} for q in view['quotes']]
    view["limitations"] = []
    if result.get('request_boundary_only'):
        view['limitations'].append('Request capability check only; no corpus search was run.')
    elif result.get("model_invoked") and not result.get('simulation', {}).get('enabled'):
        view["limitations"].append("Based on the supplied corpus; current legal applicability has not been independently verified.")
    if result.get("simulation", {}).get("enabled"):
        view['mode'] = 'api_check'
        view["limitations"].append("API CHECK ONLY: simulated model responses and lexical retrieval; this is not a research answer.")
    if result["status"] == "evidence_only":
        view["limitations"].append("Evidence retrieval only; no model answer was generated. Use the debug view to inspect the passages.")
    return view


def demo_markdown(result):
    view = demo_result(result)
    literal = base.markdown_literal
    lines = []
    if result.get("simulation", {}).get("enabled"):
        lines += ["> API CHECK ONLY — simulated responses, not a research answer.", ""]
    if view['status'] == 'failed':
        lines += ["The research run could not be completed. No answer was accepted.", ""]
    elif view.get('request_boundary_only'):
        lines += ["This request is limited to public-rule research; no source search was run.", ""]
    elif view["status"] == "not_found":
        lines += ["I could not establish a suitable answer from the supplied evidence.", ""]
    elif view["status"] == "evidence_only":
        lines += ["Evidence retrieved; no model answer generated.", ""]
    elif view["status"] in {"partial", "currency_unverified", "conflicting_sources"}:
        lines += ["**Qualified research result**", ""]
    for finding in view["findings"]:
        refs = "; ".join(dict.fromkeys(base.source_link(c) for c in finding.get("citations", [])))
        lines += [literal(finding["text"]) + (f" ({refs})" if refs else ""), ""]
    if view.get('answer_style') == 'source_quotations' and view['quotes']:
        lines += ['**Source findings — verbatim quotations**', '']
    # Never shorten a verified quotation: doing so could remove an exception.
    for quote in view["quotes"]:
        ref = quote["citation"]
        location = f", page {ref['page']}" if ref.get("page") is not None else ""
        if ref.get('page_end') is not None and ref['page_end'] != ref.get('page'):
            location = f", pages {ref['page']}–{ref['page_end']}"
        lines += [f"{base.source_link(ref)}{location}", ""]
        expanded = len(quote['text']) > 1800
        if expanded:
            lines += ['<details>', '<summary>Read the full supporting quotation</summary>', '']
        lines += ["> " + literal(line) for line in quote["text"].splitlines()] + [""]
        if expanded:
            lines += ['</details>', '']
    if view["uncertainties"]:
        lines += ["**Limits and unresolved questions**", ""]
        for item in view["uncertainties"]:
            refs = "; ".join(dict.fromkeys(base.source_link(c) for c in item.get("citations", [])))
            lines.append("- " + literal(item["description"]) + (f" ({refs})" if refs else ""))
        lines.append("")
    questions = list(view["clarifying_questions"])
    questions += [u["question_for_user"] for u in view["uncertainties"] if u.get("question_for_user")]
    if questions:
        lines += ["**More information needed**", ""]
        lines += ["- " + literal(q) for q in dict.fromkeys(questions)] + [""]
    lines += [literal(text) for text in view["limitations"]] + [""]
    return "\n".join(lines)


class DebugTrace:
    """Record observable evidence and decisions, never private reasoning tokens.

    Deliberately opt-in: source text and the non-identifying research query are
    present in this local report. No credentials, HTTP headers, or raw provider
    responses are collected. Model output is restricted to the public contract.
    """
    def __init__(self):
        self.started = monotonic()
        self.events = []
        self.evidence = {}
        self.document_metadata = {}

    def event(self, stage, **details):
        self.events.append({"stage": stage, "elapsed_seconds": round(monotonic() - self.started, 3),
                            **copy.deepcopy(details)})

    def packet(self, stage, packet, corpus=None):
        for item in packet["evidence"]:
            self.evidence[item["chunk_id"]] = copy.deepcopy(item)
        if corpus is not None:
            for edge in (packet.get('context_expansion') or {}).get('relationships', []):
                for key in ('source_document_id', 'target_document_id'):
                    identity = edge.get(key)
                    if identity and identity not in self.document_metadata:
                        row = corpus.connection.execute('SELECT metadata_json FROM documents WHERE document_id=?', (identity,)).fetchone()
                        if row:
                            doc = json.loads(row[0])
                            self.document_metadata[identity] = {k: doc.get(k) for k in ('title', 'source_url')}
        self.event(stage, packet_id=packet["packet_id"], scope=packet["scope"],
                   retrieval=packet["retrieval"],
                   evidence_ids=[{"evidence_id": e["evidence_id"], "chunk_id": e["chunk_id"]} for e in packet["evidence"]],
                   context_expansion=packet.get("context_expansion"))

    def model_started(self, role, model, system, payload):
        self.event("model_started", role=role,
                   model=getattr(model, "model", getattr(model, "model_id", None)),
                   system_instructions=system,
                   evidence_ids=[e["evidence_id"] for e in payload.get("evidence", [])],
                   supplied_evidence=payload.get('evidence', []),
                   supporting_input={key: value for key, value in payload.items() if key != 'evidence'})

    def model_completed(self, role, output, usage=None):
        # An allowlist excludes accidental provider thinking/reasoning fields.
        if role == 'request_classification':
            public = {'requests': [{key: item[key] for key in ('category', 'request_text') if key in item}
                                   for item in output.get('requests', []) if isinstance(item, dict)],
                      'missing_attributes': output.get('missing_attributes', [])}
        elif role == 'public_research_check':
            public = {key: output[key] for key in ('explicit_public_research', 'request_text', 'search_queries', 'search_focus') if key in output}
        elif role == "draft":
            public = {k: output[k] for k in ("status", "findings", "quotes", "uncertainties", "clarifying_questions") if k in output}
        else:
            public = {k: output[k] for k in ("checks", "issues", "review_notes", "additional_query", "metadata_proxy_evidence_ids") if k in output}
            response = output.get("response", {})
            public["response"] = {k: response[k] for k in ("status", "findings", "quotes", "uncertainties", "clarifying_questions") if k in response}
        self.event("model_completed", role=role, decision_summary=public, usage=usage)

    def report(self, result=None, error=None):
        documents = {}
        for item in self.evidence.values():
            doc = documents.setdefault(item['document_id'], {'document_id': item['document_id'],
                'title': item.get('title'), 'source_url': item.get('source_url'),
                'retrieved': False, 'expanded_context_loaded': False, 'graph_connected': False})
            doc['retrieved'] |= not item['chunk_id'].startswith('ctx:')
            doc['expanded_context_loaded'] |= item['chunk_id'].startswith('ctx:')
        for event in self.events:
            for edge in (event.get('context_expansion') or {}).get('relationships', []):
                for key in ('source_document_id', 'target_document_id'):
                    identity = edge.get(key)
                    if identity:
                        doc = documents.setdefault(identity, {'document_id': identity, 'retrieved': False,
                            'expanded_context_loaded': False, 'graph_connected': False})
                        doc['graph_connected'] = True
        for identity, metadata in self.document_metadata.items():
            if identity in documents:
                documents[identity].update(metadata)
        return {"debug_schema_version": "1.0", "status": "failed" if error else result["status"],
                "trace_policy": "Observable execution and concise evidence-based decision summaries only; private chain-of-thought is neither requested nor collected.",
                "documents": list(documents.values()),
                "evidence": list(self.evidence.values()), "events": copy.deepcopy(self.events),
                "result": copy.deepcopy(result), "error": error}


def debug_markdown(report):
    """A navigable index; the JSON report retains exact passage text and offsets."""
    literal = base.markdown_literal
    lines = ["# Oracle debugging report", "", literal(report["trace_policy"]), "",
             f"Status: {literal(report['status'])}", "", "## Document access and graph connections", "",
             "Connected documents whose context was not loaded were not supplied as evidence to the model.", "",
             "| Document | Retrieved | Expanded context loaded | Graph connected |", "| --- | --- | --- | --- |"]
    for doc in report['documents']:
        lines.append(f"| {base.source_link(doc)} ({doc['document_id']}) | {doc['retrieved']} | {doc['expanded_context_loaded']} | {doc['graph_connected']} |")
    lines += ['', '## Execution timeline', '', '| Step | Role | Seconds |', '| --- | --- | --- |']
    for event in report['events']:
        lines.append(f"| {event['stage']} | {event.get('role', '')} | {event['elapsed_seconds']} |")
    lines += ['', '## Model review conclusions', '']
    for event in report['events']:
        if event['stage'] == 'model_completed' and event.get('role') == 'review':
            summary = event['decision_summary']
            lines += [f"Review completed at {event['elapsed_seconds']}s.", '']
            lines += ['- ' + literal(note) for note in summary.get('review_notes', [])]
            lines += ['- Unresolved issue: ' + literal(issue) for issue in summary.get('issues', [])]
            lines += ['- Checks: ' + literal(json.dumps(summary.get('checks', {})))]
            if summary.get('additional_query'):
                lines += ['- Additional corpus search: ' + literal(summary['additional_query'])]
            lines.append('')
    docs = {doc['document_id']: doc for doc in report['documents']}
    edges = {edge['relationship_id']: edge for event in report['events']
             for edge in (event.get('context_expansion') or {}).get('relationships', [])}
    if edges:
        lines += ['## Graph connections considered', '',
                  '| Relationship | Source | Target | Status | Support context loaded |', '| --- | --- | --- | --- | --- |']
        for edge in edges.values():
            source = base.source_link(docs.get(edge['source_document_id'], {'document_id': edge['source_document_id']}))
            target = base.source_link(docs[edge['target_document_id']]) if edge.get('target_document_id') in docs else literal(edge.get('target_reference') or 'Unresolved target')
            lines.append(f"| {literal(edge['relation_type'])} ({edge['relationship_id'][:12]}) | {source} | {target} | {literal(edge['status'])} | {bool(edge.get('evidence_chunk_ids'))} |")
    lines += ["", "## Evidence accessed", ""]
    for item in report["evidence"]:
        kind = "expanded context" if item["chunk_id"].startswith("ctx:") else "retrieved chunk"
        lines += [f"### {item['evidence_id']} — {base.source_link(item)}", "",
                  f"{kind}; document `{item['document_id']}`; chunk `{item['chunk_id']}`; page {item.get('page')}; "
                  f"document characters {item.get('document_start_char')}–{item.get('document_end_char')}", ""]
        lines += ["> " + literal(line) for line in item["text"].splitlines()] + [""]
    lines += ["## Execution, graph connections, and review decisions", ""]
    for event in report["events"]:
        lines += [f"### {event['stage']} ({event['elapsed_seconds']}s)", "", "```json",
                  json.dumps(event, ensure_ascii=False, indent=2), "```", ""]
    if report.get("error"):
        lines += ["## Rejection", "", literal(report["error"]), ""]
    if report.get("result"):
        lines += ["## Full validated result and audit", "", "```json",
                  json.dumps(report["result"], ensure_ascii=False, indent=2), "```", ""]
    return "\n".join(lines)
