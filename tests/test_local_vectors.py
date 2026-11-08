"""Real cosine/canonical pipeline with synthetic Titan-format vectors, no network."""
import io
import json
import sqlite3
from unittest.mock import Mock, patch

from support import Fixture, FixtureGraph, SCOPE, ScriptedModel
from oracle.aws import TitanQueryEmbedder, AccountSession
from oracle.budget import SpendLedger
from oracle.context import ExpandedCorpus
from oracle.corpus import HarnessError
from oracle.harness import Harness
from oracle.local_vectors import CachedTitanQueries, LocalVectorIndex, LocalVectorCorpus
from oracle.vector_cache import export_vectors
from oracle.web import LocalApp


def direction(x, y):
    return [x, y] + [0.0] * 1022


class VectorFixture(Fixture):
    def setUp(self):
        super().setUp()
        self.vectors = self.root / 'vectors.sqlite'
        self.query_path = self.root / 'queries.sqlite'
        self.ledger = SpendLedger(self.root / 'budget.sqlite')
        records = []
        for n, values in enumerate((direction(1, 0), direction(0, 1))):
            item = self.corpus.chunk(f'chunk{n}')
            metadata = {key: item[key] for key in ('chunk_id', 'document_id', 'source_url', 'text_sha256')}
            metadata.update(snapshot_sha256=self.corpus.summary()['snapshot_sha256'], AMAZON_BEDROCK_TEXT=item['text'].strip())
            records.append({'key': f'vector{n}', 'data': {'float32': values}, 'metadata': metadata})
        sdk = Mock()
        sdk.list_vectors.return_value = {'vectors': records}
        export_vectors(sdk, 'synthetic', self.vectors, expected_count=2,
                       snapshot=self.corpus.summary()['snapshot_sha256'], resources={}, ledger=self.ledger)
        self.index = LocalVectorIndex(self.vectors)

    def queries(self, vector=None):
        embedder = Mock()
        embedder.embed.return_value = vector or direction(.6, .8)
        return CachedTitanQueries(self.query_path, embedder), embedder


class LocalVectorTests(VectorFixture):
    def test_paid_session_is_lazy_and_checks_account_before_runtime_client(self):
        with patch('boto3.Session') as factory:
            session = AccountSession('oracle', '123456789012')
            factory.assert_not_called()
            factory.return_value.client.return_value.get_caller_identity.return_value = {'Account': '000000000000'}
            with self.assertRaisesRegex(HarnessError, 'account differs'):
                session.client('bedrock-runtime', region_name='us-east-1')
            self.assertEqual(factory.return_value.client.call_count, 1)
            self.assertEqual(factory.return_value.client.call_args.args[0], 'sts')
    def test_exact_cosine_ranking_and_stable_ties(self):
        hits = self.index.rank(direction(.6, .8), limit=2)
        self.assertEqual([row['chunk_id'] for row in hits], ['chunk1', 'chunk0'])
        self.assertAlmostEqual(hits[0]['distance'], .2, places=6)
        self.assertAlmostEqual(hits[1]['cosine_similarity'], .6, places=6)
        ties = self.index.rank(direction(1, 1), limit=2)
        self.assertEqual([row['chunk_id'] for row in ties], ['chunk0', 'chunk1'])

    def test_query_cache_survives_restart_and_never_calls_aws_on_hit_or_unapproved_miss(self):
        cache, generator = self.queries()
        cache.embed('Synthetic alpha')
        generator.embed.assert_called_once()
        cached = CachedTitanQueries(self.query_path)
        with patch('socket.socket.connect', side_effect=AssertionError('network forbidden')):
            cached.embed('Synthetic alpha')
            self.assertTrue(cached.last_usage['cache_hit'])
            with self.assertRaisesRegex(HarnessError, 'Explicit approval'):
                cached.embed('A different question')
        with sqlite3.connect(self.query_path) as con:
            con.execute("UPDATE query_embeddings SET data_sha256='tampered'")
        with self.assertRaisesRegex(HarnessError, 'checksum'):
            cached.embed('Synthetic alpha')

    def test_wrong_query_settings_are_not_reused(self):
        cache, _ = self.queries()
        cache.embed('Synthetic alpha')
        with sqlite3.connect(self.query_path) as con:
            con.execute("UPDATE query_embeddings SET model_id='incompatible-model'")
        with self.assertRaisesRegex(HarnessError, 'Explicit approval'):
            CachedTitanQueries(self.query_path).embed('Synthetic alpha')

    def test_invalid_or_zero_vectors_fail_before_search(self):
        for values in ([1.0], direction(0, 0), direction(float('nan'), 0), direction(float('inf'), 0)):
            with self.subTest(values=values[:2]), self.assertRaises(HarnessError):
                self.index.rank(values)

    def test_canonical_sources_and_registry_filter_apply_before_top_k(self):
        cache, _ = self.queries()
        corpus = LocalVectorCorpus(self.db, self.registry, self.index, cache)
        self.addCleanup(corpus.close)
        corpus.policy['doc1']['retrieval_allowed'] = False
        hits = corpus.search('Synthetic alpha', 1)
        self.assertEqual(hits[0]['chunk_id'], 'chunk0')
        self.assertEqual(corpus.last_search['canonical_hits'], 1)
        self.assertTrue(corpus.last_search['semantic_retrieval'])
        self.index.rows[0][1]['text_sha256'] = 'wrong'
        with self.assertRaisesRegex(HarnessError, 'canonical source'):
            corpus.search('Synthetic alpha', 1)

    def test_stale_snapshot_is_rejected_before_query_embedding(self):
        cache, generator = self.queries()
        self.index.manifest['snapshot_sha256'] = 'different'
        with self.assertRaisesRegex(HarnessError, 'snapshots differ'):
            LocalVectorCorpus(self.db, self.registry, self.index, cache)
        generator.embed.assert_not_called()

    def test_complete_local_harness_uses_cached_titan_vectors_and_exact_quotes(self):
        cache, _ = self.queries()
        cache.embed('Synthetic alpha')
        corpus = LocalVectorCorpus(self.db, self.registry, self.index, CachedTitanQueries(self.query_path))
        self.addCleanup(corpus.close)
        expanded = ExpandedCorpus(corpus, FixtureGraph(corpus))
        with patch('socket.socket.connect', side_effect=AssertionError('network forbidden')):
            result = Harness(expanded, ScriptedModel(), limit=1).run('Synthetic alpha', SCOPE)
        self.assertEqual(result['harness']['model_calls'], 2)
        self.assertEqual(result['quotes'][0]['text'], expanded.chunk(result['quotes'][0]['chunk_id'])['text'])
        packet = expanded.packet('Synthetic alpha', SCOPE, 1)
        self.assertEqual(packet['retrieval']['engine'], 'local_titan_vectors')
        self.assertTrue(packet['retrieval']['search_plan']['embedding']['cache_hit'])

    def test_web_cache_miss_does_not_invoke_a_model_or_aws(self):
        model = Mock()
        model.complete.side_effect = AssertionError('model forbidden')
        factory = Mock(return_value=model)
        app = LocalApp(self.db, self.registry, self.root / 'unused-graph', self.root / 'history.sqlite',
                       retriever='local-vector', vector_cache=self.vectors, query_cache=self.query_path, model_factory=factory,
                       budget_path=self.root / 'budget.sqlite')
        with patch('oracle.web.GraphStore', side_effect=lambda *args: FixtureGraph(self.corpus)), \
             patch('socket.socket.connect', side_effect=AssertionError('network forbidden')):
            identity = app.add_run('Synthetic alpha', 'Synthetic alpha', SCOPE)
            app.lock.acquire()
            app.work(identity, 'Synthetic alpha', SCOPE)
        self.assertEqual(app.row(identity)['status'], 'failed')
        self.assertIn('Explicit approval', app.row(identity)['error'])
        model.complete.assert_not_called()

    def titan(self, fake=None, **resource):
        gate = self.grant('bedrock:InvokeModel', {**TitanQueryEmbedder.RESOURCE, **resource})
        client = fake or Mock()
        return TitanQueryEmbedder(gate, self.ledger, client_factory=Mock(return_value=client)), client

    def test_titan_sdk_request_shape_receipt_and_cache_reuse(self):
        embedder, client = self.titan()
        client.invoke_model.return_value = {'body': io.BytesIO(json.dumps({
            'embedding': direction(.6, .8), 'inputTextTokenCount': 12}).encode())}
        cache = CachedTitanQueries(self.query_path, embedder)
        cache.embed('Synthetic alpha')
        cache.embed('Synthetic alpha')
        request = client.invoke_model.call_args.kwargs
        self.assertEqual(request['modelId'], 'amazon.titan-embed-text-v2:0')
        self.assertEqual(json.loads(request['body']), {'inputText': 'Synthetic alpha', 'dimensions': 1024,
            'normalize': True, 'embeddingTypes': ['float']})
        self.assertEqual(client.invoke_model.call_count, 1)
        charges = [c for c in self.ledger.summary()['charges'] if c['category'] == 'titan-query-embedding']
        self.assertEqual(charges[0]['details']['input_tokens'], 12)
        self.assertEqual(charges[0]['status'], 'calculated')

    def test_titan_requires_exact_grant_and_budget_before_client_creation(self):
        embedder, _ = self.titan(dimensions=512)
        with self.assertRaises(HarnessError):
            embedder.embed('Synthetic alpha')
        embedder.client_factory.assert_not_called()
        embedder, _ = self.titan()
        self.ledger.use_stage('approved-local-query-embeddings', '0.01', isolated=True)
        self.ledger.reserve('exhausted', '0.01')
        with self.assertRaisesRegex(HarnessError, 'BUDGET STOP'):
            embedder.embed('Synthetic alpha')
        embedder.client_factory.assert_not_called()

    def test_invalid_embedding_still_preserves_paid_usage_receipt(self):
        embedder, client = self.titan()
        client.invoke_model.return_value = {'body': io.BytesIO(json.dumps({
            'embedding': [0.0], 'inputTextTokenCount': 12}).encode())}
        cache = CachedTitanQueries(self.query_path, embedder)
        with self.assertRaises(HarnessError):
            cache.embed('Synthetic alpha')
        self.assertTrue(any(c['status'] == 'calculated' for c in self.ledger.summary()['charges']))
        with sqlite3.connect(self.query_path) as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM query_embeddings').fetchone()[0], 0)
