"""Lossless export/restore and refusal of incomplete or corrupted backups."""
from pathlib import Path
from tempfile import TemporaryDirectory
import sqlite3
import unittest
from unittest.mock import Mock

from oracle.budget import SpendLedger
from oracle.corpus import HarnessError
from oracle.vector_cache import export_vectors, restore_batches, verify_cache, remap_bedrock_metadata
import json


class VectorCacheTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'vectors.sqlite'
        self.ledger = SpendLedger(self.path.with_name('budget.sqlite'))
        self.sdk = Mock()

    def row(self, key):
        return {'key': key, 'data': {'float32': [0.125, -0.25] * 512},
                'metadata': {'AMAZON_BEDROCK_TEXT': 'Exact source text', 'source_id': 'original', 'flag': True}}

    def export(self, count=2):
        return export_vectors(self.sdk, 'owned-index', self.path, expected_count=count,
                              snapshot='pinned-snapshot', resources={'kb': 'original'}, ledger=self.ledger)

    def test_pagination_and_lossless_restore_preserve_all_metadata_and_values(self):
        self.sdk.list_vectors.side_effect = [{'vectors': [self.row('b')], 'nextToken': 'next'}, {'vectors': [self.row('a')]}]
        manifest = self.export()
        self.assertEqual(manifest['vector_count'], 2)
        self.assertEqual(list(restore_batches(self.path, 1)), [[self.row('a')], [self.row('b')]])
        self.assertEqual(self.sdk.list_vectors.call_args.kwargs['nextToken'], 'next')
        self.assertEqual(self.ledger.summary()['committed_upper_usd'], 0.1)
        self.export()
        self.assertEqual(self.sdk.list_vectors.call_count, 2)

    def test_incomplete_export_is_never_promoted_to_verified_cache(self):
        self.sdk.list_vectors.return_value = {'vectors': [self.row('a')]}
        with self.assertRaises(HarnessError):
            self.export()
        self.assertFalse(self.path.exists())

    def test_corrupted_vector_or_metadata_fails_before_restore(self):
        self.sdk.list_vectors.return_value = {'vectors': [self.row('a'), self.row('b')]}
        self.export()
        with sqlite3.connect(self.path) as con:
            con.execute("UPDATE vectors SET metadata='{}' WHERE key='a'")
        with self.assertRaises(HarnessError):
            list(restore_batches(self.path))

    def test_budget_is_reserved_before_the_first_export_request(self):
        self.ledger.reserve('earlier-work', 9.95)
        with self.assertRaises(HarnessError):
            self.export()
        self.sdk.list_vectors.assert_not_called()

    def test_resource_rebinding_changes_only_managed_references(self):
        vector = self.row('a')
        vector['metadata'].update({'x-amz-bedrock-kb-data-source-id': 'old-source',
            'AMAZON_BEDROCK_TEXT': 'Exact source text old-bucket', 'text_sha256': 'pinned',
            'AMAZON_BEDROCK_METADATA': json.dumps({'text': 'Exact source text old-bucket',
                'source': {'sourceLocation': 's3://old-bucket/corpus/chunk.txt'}})})
        restored = remap_bedrock_metadata(vector, {'DocumentBucket': 'old-bucket', 'DataSourceId': 'old-source'},
            {'DocumentBucket': 'new-bucket', 'DataSourceId': 'new-source'})['metadata']
        self.assertEqual(restored['AMAZON_BEDROCK_TEXT'], 'Exact source text old-bucket')
        self.assertEqual(restored['text_sha256'], 'pinned')
        nested = json.loads(restored['AMAZON_BEDROCK_METADATA'])
        self.assertEqual(nested['text'], 'Exact source text old-bucket')
        self.assertEqual(nested['source']['sourceLocation'], 's3://new-bucket/corpus/chunk.txt')

