"""Local hybrid retrieval over verified Titan vectors and canonical FTS text.

Document vectors are never regenerated. Query cache misses fail closed unless an
explicitly approved embedding adapter is supplied. FTS complements a successful
vector query; it never bypasses the approved embedding/cache boundary.
"""
from array import array
from contextlib import contextmanager, closing
import hashlib
import heapq
import json
import math
import re
from pathlib import Path
import sqlite3
import struct
import time

from .corpus import ApprovedCorpus, HarnessError, ROOT, base
from .policy import check_input
from .vector_cache import verify_cache

MODEL = 'amazon.titan-embed-text-v2:0'
DIMENSIONS = 1024
VECTOR_CACHE = ROOT / 'artifacts/aws-eval/vector-cache.sqlite'
QUERY_CACHE = ROOT / 'artifacts/query-embeddings.sqlite'


def vector_values(values):
    if not isinstance(values, (list, tuple, array)) or len(values) != DIMENSIONS:
        raise HarnessError('Titan query embedding must have 1,024 dimensions')
    if any(type(v) not in (int, float) or not math.isfinite(v) for v in values):
        raise HarnessError('Embedding contains invalid values')
    if not any(values):
        raise HarnessError('A zero embedding cannot be used for cosine search')
    try:
        data = struct.pack('<1024f', *values)
    except (OverflowError, struct.error) as exc:
        raise HarnessError('Embedding is outside the float32 range') from exc
    result = array('f', struct.unpack('<1024f', data))
    if not all(math.isfinite(v) for v in result) or not any(result):
        raise HarnessError('Embedding is not a finite nonzero float32 vector')
    return result


class CachedTitanQueries:
    """Only exact input text/model/settings matches can reuse a query vector."""
    def __init__(self, path=QUERY_CACHE, on_miss=None):
        self.path, self.on_miss = Path(path), on_miss
        self.last_usage = {}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as con:
            con.execute('''CREATE TABLE IF NOT EXISTS query_embeddings(
                query_sha256 TEXT NOT NULL,model_id TEXT NOT NULL,dimensions INTEGER NOT NULL,
                normalized INTEGER NOT NULL,data BLOB NOT NULL,data_sha256 TEXT NOT NULL,
                provenance TEXT NOT NULL,created_at REAL NOT NULL,
                PRIMARY KEY(query_sha256,model_id,dimensions,normalized))''')

    @contextmanager
    def connect(self):
        con = sqlite3.connect(self.path, timeout=120)
        try:
            with con:
                yield con
        finally:
            con.close()

    def embed(self, query):
        check_input(query, {})
        identity = (base.digest(query), MODEL, DIMENSIONS, 1)
        with self.connect() as con:
            # Serialize misses across processes so a question is not charged twice.
            con.execute('BEGIN IMMEDIATE')
            row = con.execute('SELECT data,data_sha256,provenance FROM query_embeddings '
                              'WHERE query_sha256=? AND model_id=? AND dimensions=? AND normalized=?', identity).fetchone()
            hit = row is not None
            if row:
                data, checksum, provenance = row
                if hashlib.sha256(data).hexdigest() != checksum or len(data) != DIMENSIONS * 4:
                    raise HarnessError('Cached query embedding checksum or size mismatch')
                values = vector_values(struct.unpack('<1024f', data))
            else:
                if self.on_miss is None:
                    raise HarnessError('This question has no cached Titan embedding. Explicit approval is required '
                                       'to generate it; no AWS call or keyword fallback was attempted.')
                values = vector_values(self.on_miss.embed(query))
                data = struct.pack('<1024f', *values)
                provenance = 'bedrock_titan_v2'
                con.execute('INSERT INTO query_embeddings VALUES(?,?,?,?,?,?,?,?)',
                            (*identity, data, hashlib.sha256(data).hexdigest(), provenance, time.time()))
        self.last_usage = {'query_sha256': identity[0], 'model_id': MODEL, 'dimensions': DIMENSIONS,
                           'normalized': True, 'cache_hit': hit, 'provenance': provenance}
        return values


class LocalVectorIndex:
    """Read-only document index, shared by UI turns; no third-party vector engine."""
    def __init__(self, path=VECTOR_CACHE):
        self.path = Path(path)
        self.manifest = verify_cache(self.path)
        if (self.manifest.get('embedding_model_id') != MODEL or self.manifest.get('dimensions') != DIMENSIONS
                or self.manifest.get('distance_metric') != 'cosine' or self.manifest.get('vector_count', 0) < 1):
            raise HarnessError('Local index must contain Titan V2 1,024-dimensional cosine vectors')
        self.rows, seen = [], set()
        with closing(sqlite3.connect(f'{self.path.resolve().as_uri()}?mode=ro', uri=True)) as con:
            for key, data, metadata in con.execute('SELECT key,data,metadata FROM vectors ORDER BY key'):
                meta = json.loads(metadata)
                chunk_id = meta.get('chunk_id')
                if not isinstance(chunk_id, str) or not chunk_id or chunk_id in seen:
                    raise HarnessError('Local vector index has a missing or duplicate canonical chunk identity')
                if meta.get('snapshot_sha256') != self.manifest['snapshot_sha256']:
                    raise HarnessError('Local vector metadata uses a different corpus snapshot')
                text = meta.get('AMAZON_BEDROCK_TEXT')
                if not isinstance(text, str) or not isinstance(meta.get('text_sha256'), str):
                    raise HarnessError('Local vector source text or canonical hash is missing')
                values = vector_values(struct.unpack('<1024f', data))
                norm = math.sqrt(sum(v * v for v in values))
                identity = {k: meta.get(k) for k in ('chunk_id', 'document_id', 'text_sha256', 'source_url')}
                # Bedrock removes outer whitespace from its stored text. Check it
                # against canonical text on resolution; quotations never use this
                # normalized copy and retain their original character offsets.
                identity['managed_text_sha256'] = base.digest(text.strip())
                self.rows.append((key, identity, values, norm))
                seen.add(chunk_id)

    def rank(self, query_vector, *, limit=30, allowed_documents=None):
        query = vector_values(query_vector)
        norm = math.sqrt(sum(v * v for v in query))
        def scores():
            for key, meta, values, length in self.rows:
                if allowed_documents is not None and meta['document_id'] not in allowed_documents:
                    continue
                similarity = sum(a * b for a, b in zip(query, values)) / (norm * length)
                similarity = max(-1.0, min(1.0, similarity))
                yield {'vector_key': key, **meta, 'cosine_similarity': similarity, 'distance': 1.0 - similarity}
        return heapq.nsmallest(limit, scores(), key=lambda row: (row['distance'], row['chunk_id']))


class LocalVectorCorpus(ApprovedCorpus):
    def __init__(self, db, registry, index, queries):
        super().__init__(db, registry)
        if index.manifest['snapshot_sha256'] != self.summary()['snapshot_sha256']:
            self.close()
            raise HarnessError('Local Titan index and canonical corpus snapshots differ')
        self.index, self.queries = index, queries

    def search(self, query, limit=4, include_indexes=False):
        if type(limit) is not int or not 1 <= limit <= 12 or include_indexes:
            raise HarnessError('Local vector search requires 1–12 results and excludes collection indexes')
        check_input(query, {})
        started = time.monotonic()
        vector = self.queries.embed(query)
        permitted = {doc_id for doc_id, policy in self.policy.items() if policy.get('retrieval_allowed') is True}
        ranked = self.index.rank(vector, limit=30, allowed_documents=permitted)
        found = []
        for hit in ranked:
            item = self.chunk(hit['chunk_id'])
            if any(hit[k] != item[k] for k in ('document_id', 'text_sha256', 'source_url')):
                raise HarnessError('Local vector identity differs from the canonical source')
            if base.digest(item['text'].strip()) != hit['managed_text_sha256']:
                raise HarnessError('Local managed vector text differs from the canonical source')
            item.update(retrieval_score=hit['cosine_similarity'], retrieval_score_kind='cosine_similarity',
                        vector_distance=hit['distance'])
            found.append(item)
        # Exact vocabulary complements embeddings, which can miss a rare service
        # acronym or retrieve a generic overview. This adds no external evidence.
        lexical = [item for item in base.Corpus.search(self, query, 30, False) if self.allowed(item)]
        plan = next((p for p in ('mltc', 'harp', 'map', 'pace') if re.search(r'\b' + p + r'\b', query, re.I)), None)
        lexical.sort(key=lambda item: item['retrieval_score'] *
                     (1.8 if plan and re.search(r'\b' + plan + r'\b', item.get('title') or '', re.I) else 1))
        lexical = lexical[:30]
        candidates = {item['chunk_id']: item for item in found}
        scores, contributions = {}, {}
        for channel, queue in (('vector', found), ('lexical', lexical)):
            for rank, item in enumerate(queue, 1):
                candidates.setdefault(item['chunk_id'], item)
                identity = item['chunk_id']
                contribution = 1 / (20 + rank)
                scores[identity] = scores.get(identity, 0) + contribution
                contributions.setdefault(identity, {})[channel + '_rank'] = rank
                contributions[identity][channel + '_rrf_contribution'] = contribution
        if plan:
            for item in candidates.values():
                if re.search(r'\b' + plan + r'\b', item.get('title') or '', re.I):
                    scores[item['chunk_id']] *= 1.5
        combined = sorted(candidates.values(), key=lambda item: (-scores[item['chunk_id']], item['chunk_id']))
        selected, seen = [], set()
        for item in combined:
            identity = item['chunk_id']
            if identity in seen:
                continue
            selected.append(item)
            seen.add(identity)
            if len(selected) == limit:
                break
        self.last_search = {'strategy': 'local_titan_fts_hybrid', 'semantic_retrieval': True,
            'embedding': dict(self.queries.last_usage), 'document_vectors_sha256': self.index.manifest['content_sha256'],
            'candidate_limit': 30, 'canonical_hits': len(candidates), 'returned_hits': len(selected),
            'ranking': 'reciprocal_rank_fusion_k20_then_chunk_id', 'plan_title_boost': plan,
            'fused_plan_title_multiplier': 1.5 if plan else 1.0,
            'lexical_candidates': [item['chunk_id'] for item in lexical],
            'selected_candidates': [item['chunk_id'] for item in selected],
            'candidate_contributions': [{'chunk_id': item['chunk_id'],
                'vector_rank': contributions[item['chunk_id']].get('vector_rank'),
                'lexical_rank': contributions[item['chunk_id']].get('lexical_rank'),
                'vector_rrf_contribution': contributions[item['chunk_id']].get('vector_rrf_contribution', 0),
                'lexical_rrf_contribution': contributions[item['chunk_id']].get('lexical_rrf_contribution', 0),
                'plan_title_multiplier': 1.5 if plan and re.search(r'\b' + plan + r'\b', item.get('title') or '', re.I) else 1.0,
                'fused_score': scores[item['chunk_id']]} for item in combined],
            'aws_ranking_equivalence': 'not_equivalent_hybrid_local_ranking',
            'seconds': round(time.monotonic() - started, 6),
            'candidates': [{k: hit[k] for k in ('chunk_id', 'document_id', 'cosine_similarity', 'distance')} for hit in ranked]}
        return selected

    def packet(self, question, scope, limit=4):
        packet = super().packet(question, scope, limit)
        packet['retrieval'].update(engine='local_titan_vectors', mode='hybrid', semantic_retrieval=True)
        packet['packet_id'] = base.packet_hash(packet)
        return packet


def cached_queries(path=QUERY_CACHE, *, approval=None, profile='oracle'):
    on_miss = None
    if approval is not None:
        from .aws import ApprovalGate, TitanQueryEmbedder
        from .budget import SpendLedger
        on_miss = TitanQueryEmbedder(ApprovalGate(approval), SpendLedger(ROOT / 'artifacts/aws-eval/budget.sqlite'),
                                    profile=profile)
    return CachedTitanQueries(path, on_miss)
