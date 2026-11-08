"""Cost receipts and disposable-resource cleanup, with all SDK calls mocked."""
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from oracle.aws import BedrockModel
from oracle.aws_evaluation import MeteredClients, MODEL, MAX_OUTPUT, REGION, cleanup, trial_template, finish_ingestion, resume_ingestion, save, evaluate, converse_reservation
from oracle.budget import SpendLedger
from oracle.corpus import HarnessError


class EvaluationTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.patch_directory = patch('oracle.aws_evaluation.DIRECTORY', self.directory)
        self.patch_directory.start()
        self.addCleanup(self.patch_directory.stop)
        self.ledger = SpendLedger(self.directory / 'budget.sqlite')
        self.session, self.sdk = Mock(), Mock()
        self.session.client.return_value = self.sdk
        self.clients = MeteredClients(self.session, self.ledger,
            {'llm_input_per_million': 0.15, 'llm_output_per_million': 0.60})

    def test_invalid_answer_still_has_a_paid_token_receipt(self):
        self.sdk.converse.return_value = {'stopReason': 'end_turn', 'output': {'message': {
            'role': 'assistant', 'content': [{'text': 'invalid json'}]}},
            'usage': {'inputTokens': 10000, 'outputTokens': 1000}}
        model = BedrockModel(MODEL, REGION, Mock(), max_output_tokens=MAX_OUTPUT, client_factory=self.clients)
        with self.assertRaises(HarnessError):
            model.complete('policy', {}, {})
        self.assertEqual(self.clients.records[0]['calculated_cost_usd'], 0.0021)
        self.assertEqual(self.ledger.summary()['committed_upper_usd'], 0.0021)
        sent = self.sdk.converse.call_args.kwargs
        self.assertTrue(sent['messages'][0]['content'][0]['text'].endswith('/no_think'))

    def test_failed_sdk_call_is_not_retried_and_retains_its_upper_bound(self):
        self.sdk.converse.side_effect = RuntimeError('simulated failure')
        client = self.clients('bedrock-runtime', REGION)
        with self.assertRaises(RuntimeError):
            client.converse(modelId=MODEL, inferenceConfig={'maxTokens': MAX_OUTPUT})
        self.sdk.converse.assert_called_once()
        self.assertFalse(self.clients.records[0]['completed'])
        self.assertGreaterEqual(self.ledger.summary()['committed_upper_usd'], self.clients.records[0]['cost_upper_usd'])

    def test_missing_token_usage_keeps_reservation(self):
        self.sdk.converse.return_value = {'usage': {}}
        with self.assertRaises(HarnessError):
            self.clients('bedrock-runtime', REGION).converse(modelId=MODEL, inferenceConfig={'maxTokens': MAX_OUTPUT})
        self.assertGreaterEqual(self.ledger.summary()['committed_upper_usd'], self.clients.records[0]['cost_upper_usd'])

    def test_input_reservation_counts_utf8_bytes_and_both_schema_copies(self):
        request = {'modelId': MODEL, 'messages': [{'role': 'user', 'content': [{'text': '😀' * 40000}]}],
                   'system': [{'text': 'schema-in-prompt'}], 'outputConfig': {'schema': 'schema-in-api'},
                   'inferenceConfig': {'maxTokens': MAX_OUTPUT}}
        upper = converse_reservation(request, self.clients.prices)
        # More than 160k input bytes: never price this as a nominal 32K context.
        self.assertGreater(upper, Decimal('0.026'))

    def test_other_model_or_region_cannot_use_the_trial(self):
        with self.assertRaises(HarnessError):
            self.clients('bedrock-runtime', 'us-west-2')
        with self.assertRaises(HarnessError):
            self.clients('bedrock-runtime', REGION).converse(modelId='other', inferenceConfig={'maxTokens': MAX_OUTPUT})
        self.sdk.converse.assert_not_called()
        self.assertEqual(self.ledger.summary()['committed_upper_usd'], 0)

    def test_retrieval_cost_is_labeled_as_upper_bound_without_a_token_receipt(self):
        self.sdk.retrieve.return_value = {'retrievalResults': []}
        self.clients('bedrock-agent-runtime', REGION).retrieve(knowledgeBaseId='KB12345678')
        self.assertEqual(self.ledger.summary()['committed_upper_usd'], 0.002)
        self.assertNotIn('calculated_cost_usd', self.clients.records[0])

    def test_trial_template_has_no_retained_paid_resources(self):
        template = trial_template()
        for resource in template['Resources'].values():
            self.assertNotEqual(resource.get('DeletionPolicy'), 'Retain')
            self.assertNotEqual(resource.get('UpdateReplacePolicy'), 'Retain')
        self.assertEqual(template['Resources']['DataSource']['Properties']['DataDeletionPolicy'], 'RETAIN')
        self.assertEqual(template['Resources']['VectorIndex']['DeletionPolicy'], 'Delete')

    def test_cleanup_preserves_aws_copy_when_required_backup_is_missing(self):
        with self.assertRaises(HarnessError):
            cleanup(self.clients, {'stack_id': 'owned', 'archive_required': True})
        self.sdk.delete_objects.assert_not_called()
        self.sdk.delete_stack.assert_not_called()

    def test_cleanup_deletes_only_this_trial_manifest_before_its_stack(self):
        calls = []
        self.sdk.delete_objects.side_effect = lambda **kw: calls.append(('objects', kw)) or {}
        self.sdk.delete_stack.side_effect = lambda **kw: calls.append(('stack', kw)) or {}
        self.sdk.describe_stacks.side_effect = [
            {'Stacks': [{'StackStatus': 'CREATE_COMPLETE'}]}, {'Stacks': [{'StackStatus': 'DELETE_COMPLETE'}]}]
        state = {'stack_id': 'owned-trial', 'outputs': {'DocumentBucket': 'owned-bucket'},
                 'manifest': {'files': [{'name': 'passage.txt'}, {'name': 'passage.txt.metadata.json'}]}}
        cleanup(self.clients, state)
        self.assertEqual([call[0] for call in calls], ['objects', 'stack'])
        self.assertEqual(calls[0][1]['Bucket'], 'owned-bucket')
        self.assertEqual(calls[0][1]['Delete']['Objects'],
            [{'Key': 'corpus/passage.txt'}, {'Key': 'corpus/passage.txt.metadata.json'}])
        self.assertTrue(state['cleaned_up'])

    def test_continuing_ingestion_uses_the_same_job_without_starting_another(self):
        state = {'outputs': {'KnowledgeBaseId': 'KB12345678', 'DataSourceId': 'DS12345678'},
                 'ingestion_job_id': 'same-job'}
        self.sdk.get_ingestion_job.side_effect = [
            {'ingestionJob': {'status': 'IN_PROGRESS', 'statistics': {'numberOfNewDocumentsIndexed': 20}}},
            {'ingestionJob': {'status': 'COMPLETE', 'statistics': {'numberOfNewDocumentsIndexed': 40, 'numberOfDocumentsFailed': 0}}}]
        with patch('oracle.aws_evaluation.time.sleep'), patch('oracle.aws_evaluation.evaluate') as evaluate:
            finish_ingestion(self.clients, state, {})
        self.sdk.start_ingestion_job.assert_not_called()
        self.assertEqual(self.sdk.get_ingestion_job.call_count, 2)
        for call in self.sdk.get_ingestion_job.call_args_list:
            self.assertEqual(call.kwargs['ingestionJobId'], 'same-job')
        evaluate.assert_called_once()

    def test_resume_cannot_repeat_a_paid_query_even_if_its_report_was_not_saved(self):
        save(self.directory / 'state.json', {'ingestion_job_id': 'same-job', 'cleaned_up': False})
        save(self.directory / 'generation-approval.json', {'used_calls': 1})
        with patch('boto3.Session') as session, self.assertRaises(HarnessError):
            resume_ingestion('oracle', 'expected-account')
        session.assert_not_called()

    def test_continuing_evaluation_does_not_reset_grants_or_rerun_accepted_cases(self):
        save(self.directory / 'queries.json', [{'scenario': 1, 'status': 'partial', 'seconds': 3}])
        with patch('oracle.aws_evaluation.base.load_json', side_effect=[
                [{'scenario': 1, 'status': 'partial', 'seconds': 3}],
                {'scenarios': [{'id': 1, 'question': 'Already accepted'}]}]), \
                patch('oracle.aws_evaluation.grant') as grants, patch('oracle.aws_evaluation.Harness') as harness:
            rows = evaluate(self.clients, {'KnowledgeBaseId': 'same-kb'}, {}, continue_run=True)
        grants.assert_not_called()
        harness.assert_not_called()
        self.assertEqual(rows[0]['scenario'], 1)
        self.assertEqual(rows[0]['attempts'], 1)


if __name__ == '__main__':
    unittest.main()
