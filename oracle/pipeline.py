"""Research pipeline: plan, search widely, screen passages, pick key passages, answer, check support.

    question
      -> plan        one call: restated question, issues, legal concepts, source-style search texts
      -> search      many cheap local searches (paragraph vectors, chunk vectors, keywords), fused into a pool
      -> read        parallel small calls: which passages would a careful researcher keep?
      -> triage      one call: the dozen passages the answer must rest on
      -> answer      one call over that short list: findings first, then the direct answer
      -> check       one call: does each finding say only what its passages say?

Why this shape: the answer model reasons well over a dozen clean passages and
badly over fifty mixed ones, and the passage that answers a question is usually
written in vocabulary the advocate did not use. Everything before the answer
exists to put the right dozen passages in front of it. Recall is favoured over
precision throughout; the final response carries extra flagged passages.

Python owns identity: models only ever return passage ids, and every id is
resolved against the pinned canonical chunk before it is shown.
"""
from __future__ import annotations

import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from .corpus import HarnessError, ROOT, base
from .paragraphs import ParagraphKeywords, ParagraphVectors, search_terms
from .pipeline_prompts import ANSWER_POLICY, DOUBTS_POLICY, PLANNER_POLICY, READER_POLICY, TRIAGE_POLICY, VERIFY_POLICY
from .policy import check_input
from .quotations import passages

CONFIG = base.load_json(ROOT / 'config/research-pipeline.json')
LIMITS = CONFIG['limits']


def _strings(count=None):
    return {'type': 'array', 'items': {'type': 'string'}}


PLAN_SCHEMA = {'type': 'object', 'additionalProperties': False,
    'required': ['restated_question', 'issues', 'legal_concepts', 'passages', 'key_phrases', 'likely_documents'],
    'properties': {'restated_question': {'type': 'string'}, 'issues': _strings(), 'legal_concepts': _strings(),
                   'passages': _strings(), 'key_phrases': _strings(), 'likely_documents': _strings()}}
READ_SCHEMA = {'type': 'object', 'additionalProperties': False, 'required': ['selected'],
    'properties': {'selected': {'type': 'array', 'items': {'type': 'object', 'additionalProperties': False,
        'required': ['id', 'grade', 'reason'],
        'properties': {'id': {'type': 'string'}, 'grade': {'type': 'integer'}, 'reason': {'type': 'string'}}}}}}
TRIAGE_SCHEMA = {'type': 'object', 'additionalProperties': False, 'required': ['needs', 'key_passages'],
    'properties': {'needs': _strings(), 'key_passages': {'type': 'array', 'items': {
        'type': 'object', 'additionalProperties': False, 'required': ['quote_id', 'rule', 'applies_to'],
        'properties': {'quote_id': {'type': 'string'}, 'rule': {'type': 'string'}, 'applies_to': {'type': 'string'}}}}}}
ANSWER_SCHEMA = {'type': 'object', 'additionalProperties': False,
    'required': ['findings', 'basis', 'answer', 'answer_quote_ids', 'not_addressed', 'status'],
    'properties': {'findings': {'type': 'array', 'items': {'type': 'object', 'additionalProperties': False,
                       'required': ['text', 'quote_ids'],
                       'properties': {'text': {'type': 'string'}, 'quote_ids': _strings()}}},
                   'basis': {'type': 'string'},
                   'answer': {'type': 'string'}, 'answer_quote_ids': _strings(),
                   'not_addressed': _strings(),
                   'status': {'type': 'string', 'enum': ['supported', 'partial', 'not_found']}}}
DOUBTS_SCHEMA = {'type': 'object', 'additionalProperties': False, 'required': ['doubts'],
    'properties': {'doubts': {'type': 'array', 'items': {'type': 'object', 'additionalProperties': False,
        'required': ['issue', 'readings', 'question', 'quote_ids'],
        'properties': {'issue': {'type': 'string'}, 'readings': {'type': 'string'},
                       'question': {'type': 'string'}, 'quote_ids': _strings()}}}}}
VERIFY_SCHEMA = {'type': 'object', 'additionalProperties': False, 'required': ['findings'],
    'properties': {'findings': {'type': 'array', 'items': {'type': 'object', 'additionalProperties': False,
        'required': ['index', 'problem', 'verdict', 'corrected_text'],
        'properties': {'index': {'type': 'integer'}, 'problem': {'type': 'string'},
                       'verdict': {'type': 'string', 'enum': ['supported', 'overstated', 'unsupported']},
                       'corrected_text': {'type': 'string'}}}}}}


# ---------------------------------------------------------------- documents

def classify(title):
    """(kind of authority, plan family or None) from a document title."""
    text = ' '.join((title or '').split())
    for pattern, kind, family in CONFIG['authority_rules']:
        if re.search(pattern, text):
            return kind, family
    return 'Other guidance', None


def off_scope(title, plan_type):
    """True when a document was written for another program or plan type."""
    family = classify(title)[1]
    if family is None:
        return False
    if family == 'MEDICARE':
        return True
    allowed = CONFIG['in_scope_families'].get((plan_type or '').upper())
    return allowed is not None and family not in allowed


def source_label(item, plan_type):
    kind = classify(item.get('title'))[0]
    flag = ' — DIFFERENT PROGRAM OR PLAN TYPE than the question' if off_scope(item.get('title'), plan_type) else ''
    heading = item.get('heading_path')
    heading = ' > '.join(heading) if isinstance(heading, list) else str(heading or '')
    parts = [' '.join((item.get('title') or '').split()),
             f"page {item['page']}" if item.get('page') is not None else '', heading[:160]]
    return f'[{kind}{flag}] ' + ' | '.join(p for p in parts if p)


def visible_passages(item):
    """(original index, passage) for the non-blank passages of a chunk; ids stay those of quotations.passages."""
    return [(i, p) for i, p in enumerate(passages(dict(item, evidence_id='E0'))) if p['text'].strip()]


# ---------------------------------------------------------------- retrieval

class Searcher:
    """Local search lanes over one corpus. Use from a single thread (sqlite and the query cache are per-thread)."""

    def __init__(self, retriever):
        self.retriever = retriever
        self.permitted = {d for d, p in retriever.policy.items() if p.get('retrieval_allowed') is True}
        self.chunk_document = dict(retriever.connection.execute('SELECT chunk_id, document_id FROM chunks'))
        rows = retriever.connection.execute(
            'SELECT c.document_id, MIN(f.title) FROM chunks c JOIN chunks_fts f ON f.chunk_id=c.chunk_id '
            'GROUP BY c.document_id ORDER BY MIN(f.title), c.document_id').fetchall()
        self.titles = {d: ' '.join((t or '').split()) for d, t in rows if d in self.permitted}
        self.catalog = [(f'D{i + 1}', d) for i, d in enumerate(self.titles)]
        self.keywords = ParagraphKeywords(retriever)
        try:
            self.paragraphs = ParagraphVectors().bind(self.chunk_document)
            if self.paragraphs.manifest.get('snapshot_sha256') != retriever.summary()['snapshot_sha256']:
                self.paragraphs = None
        except HarnessError:
            self.paragraphs = None      # Degrade to chunk vectors and keywords; recorded in the trace.
        self.vectors = {}
        self.embedding_calls = 0

    def document(self, chunk_id):
        return self.chunk_document.get(chunk_id)

    def allowed(self, chunk_id):
        return self.chunk_document.get(chunk_id) in self.permitted

    def title(self, chunk_id):
        return self.titles.get(self.chunk_document.get(chunk_id), '')

    def embed(self, text):
        if text not in self.vectors:
            self.vectors[text] = self.retriever.queries.embed(text)
            self.embedding_calls += not self.retriever.queries.last_usage.get('cache_hit', False)
        return self.vectors[text]

    def chunk_vector(self, text, limit):
        index = self.retriever.index
        matrix = getattr(index, '_matrix', None)
        if matrix is None:
            try:
                import numpy as np
                data = np.array([values for _, _, values, _ in index.rows], dtype='f4')
                data /= np.maximum(np.linalg.norm(data, axis=1, keepdims=True), 1e-9)
                index._matrix = matrix = (np, data, [meta['chunk_id'] for _, meta, _, _ in index.rows],
                                          np.array([meta['document_id'] in self.permitted for _, meta, _, _ in index.rows]))
            except ImportError:
                index._matrix = matrix = False
        if matrix is False:
            return [hit['chunk_id'] for hit in index.rank(self.embed(text), limit=limit, allowed_documents=self.permitted)]
        np, data, ids, allowed = matrix
        query = np.asarray(self.embed(text), dtype='f4')
        scores = np.where(allowed, data @ (query / np.linalg.norm(query)), -2.0)
        count = min(limit, len(scores))
        top = np.argpartition(-scores, count - 1)[:count]
        return [ids[i] for i in top[np.argsort(-scores[top])] if scores[i] > -1]

    def paragraph_vector(self, text, limit, documents=None):
        if self.paragraphs is None:
            return []
        return self.paragraphs.rank(self.embed(text), limit, documents if documents is not None else self.permitted)

    def chunk_keyword(self, text, limit):
        terms = search_terms(text)
        if not terms:
            return []
        expression = ' OR '.join('"' + t.replace('"', '') + '"' for t in terms)
        rows = self.retriever.connection.execute(
            'SELECT chunk_id FROM chunks_fts WHERE chunks_fts MATCH ? ORDER BY bm25(chunks_fts,0.0,3.0,2.0,1.0) LIMIT ?',
            (expression, limit * 3)).fetchall()
        return [r[0] for r in rows if self.allowed(r[0])][:limit]

    def phrase(self, text, limit):
        words = re.findall(r"[a-z0-9][a-z0-9\-]*", text.lower())
        if len(words) < 2:
            return []
        try:
            rows = self.retriever.connection.execute(
                'SELECT chunk_id FROM chunks_fts WHERE chunks_fts MATCH ? ORDER BY bm25(chunks_fts,0.0,3.0,2.0,1.0) LIMIT ?',
                ('"' + ' '.join(words) + '"', limit * 3)).fetchall()
        except Exception:
            return []
        return [r[0] for r in rows if self.allowed(r[0])][:limit]

    def core_documents(self, plan_type):
        table = CONFIG['core_documents']
        wanted = table['*'] + table.get((plan_type or '').upper(), table['UNKNOWN'])
        return {d for d, t in self.titles.items() if any(w.lower() in t.lower() for w in wanted)}

    def neighbors(self, chunk_ids, exclude, span, cap):
        found = []
        for chunk_id in chunk_ids:
            rows = [r[0] for r in self.retriever.connection.execute(
                "SELECT chunk_id FROM chunks WHERE document_id=? "
                "ORDER BY json_extract(metadata_json,'$.document_start_char'), chunk_id", (self.document(chunk_id),))]
            at = rows.index(chunk_id)
            for other in rows[max(0, at - span):at + span + 1]:
                if other not in exclude and other not in found:
                    found.append(other)
        return found[:cap]


def search_texts(question, plan):
    """The question, its restatement, and every planned search text; unsafe or empty texts are dropped."""
    texts = []
    for text in [question, plan['restated_question']] + plan['passages'] + plan['legal_concepts'] + plan['issues']:
        text = ' '.join(str(text).split())[:1600]
        if not text or text in texts or re.search(r'(?:https?://|www\.)', text, re.I):
            continue
        try:
            check_input(text, {})
        except HarnessError:
            continue
        texts.append(text)
    return texts[:28]


def candidate_pool(searcher, question, scope, plan):
    """Fuse all lanes with a rank formula that favours any lane's top hits; down-weight other programs' documents."""
    depth, weights, k0 = LIMITS['lane_depth'], CONFIG['lane_weights'], CONFIG['rank_constant']
    core = searcher.core_documents(scope.get('plan_type')) | set(plan['document_ids'])
    lanes = []
    for text in search_texts(question, plan):
        lanes.append(('paragraph_vector_core', searcher.paragraph_vector(text, depth, core)))
        lanes.append(('paragraph_vector', searcher.paragraph_vector(text, depth)))
        lanes.append(('chunk_vector', searcher.chunk_vector(text, depth)))
        lanes.append(('chunk_keyword', searcher.chunk_keyword(text, depth)))
        lanes.append(('paragraph_keyword', searcher.keywords.search(text, depth)))
    for text in dict.fromkeys(plan['key_phrases'] + plan['legal_concepts']):
        lanes.append(('phrase', searcher.phrase(text, 10)))
    scores = {}
    for kind, hits in lanes:
        for rank, chunk_id in enumerate(hits, 1):
            if searcher.allowed(chunk_id):
                scores[chunk_id] = scores.get(chunk_id, 0.0) + weights[kind] / (k0 + rank)
    for chunk_id in scores:
        if off_scope(searcher.title(chunk_id), scope.get('plan_type')):
            scores[chunk_id] *= CONFIG['off_scope_weight']
    pool = sorted(scores, key=lambda c: (-scores[c], c))[:LIMITS['pool_size']]
    return pool, {'lanes': len(lanes), 'candidates': len(scores), 'core_documents': len(core),
                  'paragraph_vectors': searcher.paragraphs is not None}


# ---------------------------------------------------------------- pipeline

class ResearchPipeline:
    def __init__(self, corpus, complete, synthesize=None, event=None):
        """complete/synthesize(role, system, payload, schema) -> dict. Both must meter and trace their calls."""
        self.retriever = getattr(corpus, 'retriever', corpus)
        self.graph = getattr(corpus, 'graph', None)       # relationship map, when the corpus carries one
        self.complete, self.synthesize = complete, synthesize or complete
        self.event = event or (lambda stage, **details: None)
        self.calls, self._lock = 0, threading.Lock()

    def _call(self, fn, role, system, payload, schema):
        with self._lock:
            self.calls += 1
        return fn(role, system, payload, schema)

    # -- plan
    def plan(self, searcher, question, scope):
        payload = {'catalog': [f'{code}: {searcher.titles[doc]}' for code, doc in searcher.catalog],
                   'question': question, 'scope': {k: scope.get(k) for k in ('program', 'plan_type')}}
        raw = self._call(self.complete, 'plan', PLANNER_POLICY.replace('{glossary}', CONFIG['glossary']), payload, PLAN_SCHEMA)
        codes = dict(searcher.catalog)
        clean = lambda values, count, size: [' '.join(str(v).split())[:size] for v in values if str(v).strip()][:count]
        plan = {'restated_question': ' '.join(str(raw['restated_question']).split())[:1200] or question,
                'issues': clean(raw['issues'], 3, 400) or [question[:400]],
                'legal_concepts': clean(raw['legal_concepts'], 8, 120),
                'passages': clean(raw['passages'], 10, 600),
                'key_phrases': clean(raw['key_phrases'], 10, 80),
                'document_ids': list(dict.fromkeys(codes[m.group(0)] for value in raw['likely_documents']
                                                   for m in [re.match(r'D\d+', str(value).strip())] if m and m.group(0) in codes))[:10]}
        base.reject_generated_urls({k: v for k, v in plan.items() if k != 'document_ids'})
        return plan

    # -- read
    def _read_batch(self, question, scope, plan, items, labels):
        excerpts, index = [], {}
        for number, item in enumerate(items, 1):
            shown = []
            for position, passage in visible_passages(item):
                identity = f'S{number}.{position + 1}'
                index[identity] = (item['chunk_id'], position)
                shown.append({'id': identity, 'text': passage['text']})
            excerpts.append({'label': labels[item['chunk_id']], 'passages': shown})
        payload = {'excerpts': excerpts, 'issues': plan['issues'], 'legal_concepts': plan['legal_concepts'], 'question': question,
                   'restated_question': plan['restated_question'],
                   'scope': {k: scope.get(k) for k in ('program', 'plan_type')}}
        try:
            output = self._call(self.complete, 'read', READER_POLICY.replace('{glossary}', CONFIG['glossary']), payload, READ_SCHEMA)
        except HarnessError as exc:
            # One unreadable batch must not sink the run; the loss is recorded.
            return {}, str(exc)
        selected = {}
        for entry in output['selected']:
            key = index.get(str(entry.get('id', '')).strip())
            if key and entry.get('grade') in (2, 3):
                selected[key] = max(selected.get(key, 0), entry['grade'])
        return selected, None

    def read(self, question, scope, plan, items, labels):
        batches, current, size = [], [], 0
        for item in items:
            if current and (size + len(item['text']) > LIMITS['read_batch_chars'] or len(current) >= LIMITS['read_batch_chunks']):
                batches.append(current)
                current, size = [], 0
            current.append(item)
            size += len(item['text'])
        if current:
            batches.append(current)
        selected, failures = {}, []
        with ThreadPoolExecutor(LIMITS['read_workers']) as pool:
            for result, failure in pool.map(lambda b: self._read_batch(question, scope, plan, b, labels), batches):
                for key, grade in result.items():
                    selected[key] = max(selected.get(key, 0), grade)
                if failure:
                    failures.append(failure)
        return selected, len(batches), failures

    # -- relationship map
    def referenced(self, searcher, seeds, exclude, cap):
        """Chunks holding the exact provisions that the kept passages cite, per the relationship map.

        Only references with a resolved target provision are followed, and only from text inside a kept chunk.
        The map supplies candidates to read; it never decides relevance, currency or supersession.
        """
        # Off by default (limits.reference_cap = 0): on the client cases it added reading time and no recall.
        if self.graph is None or cap <= 0:
            return [], 0
        found, followed, spans = [], 0, {}
        for chunk_id in seeds:
            item = self.retriever.chunk(chunk_id)
            document, low, high = item['document_id'], item['document_start_char'], item['document_end_char']
            try:
                edges = self.graph.relationships_for(document, include_candidates=True)
            except HarnessError:
                continue
            for edge in edges:
                qualifiers = edge.get('qualifiers') or {}
                targets = qualifiers.get('target_section_ids') or ([qualifiers['target_section_id']] if qualifiers.get('target_section_id') else [])
                if edge.get('source_document_id') != document or not edge.get('target_document_id') or not targets:
                    continue
                if edge.get('status') in ('model_rejected', 'rejected'):
                    continue
                inside = False
                for ref in edge.get('evidence', []):
                    section = self.graph.section(ref['section_id'])
                    inside |= section['document_id'] == document and low <= section['document_start_char'] + ref['start'] < high
                if not inside:
                    continue
                followed += 1
                for section_id in targets[:2]:
                    section = self.graph.section(section_id)
                    target = section['document_id']
                    if target not in searcher.permitted:
                        continue
                    if target not in spans:
                        spans[target] = self.retriever.connection.execute(
                            "SELECT chunk_id, json_extract(metadata_json,'$.document_start_char'), "
                            "json_extract(metadata_json,'$.document_end_char') FROM chunks WHERE document_id=?", (target,)).fetchall()
                    for other, start, end in spans[target]:
                        if start < section['document_end_char'] and end > section['document_start_char'] \
                                and other not in exclude and other not in found:
                            found.append(other)
        return found[:cap], followed

    # -- evidence
    def evidence(self, searcher, selected, order, items, plan_type):
        """Selected passages within the character budget: in-scope grade 3, in-scope grade 2, then other-program grade 3."""
        chosen, size, seen = [], 0, set()
        text = {(c, i): p['text'] for c in items for i, p in visible_passages(items[c])}
        for grade, other in ((3, False), (2, False), (3, True)):
            for chunk_id in order:
                if off_scope(searcher.title(chunk_id), plan_type) != other:
                    continue
                for key in sorted(k for k in selected if k[0] == chunk_id and selected[k] == grade):
                    normal = ' '.join(text[key].split())
                    if normal in seen or size + len(text[key]) > LIMITS['evidence_chars']:
                        continue        # identical text recurs in overlapping chunks and table copies
                    seen.add(normal)
                    chosen.append(key)
                    size += len(text[key])
        ranks = CONFIG['authority_order']

        def authority(chunk_id):
            kind = classify(searcher.title(chunk_id))[0]
            return (off_scope(searcher.title(chunk_id), plan_type),
                    next((i for i, name in enumerate(ranks) if kind.startswith(name)), len(ranks)))
        documents = list(dict.fromkeys(searcher.document(c) for c, _ in chosen))
        return sorted(chosen, key=lambda k: (authority(k[0]), documents.index(searcher.document(k[0])),
                                             items[k[0]]['document_start_char'], k[1]))

    # -- run
    def run(self, question, scope):
        timing, started = {}, time.monotonic()

        def lap(name):
            nonlocal started
            timing[name] = round(time.monotonic() - started, 2)
            started = time.monotonic()

        searcher = Searcher(self.retriever)
        plan_type = scope.get('plan_type')
        # Embed the question before any model call: an unapproved query-embedding miss must stop the run
        # before paid generation starts, exactly as the original loop does.
        searcher.embed(' '.join(question.split())[:1600])
        plan = self.plan(searcher, question, scope)
        lap('plan')
        pool, lanes = candidate_pool(searcher, question, scope, plan)
        self.event('research_search', plan=plan, pool=pool, **lanes, embedding_calls=searcher.embedding_calls)
        lap('search')
        if not pool:
            return None, None, {'plan': plan, 'timing': timing, 'model_calls': self.calls}
        items = {c: self.retriever.chunk(c) for c in pool}
        labels = {c: source_label(items[c], plan_type) for c in pool}
        head = pool[:LIMITS['double_read_head']]
        # Screening is noisy run to run; two independent reads of the head of the pool, in different batch
        # groupings, recover most of what a single read drops.
        with ThreadPoolExecutor(2) as both:
            first = both.submit(self.read, question, scope, plan, [items[c] for c in pool], labels)
            second = both.submit(self.read, question, scope, plan, [items[c] for c in reversed(head)], labels)
            (selected, batches_a, fail_a), (again, batches_b, fail_b) = first.result(), second.result()
        for key, grade in again.items():
            selected[key] = max(selected.get(key, 0), grade)
        best = {}
        for (chunk_id, _), grade in selected.items():
            best[chunk_id] = max(best.get(chunk_id, 0), grade)
        seeds = [c for c in pool if best.get(c) == 3] + [c for c in pool[:40] if best.get(c) == 2]
        near = searcher.neighbors(seeds, set(pool), LIMITS['neighbor_span'], LIMITS['neighbor_cap'])
        cited, references_followed = self.referenced(searcher, [c for c in pool if best.get(c) == 3], set(pool) | set(near),
                                                     LIMITS.get('reference_cap', 0))
        near = near + cited
        batches_c, fail_c = 0, []
        if near:
            items.update({c: self.retriever.chunk(c) for c in near})
            labels.update({c: source_label(items[c], plan_type) for c in near})
            more, batches_c, fail_c = self.read(question, scope, plan, [items[c] for c in near], labels)
            selected.update(more)
        # A table split across passages loses its header ("covered by the capitation") from its rows. When the
        # reader keeps any part of a table chunk, keep the whole table so a row is never read without its heading.
        for chunk_id in {c for c, _ in selected}:
            if _is_table(items[chunk_id]['text']):
                grade = max(g for (c, _), g in selected.items() if c == chunk_id)
                for position, _ in visible_passages(items[chunk_id]):
                    selected.setdefault((chunk_id, position), grade)
        lap('read')
        chosen = self.evidence(searcher, selected, pool + near, items, plan_type)
        size = lambda keys: sum(len(p['text']) for c in {k[0] for k in keys} for i, p in visible_passages(items[c]) if (c, i) in keys)
        info = {'plan': plan, 'pool_size': len(pool), 'neighbors': len(near), 'referenced_provisions': len(cited),
                'references_followed': references_followed, 'passages_selected': len(selected),
                'selected_chars': size(set(selected)), 'evidence_chars': size(set(chosen)),
                'evidence_passages': len(chosen), 'read_batches': batches_a + batches_b + batches_c,
                'read_failures': fail_a + fail_b + fail_c, 'search': lanes, 'embedding_calls': searcher.embedding_calls}
        self.event('research_read', **{k: v for k, v in info.items() if k != 'plan'})
        if not chosen:
            info.update(timing=timing, model_calls=self.calls)
            return None, None, info
        chunk_ids = list(dict.fromkeys(c for c, _ in chosen))
        packet = self.retriever.packet_from_chunks(question, scope, chunk_ids, {
            'engine': 'research_pipeline', 'mode': 'planned_multi_lane', 'semantic_retrieval': True,
            'requested_limit': LIMITS['pool_size'], 'collection_indexes_included': False,
            'completeness_verified': False, 'plan': plan, **lanes})
        evidence_id = {e['chunk_id']: e['evidence_id'] for e in packet['evidence']}
        quotes, sources = {}, []
        for chunk_id in chunk_ids:
            shown = []
            for position, passage in visible_passages(items[chunk_id]):
                if (chunk_id, position) in chosen:
                    identity = f'{evidence_id[chunk_id]}.Q{position + 1}'
                    quotes[identity] = {'chunk_id': chunk_id, 'start': passage['start'], 'end': passage['end'],
                                        'text': passage['text'], 'label': labels[chunk_id], 'grade': selected[(chunk_id, position)]}
                    shown.append({'quote_id': identity, 'text': passage['text']})
            sources.append({'label': labels[chunk_id], 'passages': shown})
        view = {'question': question, 'scope': {k: scope.get(k) for k in ('program', 'plan_type', 'research_date')}}

        # -- triage
        # Evidence too long for one careful read is triaged in batches; a final pass then chooses among the
        # batches' picks. Nothing the reader kept is discarded just to fit one prompt.
        def pick(subset):
            out = self._call(self.synthesize, 'triage', TRIAGE_POLICY, {**view, 'sources': subset}, TRIAGE_SCHEMA)
            return (list(dict.fromkeys(k['quote_id'] for k in out['key_passages'] if k.get('quote_id') in quotes)),
                    [' '.join(str(n).split())[:400] for n in out['needs'] if str(n).strip()][:3])

        batches, current, used = [], [], 0
        for source in sources:
            length = sum(len(p['text']) for p in source['passages'])
            if current and used + length > LIMITS['triage_batch_chars']:
                batches.append(current)
                current, used = [], 0
            current.append(source)
            used += length
        batches.append(current)
        triage = {'batches': len(batches)}
        try:
            if len(batches) == 1:
                keys, needs = pick(batches[0])
            else:
                with ThreadPoolExecutor(min(4, len(batches))) as pool_:
                    picks = [k for keys_, _ in pool_.map(pick, batches) for k in keys_[:LIMITS['key_passages']]]
                union = [{'label': s['label'], 'passages': [p for p in s['passages'] if p['quote_id'] in picks]} for s in sources]
                keys, needs = pick([s for s in union if s['passages']])
        except HarnessError as exc:
            triage, keys, needs = {'failed': str(exc)}, [], []
        if not keys:        # fall back to the screener's own ranking rather than fail the run
            keys = [q for g in (3, 2) for q, v in quotes.items() if v['grade'] == g]
        keys = keys[:LIMITS['key_passages']]
        lap('triage')
        focus = [{'label': s['label'], 'passages': [p for p in s['passages'] if p['quote_id'] in keys]} for s in sources]
        focus = [s for s in focus if s['passages']]

        # -- answer
        draft = self._call(self.synthesize, 'answer', ANSWER_POLICY, {**view, 'needs': needs or plan['issues'], 'sources': focus}, ANSWER_SCHEMA)
        for finding in draft['findings']:
            finding['quote_ids'] = [q for q in dict.fromkeys(finding['quote_ids']) if q in keys][:2]
        draft['findings'] = [f for f in draft['findings'] if f['quote_ids'] and str(f['text']).strip()][:10]
        draft['answer_quote_ids'] = [q for q in dict.fromkeys(draft['answer_quote_ids']) if q in keys][:4]
        lap('answer')

        # -- check
        verdicts, check_error, doubts_error = {}, None, None
        draft['unclear'] = []

        def check():
            payload = {'findings': [{'cited_passages': [{'label': quotes[q]['label'], 'text': quotes[q]['text']} for q in f['quote_ids']],
                                     'finding': f['text'], 'index': i} for i, f in enumerate(draft['findings'], 1)]}
            checked = self._call(self.synthesize, 'verify', VERIFY_POLICY, payload, VERIFY_SCHEMA)
            return {v['index']: v for v in checked['findings'] if isinstance(v.get('index'), int)}

        def doubt():
            # A separate second reader raises ambiguities and exceptions. Asking the writer to do this in the
            # same call pulled its answer off the question.
            out = self._call(self.synthesize, 'doubts', DOUBTS_POLICY, {**view, 'draft_answer': draft['answer'], 'sources': focus}, DOUBTS_SCHEMA)
            return [dict(d, quote_ids=[q for q in dict.fromkeys(d['quote_ids']) if q in keys][:3]) for d in out['doubts']]

        if draft['findings']:
            with ThreadPoolExecutor(2) as pair:
                checking, doubting = pair.submit(check), pair.submit(doubt)
                try:
                    verdicts = checking.result()
                except HarnessError as exc:
                    check_error = str(exc)
                try:
                    draft['unclear'] = doubting.result()
                except HarnessError as exc:
                    doubts_error = str(exc)
        findings, dropped, corrected = [], 0, 0
        for number, finding in enumerate(draft['findings'], 1):
            verdict = verdicts.get(number, {})
            if verdict.get('verdict') == 'unsupported':
                dropped += 1
                continue
            text = finding['text']
            fixed = str(verdict.get('corrected_text', '')).strip()
            if verdict.get('verdict') == 'overstated' and fixed and fixed != text:
                text, corrected = fixed, corrected + 1
            findings.append({'text': text, 'quote_ids': finding['quote_ids']})
        lap('verify')

        info.update(needs=needs, key_passages=keys, basis=' '.join(str(draft['basis']).split())[:1200],
                    findings_dropped=dropped, findings_corrected=corrected, verify_error=check_error,
                    doubts_raised=len(draft['unclear']), doubts_error=doubts_error,
                    triage_failed=triage.get('failed'), timing=timing, model_calls=self.calls)
        response = assemble(packet, quotes, keys, draft, findings)
        return packet, response, info


def _is_table(text):
    """Extracted tables: tab-separated cells, or several lines laid out in columns."""
    return text.count('\t') >= 6 or len(re.findall(r'\S {4,}\S', text)) >= 6


def _sentence(text, limit=1600):
    text = ' '.join(str(text).split())
    if len(text) > limit:
        text = text[:limit]
        cut = max(text.rfind('. '), text.rfind('; '))
        text = text[:cut + 1] if cut > limit // 2 else text.rstrip() + '…'
    return text


_ADVICE = re.compile(r"\b(?:you should|your client should|advise (?:the|your) client|recommend that|file an appeal now)\b", re.I)


_IDS = re.compile(r"\s*\(?\bE\d+\.Q\d+(?:\s*(?:,|and)\s*E\d+\.Q\d+)*\)?")
_PERSONAL = re.compile(r"\b(?:name|birth|dob|address|email|phone|telephone|member (?:id|number)|diagnos\w*|social security|client documents|medical records)\b", re.I)


def assemble(packet, quotes, keys, draft, findings):
    """Map the pipeline's answer onto the validated response contract. The direct answer is the first finding."""
    evidence_id = {e['chunk_id']: e['evidence_id'] for e in packet['evidence']}
    scope = dict(packet['scope'], assumptions=[])

    def evidence(ids):
        return list(dict.fromkeys(evidence_id[quotes[q]['chunk_id']] for q in ids))

    # The research-only boundary is enforced again by the harness; drop an offending sentence here rather than
    # lose a whole answer to one stray phrase.
    findings = [f for f in findings if not _ADVICE.search(f['text'])]
    answer = _sentence(draft['answer'])
    gaps = [_sentence(g, 600) for g in draft['not_addressed'] if str(g).strip() and not _ADVICE.search(str(g))][:3]
    status = draft['status']
    if status == 'not_found' or not findings or not answer or _ADVICE.search(answer):
        description = ' '.join(gaps) or 'The retrieved passages do not answer this question. This does not establish that no rule exists.'
        return {'status': 'not_found', 'scope': scope, 'findings': [], 'quotes': [],
                'uncertainties': [{'kind': 'not_addressed', 'description': description, 'evidence_ids': [], 'question_for_user': None}],
                'clarifying_questions': []}
    lead = draft['answer_quote_ids'] or findings[0]['quote_ids']
    listed = [{'text': answer, 'evidence_ids': evidence(lead)}]
    listed += [{'text': _sentence(f['text']), 'evidence_ids': evidence(f['quote_ids'])} for f in findings[:10]]
    # Where a source can fairly be read two ways, say so and ask, rather than quietly choosing a reading.
    uncertainties, open_quotes = [], []
    for item in draft.get('unclear', [])[:3]:
        issue, readings, ask = (_IDS.sub('', _sentence(item[k], n)).strip() for k, n in (('issue', 500), ('readings', 900), ('question', 300)))
        if not issue or _ADVICE.search(issue + ' ' + readings) or _PERSONAL.search(ask):
            continue
        uncertainties.append({'kind': 'ambiguous_source', 'description': (issue + ' ' + readings).strip(),
                              'evidence_ids': evidence(item['quote_ids']), 'question_for_user': ask or None})
        open_quotes += [q for q in item['quote_ids'] if q not in open_quotes]
    cited = list(dict.fromkeys(list(lead) + [q for f in findings[:10] for q in f['quote_ids']]))
    # Recall over precision: also show the key passages the answer did not cite, then the screener's top grade.
    extra = [q for q in keys if q not in cited] + [q for q, v in quotes.items() if v['grade'] == 3 and q not in cited and q not in keys]
    cited += [q for q in open_quotes if q not in cited]
    extra = [q for q in extra if q not in cited]
    shown = cited + extra[:max(0, LIMITS['max_quotes'] - len(cited))]
    shown = shown[:30]
    uncertainties += [{'kind': 'not_addressed', 'description': g, 'evidence_ids': [], 'question_for_user': None} for g in gaps]
    if status == 'partial' and not uncertainties:
        uncertainties = [{'kind': 'not_addressed', 'description': 'The retrieved passages answer only part of the question.',
                          'evidence_ids': [], 'question_for_user': None}]
    return {'status': 'partial' if uncertainties else 'supported', 'scope': scope, 'findings': listed,
            'quotes': [{'chunk_id': quotes[q]['chunk_id'], 'start': quotes[q]['start'], 'end': quotes[q]['end']} for q in shown],
            'uncertainties': uncertainties, 'clarifying_questions': []}
