"""Offline CLI paths cannot obtain credentials or create AWS network clients."""
import argparse
import contextlib
import io
import json
from unittest.mock import patch

from support import Fixture, SCOPE, answer
from support import FixtureGraph
from oracle.__main__ import main, _models
from oracle.corpus import ROOT, base
from oracle.api_check import run_api_check
from oracle.harness import Harness


class CliTests(Fixture):
    def test_query_embedding_plan_and_missing_approval_never_create_sdk_clients(self):
        output = self.root / 'query-plan.json'
        with patch('oracle.aws.aws_client', side_effect=AssertionError('AWS forbidden')), \
             patch('socket.socket.connect', side_effect=AssertionError('network forbidden')):
            self.assertEqual(main(['cache-query-embeddings', '--output', str(output)]), 0)
            plan = json.loads(output.read_text())
            self.assertFalse(plan['executed'])
            self.assertEqual(plan['document_embedding_calls'], 0)
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(['cache-query-embeddings', '--execute']), 2)

    def args(self):
        return ['--db', str(self.db), '--registry', str(self.registry)]

    def test_evidence_only_runs_with_all_network_and_model_calls_blocked(self):
        output = self.root / 'packet.json'
        with patch('socket.socket.connect', side_effect=AssertionError('network forbidden')), \
             patch('oracle.aws.aws_client', side_effect=AssertionError('AWS forbidden')), \
             patch('oracle.providers.LocalModel.complete', side_effect=AssertionError('model forbidden')):
            status = main(self.args() + ['ask', 'Synthetic alpha', '--no-expansion', '--program', SCOPE['program'],
                '--plan-type', SCOPE['plan_type'], '--research-date', SCOPE['research_date'], '--evidence-only', '--output', str(output)])
        self.assertEqual(status, 0)
        self.assertEqual(json.loads(output.read_text())['status'], 'evidence_only')

    def test_ingestion_plan_is_free_and_execution_without_grant_is_blocked(self):
        output = self.root / 'ingestion.json'
        command = self.args() + ['start-ingestion', '--knowledge-base-id', 'KB12345678',
                   '--data-source-id', 'DS12345678', '--output', str(output)]
        with patch('oracle.aws.aws_client') as sdk:
            self.assertEqual(main(command), 0)
            self.assertFalse(json.loads(output.read_text())['executed'])
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(command + ['--execute', '--approval', str(self.root / 'missing.json')]), 2)
        sdk.assert_not_called()

    def test_upload_plan_never_creates_an_sdk_client(self):
        directory = self.root / 'export'
        directory.mkdir()
        (directory / 'upload-manifest.json').write_text(json.dumps({'files': [], 'file_count': 0, 'total_bytes': 0}))
        output = self.root / 'upload.json'
        with patch('oracle.aws.aws_client') as sdk:
            self.assertEqual(main(['upload-aws', '--directory', str(directory), '--bucket', 'offline-test', '--output', str(output)]), 0)
        sdk.assert_not_called()
        self.assertFalse(json.loads(output.read_text())['executed'])

    def test_actual_aws_adapter_simulation_uses_no_network_or_inference(self):
        graph = FixtureGraph(self.corpus)
        with patch('socket.socket.connect', side_effect=AssertionError('network forbidden')), \
             patch('oracle.aws.aws_client', side_effect=AssertionError('real AWS factory forbidden')):
            result = run_api_check(self.db, self.registry, graph, 'Synthetic alpha', SCOPE, 1)
        self.assertTrue(result['simulation']['enabled'])
        self.assertEqual(result['simulation']['aws_retrieve_calls'], 1)
        self.assertEqual(result['simulation']['aws_converse_calls'], 2)
        self.assertFalse(result['simulation']['actual_embeddings_generated'])
        self.assertFalse(result['simulation']['actual_llm_inference'])
        self.assertEqual(result['status'], 'currency_unverified')
        self.assertTrue(result['quotes'])
        self.assertFalse(graph.closed, 'run_api_check borrows its caller-owned graph')

    def test_local_evidence_does_not_depend_on_aws_configuration(self):
        output = self.root / 'local-evidence.json'
        with patch('oracle.aws.aws_client', side_effect=AssertionError('AWS forbidden')):
            status = main(self.args() + ['--aws-config', str(self.root / 'missing-config.json'),
                'ask', 'Synthetic alpha', '--no-expansion', '--program', SCOPE['program'],
                '--plan-type', SCOPE['plan_type'], '--research-date', SCOPE['research_date'],
                '--evidence-only', '--output', str(output)])
        self.assertEqual(status, 0)
        self.assertEqual(json.loads(output.read_text())['status'], 'evidence_only')

    def test_clarification_needs_no_database_graph_config_or_model(self):
        for command in ('ask', 'demo'):
            with self.subTest(command=command), \
                 patch('oracle.__main__.ApprovedCorpus', side_effect=AssertionError('corpus forbidden')), \
                 patch('oracle.__main__.GraphStore', side_effect=AssertionError('graph forbidden')), \
                 patch('oracle.__main__.LocalModel', side_effect=AssertionError('model forbidden')):
                output = self.root / (command + '-clarification.json')
                status = main(['--db', str(self.root / 'missing.sqlite'), '--graph', str(self.root / 'missing-graph.sqlite'),
                    '--aws-config', str(self.root / 'missing-config.json'), command, 'Synthetic alpha',
                    '--view', 'debug', '--format', 'json', '--output', str(output)])
                self.assertEqual(status, 0)
                value = json.loads(output.read_text())
                self.assertEqual(value['result']['status'], 'clarification_needed')
                self.assertFalse(value['result']['model_invoked'])

    def test_generator_and_reviewer_are_independently_configurable(self):
        config = base.load_json(ROOT / 'config/aws-demo.json')
        args = argparse.Namespace(provider='bedrock', model='generator-id', review_model='reviewer-id',
                                  model_approval=self.root / 'generator-grant.json', review_approval=self.root / 'review-grant.json')
        with patch('oracle.aws.aws_client') as sdk:
            model, reviewer = _models(args, config)
        sdk.assert_not_called()
        self.assertEqual(model.model_id, 'generator-id')
        self.assertEqual(reviewer.model_id, 'reviewer-id')
        self.assertIsNot(model, reviewer)

    def test_separate_reviewer_is_actually_used_by_harness(self):
        class Draft:
            model_id = 'draft'
            calls = 0
            def complete(self, system, payload, schema):
                self.calls += 1
                return answer(payload)
        class Review:
            model_id = 'review'
            calls = 0
            def complete(self, system, payload, schema):
                self.calls += 1
                return {'response': answer(payload), 'metadata_proxy_evidence_ids': [], 'additional_query': None,
                        'checks': {key: True for key in ['support','applicability','responsiveness','dates_and_versions','conflicts_and_exceptions','boundary_safe']}, 'issues': []}
        draft, review = Draft(), Review()
        result = Harness(self.corpus, draft, 1, reviewer=review).run('Synthetic alpha', SCOPE)
        self.assertEqual((draft.calls, review.calls), (1, 1))
        self.assertEqual(result['harness']['review_model'], 'review')
