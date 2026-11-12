"""Paragraph-level retrieval aids over the pinned canonical chunks.

A 3,000-character chunk often buries the one sentence that answers a question,
so its embedding and its BM25 score are diluted. Two sidecar indexes address
that without changing the canonical corpus:

* a stemmed, paragraph-level keyword index, built in memory from the corpus
  database (no cost, about one second), and
* an optional paragraph-vector file made once with the same Titan model as the
  chunk vectors (see scripts/build_paragraph_index.py). Titan V2 vectors are
  nested, so the cached 1,024-dimensional query vectors are simply truncated to
  the stored dimension; no additional query embedding is ever made.

Both map every hit back to its parent canonical chunk. Quotation text and
offsets always come from the canonical chunk, never from these indexes.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
from pathlib import Path

from .corpus import HarnessError, ROOT, base

PARAGRAPH_VECTORS = ROOT / 'artifacts/aws-eval/paragraph-vectors.sqlite'
SPLITTER_VERSION = 'paragraphs-v1'
MODEL = 'amazon.titan-embed-text-v2:0'

_STOP = set(base.STOP_WORDS) | set(
    "if any there their his her he she them then than as at by not no so such will would should must shall "
    "have has had into out up also only other more most some same per its our your my am s t".split())


def search_terms(query, limit=40):
    """Lower-case content words, in order, without duplicates. Bound FTS terms only."""
    terms = [t for t in re.findall(r"[a-z0-9][a-z0-9\-]*", query.lower()) if t not in _STOP and len(t) > 1]
    return list(dict.fromkeys(terms))[:limit]


def split_paragraphs(text, low=250, high=1100):
    """Lossless partition of a chunk into paragraph-like [start, end) spans."""
    cuts = [0] + [m.end() for m in re.finditer(r'\n[ \t]*\n', text)] + [len(text)]
    merged = []
    for start, end in zip(cuts, cuts[1:]):
        if end <= start:
            continue
        if merged and merged[-1][1] - merged[-1][0] < low and end - merged[-1][0] <= high:
            merged[-1] = (merged[-1][0], end)
        else:
            merged.append((start, end))
    spans = []
    for start, end in merged:
        while end - start > high:
            window = text[start:start + high]
            breaks = list(re.finditer(r'(?<=[.;:])\s+|\n', window[high // 2:]))
            cut = start + high // 2 + breaks[-1].end() if breaks else start + high
            spans.append((start, cut))
            start = cut
        if end > start:
            spans.append((start, end))
    return spans


def paragraph_rows(corpus):
    """(chunk_id, start, end, title, heading, text) for every retrievable paragraph, in a stable order."""
    rows = corpus.connection.execute(
        'SELECT f.chunk_id, f.title, f.heading_path, f.text, c.document_id FROM chunks_fts f '
        'JOIN chunks c ON c.chunk_id=f.chunk_id ORDER BY f.chunk_id').fetchall()
    for chunk_id, title, heading, text, document_id in rows:
        if corpus.policy.get(document_id, {}).get('retrieval_allowed') is not True:
            continue
        for start, end in split_paragraphs(text):
            if text[start:end].strip():
                yield chunk_id, start, end, title, heading, text[start:end]


class ParagraphKeywords:
    """Stemmed BM25 over paragraphs. One shared in-memory table per corpus snapshot."""
    _shared, _guard = {}, threading.Lock()

    def __init__(self, corpus):
        snapshot = corpus.summary()['snapshot_sha256']
        with self._guard:
            if snapshot not in self._shared:
                con = sqlite3.connect(':memory:', check_same_thread=False)
                con.execute("CREATE VIRTUAL TABLE para USING fts5(chunk_id UNINDEXED, start UNINDEXED, "
                            "end UNINDEXED, title, heading, text, tokenize='porter unicode61')")
                con.executemany('INSERT INTO para VALUES(?,?,?,?,?,?)', paragraph_rows(corpus))
                con.commit()
                self._shared[snapshot] = (con, threading.Lock())
        self.connection, self.lock = self._shared[snapshot]

    def search(self, query, limit=30):
        terms = search_terms(query)
        if not terms:
            return []
        expression = ' OR '.join('"' + t.replace('"', '') + '"' for t in terms)
        with self.lock:
            rows = self.connection.execute(
                'SELECT chunk_id FROM para WHERE para MATCH ? ORDER BY bm25(para,0,0,0,0.5,0.5,1.0) LIMIT ?',
                (expression, limit * 6)).fetchall()
        return list(dict.fromkeys(r[0] for r in rows))[:limit]


class ParagraphVectors:
    """Read-only paragraph vectors; requires numpy. Shared across requests."""
    _shared, _guard = {}, threading.Lock()

    def __init__(self, path=PARAGRAPH_VECTORS):
        try:
            import numpy
        except ImportError as exc:
            raise HarnessError('Paragraph-vector retrieval needs numpy (pip install -r requirements-demo.txt)') from exc
        self.np, self.path = numpy, Path(path)
        if not self.path.is_file():
            raise HarnessError('Paragraph-vector index is not installed')
        key = str(self.path.resolve())
        with self._guard:
            if key not in self._shared:
                self._shared[key] = self._load()
        self.manifest, self.matrix, self.chunks = self._shared[key]
        self.documents = None

    def _load(self):
        np = self.np
        with sqlite3.connect(f'{self.path.resolve().as_uri()}?mode=ro', uri=True) as con:
            manifest = json.loads(con.execute('SELECT value FROM manifest').fetchone()[0])
            rows = con.execute('SELECT chunk_id, data FROM vectors ORDER BY rowid').fetchall()
        dims = manifest.get('dimensions')
        if (manifest.get('embedding_model_id') != MODEL or manifest.get('splitter') != SPLITTER_VERSION
                or type(dims) is not int or not 64 <= dims <= 1024 or manifest.get('vector_count') != len(rows)):
            raise HarnessError('Paragraph-vector index has an unexpected model, splitter, or size')
        blob = b''.join(r[1] for r in rows)
        if hashlib.sha256(blob).hexdigest() != manifest.get('content_sha256'):
            raise HarnessError('Paragraph-vector index failed its checksum')
        matrix = np.frombuffer(blob, dtype='<f2').reshape(len(rows), dims).astype('f4')
        matrix /= np.maximum(np.linalg.norm(matrix, axis=1, keepdims=True), 1e-9)
        return manifest, matrix, [r[0] for r in rows]

    def bind(self, chunk_document):
        """Attach the corpus's chunk -> document map so searches can be limited to documents."""
        self.documents = self.np.array([chunk_document.get(c, '') for c in self.chunks])
        return self

    def rank(self, query_vector, limit=30, documents=None):
        """Parent chunk ids ordered by their best paragraph."""
        np, dims = self.np, self.manifest['dimensions']
        query = np.asarray(query_vector, dtype='f4')[:dims]
        norm = float(np.linalg.norm(query))
        if not norm:
            raise HarnessError('A zero embedding cannot be used for cosine search')
        scores = self.matrix @ (query / norm)
        if documents is not None:
            if self.documents is None:
                raise HarnessError('Paragraph vectors are not bound to a corpus')
            scores = np.where(np.isin(self.documents, list(documents)), scores, -2.0)
        count = min(len(scores), limit * 5)
        top = np.argpartition(-scores, count - 1)[:count]
        found = []
        for index in top[np.argsort(-scores[top])]:
            if scores[index] <= -1:
                break
            if self.chunks[index] not in found:
                found.append(self.chunks[index])
                if len(found) == limit:
                    break
        return found
