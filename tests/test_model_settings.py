"""Settings/local-runtime safety with synthetic metadata and all sockets blocked."""
import io
import json
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from oracle.__main__ import _models, _parser
from oracle.budget import SpendLedger
from oracle.corpus import HarnessError
from oracle.local_vectors import CachedTitanQueries
from oracle.model_settings import CATALOG, DEFAULT, ModelSettings
from oracle.providers import LocalModel, discover_local, guard_local_model, server_url

GIB = 1024**3
ACCOUNT = '123456789012'
SCHEMA = {'type': 'object', 'properties': {'ok': {'type': 'boolean'}},
          'required': ['ok'], 'additionalProperties': False}


class ModelSettingsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.settings = ModelSettings(self.root / 'settings.json', self.root / 'queries.sqlite', self.root / 'budget.sqlite')
        # Even an accidentally unstubbed metadata/SDK operation cannot connect.
        self.sockets = patch('socket.socket.connect', side_effect=AssertionError('all sockets forbidden')).start()
        self.addCleanup(patch.stopall)

    def metadata(self, models=None):
        return {'models': models or [{'name': 'gemma3:4b', 'size': 3 * GIB}]}

    def local_patches(self, models=None):
        return (patch('oracle.providers.local_json', return_value=self.metadata(models)),
                patch('oracle.providers.physical_memory', return_value=36 * GIB),
                patch('oracle.providers.sys.platform', 'linux'))

    def authorize(self, allowance='.01', **changes):
        payload = {**DEFAULT, 'authorize_aws': True, 'confirmed_account': ACCOUNT,
                   'allowance_usd': allowance, **changes}
        with patch('oracle.model_settings.check_aws', return_value={'account': ACCOUNT, 'models': CATALOG['models']}):
            return self.settings.save(payload)

    def test_metadata_discovery_never_loads_downloads_or_infers(self):
        cases = [('ollama', {'models': [{'name': 'small', 'size': GIB}]}, '/api/tags'),
                 ('lmstudio', {'models': [{'key': 'small', 'type': 'llm', 'size_bytes': GIB}]}, '/api/v1/models'),
                 ('openai-local', {'data': [{'id': 'small'}]}, '/v1/models')]
        for provider, body, path in cases:
            with self.subTest(provider=provider), patch('oracle.providers.local_json', return_value=body) as read, \
                 patch('oracle.providers.physical_memory', return_value=36 * GIB):
                result = discover_local(provider, 'http://127.0.0.1:9999')
                self.assertEqual(result['models'][0]['id'], 'small')
                read.assert_called_once_with('http://127.0.0.1:9999' + path)
        self.sockets.assert_not_called()

    def test_known_large_model_blocked_even_with_acknowledgment(self):
        a,b,c = self.local_patches([{'name': 'qwen3.6:latest', 'size': int(30.8 * GIB)}])
        with a,b,c:
            for acknowledge in (False, True):
                with self.assertRaisesRegex(HarnessError, 'Too large'):
                    guard_local_model('ollama', DEFAULT['local_url'], 'qwen3.6:latest',
                                      size_acknowledged=acknowledge)

    def test_malformed_metadata_has_a_clear_error_without_other_requests(self):
        for provider, body in [('lmstudio', {'models': ['not an object']}),
                               ('ollama', {'models': [{'name': True}]}),
                               ('openai-local', {'data': None})]:
            with self.subTest(provider=provider), patch('oracle.providers.local_json', return_value=body) as read:
                with self.assertRaisesRegex(HarnessError, 'Could not list local models'):
                    discover_local(provider, DEFAULT['local_url'])
                self.assertEqual(read.call_count, 1)

    def test_invalid_metadata_is_unknown_and_requires_explicit_acknowledgment(self):
        for size in (None, 'unknown', '4GB', -1, 0, True, float('nan'), float('inf'), {'bytes': GIB}):
            with self.subTest(size=size):
                a,b,c = self.local_patches([{'name': 'small', 'size': size}])
                with a,b,c:
                    row = discover_local('ollama', DEFAULT['local_url'])['models'][0]
                    self.assertIsNone(row['size_bytes'])
                    with self.assertRaisesRegex(HarnessError, 'Confirm'):
                        guard_local_model('ollama', DEFAULT['local_url'], 'small')
                    guard_local_model('ollama', DEFAULT['local_url'], 'small', size_acknowledged=True)

    def test_unknown_physical_memory_uses_eight_gib_fallback(self):
        with patch('oracle.providers.local_json', return_value=self.metadata([{'name': 'large', 'size': 9 * GIB}])), \
             patch('oracle.providers.physical_memory', return_value=None):
            with self.assertRaisesRegex(HarnessError, 'Too large'):
                guard_local_model('ollama', DEFAULT['local_url'], 'large')

    def test_not_installed_is_an_error_without_fallback_or_download(self):
        a,b,c = self.local_patches()
        with a,b,c, self.assertRaisesRegex(HarnessError, 'installed model'):
            guard_local_model('ollama', DEFAULT['local_url'], 'not-installed')

    def test_macos_pressure_probe_blocks_low_unparseable_or_unavailable_memory(self):
        with patch('oracle.providers.local_json', return_value=self.metadata()), \
             patch('oracle.providers.physical_memory', return_value=36 * GIB), \
             patch('oracle.providers.sys.platform', 'darwin'), patch('oracle.providers.subprocess.run') as probe:
            for stdout in (b'System-wide memory free percentage: 24%', b'unrecognized output'):
                probe.return_value = SimpleNamespace(stdout=stdout)
                with self.assertRaises(HarnessError):
                    guard_local_model('ollama', DEFAULT['local_url'], 'gemma3:4b', inference=True)
            probe.side_effect = subprocess.TimeoutExpired('memory_pressure', 2)
            with self.assertRaisesRegex(HarnessError, 'Could not verify'):
                guard_local_model('ollama', DEFAULT['local_url'], 'gemma3:4b', inference=True)
            probe.side_effect = None
            probe.return_value = SimpleNamespace(stdout=b'System-wide memory free percentage: 25%')
            guard_local_model('ollama', DEFAULT['local_url'], 'gemma3:4b', inference=True)

    def test_remote_or_malformed_server_urls_fail_before_metadata(self):
        with patch('oracle.providers.local_json') as read:
            for url in (None, 12, 'https://external.example', 'http://localhost:11434?key=x',
                        'http://user:password@localhost', 'http://localhost/api/chat', 'http://['):
                with self.subTest(url=url), self.assertRaises(HarnessError):
                    server_url(url)
            read.assert_not_called()

    def test_cli_default_is_small_installed_model_with_strict_request_limits(self):
        args = _parser().parse_args(['demo', '--case', '1'])
        a,b,c = self.local_patches()
        with a,b,c, patch('oracle.__main__.LocalModel') as model:
            generator, reviewer = _models(args, {})
        self.assertIs(generator, reviewer)
        self.assertEqual(model.call_args.args[0], 'gemma3:4b')
        self.assertEqual(model.call_args.kwargs, {'context_tokens': 32768, 'strict_context': True,
                                                'max_output_tokens': 2048, 'keep_alive': 0})

    def test_cli_explicit_oversized_generator_or_reviewer_never_constructs_model(self):
        models = [{'name': 'gemma3:4b', 'size': 3 * GIB}, {'name': 'qwen3.6:latest', 'size': int(30.8 * GIB)}]
        for option in ('--model', '--review-model'):
            args = _parser().parse_args(['demo', option, 'qwen3.6:latest', '--acknowledge-unknown-model-size'])
            a,b,c = self.local_patches(models)
            with self.subTest(option=option), a,b,c, patch('oracle.__main__.LocalModel') as model:
                with self.assertRaisesRegex(HarnessError, 'Too large'):
                    _models(args, {})
                model.assert_not_called()

    def test_cli_unknown_size_needs_explicit_flag(self):
        args = _parser().parse_args(['demo', '--model', 'custom'])
        a,b,c = self.local_patches([{'name': 'custom'}])
        with a,b,c, patch('oracle.__main__.LocalModel'):
            with self.assertRaisesRegex(HarnessError, 'Confirm'):
                _models(args, {})
            args.acknowledge_unknown_model_size = True
            _models(args, {})

    def test_settings_and_cli_share_the_same_guard(self):
        a,b,c = self.local_patches([{'name': 'large', 'size': 31 * GIB}])
        with a,b,c:
            with self.assertRaisesRegex(HarnessError, 'Too large'):
                self.settings.save({**DEFAULT, 'provider': 'ollama', 'local_model': 'large',
                                    'local_size_acknowledged': True})
        self.assertFalse(self.settings.path.exists())

    def test_oversized_context_fails_before_any_inference_request(self):
        model = LocalModel('small', context_tokens=8192, strict_context=True)
        with patch('oracle.providers.urllib.request.build_opener') as opener:
            with self.assertRaisesRegex(HarnessError, 'context budget'):
                model.complete('S' * 9000, {}, SCHEMA)
            opener.assert_not_called()
        self.assertEqual(model.calls, 0)

    def test_no_aws_consent_blocks_bedrock_and_uncached_query(self):
        with patch('boto3.Session', side_effect=AssertionError('AWS session forbidden')):
            with self.assertRaisesRegex(HarnessError, 'explicitly enable'):
                self.settings.model()
            with self.assertRaisesRegex(HarnessError, 'Explicit approval'):
                self.settings.queries().embed('Synthetic research')

    def test_cached_query_works_without_aws_consent(self):
        embedder = Mock()
        embedder.embed.return_value = [1.0] + [0.0] * 1023
        CachedTitanQueries(self.settings.query_cache, embedder).embed('Synthetic research')
        with patch('boto3.Session', side_effect=AssertionError('AWS session forbidden')):
            cache = self.settings.queries()
            cache.embed('Synthetic research')
            self.assertTrue(cache.last_usage['cache_hit'])

    def test_account_mismatch_or_missing_confirmation_creates_no_grants(self):
        for account in (None, '000000000000'):
            with patch('oracle.model_settings.check_aws', return_value={'account': ACCOUNT, 'models': CATALOG['models']}):
                with self.assertRaisesRegex(HarnessError, 'explicitly authorize that account'):
                    self.settings.save({**DEFAULT, 'authorize_aws': True, 'confirmed_account': account})
        self.assertFalse((self.root / 'grants').exists())

    def test_invalid_allowances_fail_before_aws_metadata(self):
        with patch('oracle.model_settings.check_aws') as check:
            for allowance in ('not money', None, [], 'NaN', 'Infinity', '-1', '0', '10.01'):
                with self.subTest(allowance=allowance), self.assertRaises(HarnessError):
                    self.settings.save({**DEFAULT, 'authorize_aws': True, 'allowance_usd': allowance})
            check.assert_not_called()

    def test_allowance_cannot_exceed_persistent_global_remainder(self):
        ledger = SpendLedger(self.settings.ledger_path)
        ledger.reserve('existing-approved-spend', '9.99')
        with self.assertRaisesRegex(HarnessError, 'remaining strict'):
            self.authorize('.02')
        self.assertFalse((self.root / 'grants').exists())

    def test_generation_and_embeddings_share_stage_without_real_calls(self):
        self.authorize()
        sdk = Mock()
        sdk.converse.return_value = {'stopReason': 'end_turn', 'output': {'message': {
            'role': 'assistant', 'content': [{'text': '{"ok":true}'}]}},
            'usage': {'inputTokens': 10, 'outputTokens': 10, 'totalTokens': 20}}
        sdk.invoke_model.return_value = {'body': io.BytesIO(json.dumps({
            'embedding': [1.0] + [0.0] * 1023, 'inputTextTokenCount': 3}).encode())}
        with patch('oracle.model_settings.AccountSession') as model_session, patch('oracle.aws.AccountSession') as query_session:
            model_session.return_value.client.return_value = sdk
            query_session.return_value.client.return_value = sdk
            self.assertEqual(self.settings.model().complete('Policy', {}, SCHEMA), {'ok': True})
            queries = self.settings.queries()
            queries.embed('Synthetic research')
            queries.embed('Synthetic research')
            self.assertTrue(queries.last_usage['cache_hit'])
        self.assertEqual(sdk.converse.call_count, 1)
        self.assertEqual(sdk.invoke_model.call_count, 1)
        summary = SpendLedger(self.settings.ledger_path).summary()
        self.assertEqual(summary['cap_usd'], 10)
        self.assertEqual(len(summary['charges']), 2)
        stages = {r['details']['approval_stage'] for r in summary['charges']}
        self.assertEqual(stages, {'demo-' + self.settings.value['authorization']['id']})
        resumed = ModelSettings(self.settings.path, self.settings.query_cache, self.settings.ledger_path)
        _, ledger = resumed.authorized()
        self.assertEqual(ledger.summary()['committed_upper_usd'], summary['committed_upper_usd'])
        self.assertEqual(ledger.summary()['stage_limits'][0]['allowance_usd'], .01)

    def test_disable_revokes_already_constructed_paid_adapters(self):
        self.authorize()
        model, queries = self.settings.model(), self.settings.queries()
        self.settings.disable()
        self.assertFalse(self.settings.public()['aws_enabled'])
        with patch('boto3.Session', side_effect=AssertionError('AWS session forbidden')):
            with self.assertRaisesRegex(HarnessError, 'scope, expiry'):
                model.complete('Policy', {}, SCHEMA)
            with self.assertRaisesRegex(HarnessError, 'scope, expiry'):
                queries.embed('New synthetic question')

    def test_local_consent_creates_query_only_grant_and_cannot_gain_generation(self):
        a,b,c = self.local_patches()
        with a,b,c:
            public = self.authorize(provider='ollama', local_model='gemma3:4b')
        auth = self.settings.value['authorization']
        identifier = auth['id']
        self.assertIsNone(auth['generation_model_id'])
        self.assertIsNone(public['authorization']['generation_model_id'])
        self.assertTrue(self.settings.grant_path(identifier, 'query').is_file())
        self.assertFalse(self.settings.grant_path(identifier, 'model').exists())
        with patch('oracle.model_settings.check_aws', side_effect=AssertionError('no new consent given')):
            self.settings.save({**self.settings.value, 'provider': 'bedrock', 'authorize_aws': False})
        self.assertEqual(self.settings.value['authorization']['id'], identifier)
        with patch('oracle.model_settings.AccountSession', side_effect=AssertionError('AWS client forbidden')):
            with self.assertRaisesRegex(HarnessError, 'explicitly authorize the selected Bedrock model'):
                self.settings.model()
        self.assertFalse(self.settings.grant_path(identifier, 'model').exists())
        # Unapproved generation does not revoke the already approved query capability.
        self.assertIsNotNone(self.settings.queries().on_miss)

    def test_local_only_consent_can_be_disabled_without_model_grant(self):
        a,b,c = self.local_patches()
        with a,b,c:
            self.authorize(provider='ollama', local_model='gemma3:4b')
        auth = self.settings.value['authorization']
        self.settings.disable()
        self.assertIsNone(self.settings.value['authorization'])
        self.assertFalse(self.settings.public()['aws_enabled'])
        self.assertFalse(self.settings.grant_path(auth['id'], 'model').exists())
        grant = json.loads(self.settings.grant_path(auth['id'], 'query').read_text())
        self.assertLessEqual(datetime.fromisoformat(grant['expires_at']), datetime.now(timezone.utc))

    def test_bedrock_consent_survives_local_switch_only_for_same_approved_model(self):
        self.authorize()
        auth = dict(self.settings.value['authorization'])
        self.assertEqual(auth['generation_model_id'], DEFAULT['bedrock_model'])
        a,b,c = self.local_patches()
        with a,b,c:
            self.settings.save({**self.settings.value, 'provider': 'ollama', 'local_model': 'gemma3:4b'})
        self.settings.save({**self.settings.value, 'provider': 'bedrock'})
        self.assertEqual(self.settings.value['authorization'], auth)
        with patch('oracle.model_settings.AccountSession') as session:
            model = self.settings.model()
            self.assertEqual(model.model_id, DEFAULT['bedrock_model'])
            session.assert_called_once_with(DEFAULT['aws_profile'], ACCOUNT)
        # A different priced model still needs a new explicit approval.
        self.settings.save({**self.settings.value, 'bedrock_model': 'amazon.nova-lite-v1:0'})
        with self.assertRaisesRegex(HarnessError, 'explicitly enable paid calls'):
            self.settings.model()

    def test_legacy_unbound_grant_cannot_authorize_bedrock_generation(self):
        self.authorize()
        self.settings.value['authorization'].pop('generation_model_id')
        with patch('oracle.model_settings.AccountSession', side_effect=AssertionError('AWS client forbidden')):
            with self.assertRaisesRegex(HarnessError, 'query-embedding consent alone'):
                self.settings.model()

    def test_explicit_bedrock_consent_replaces_query_only_approval_with_bound_model(self):
        a,b,c = self.local_patches()
        with a,b,c:
            self.authorize(provider='ollama', local_model='gemma3:4b')
        old_id = self.settings.value['authorization']['id']
        self.authorize()
        auth = self.settings.value['authorization']
        self.assertNotEqual(old_id, auth['id'])
        self.assertEqual(auth['generation_model_id'], DEFAULT['bedrock_model'])
        grant = json.loads(self.settings.grant_path(auth['id'], 'model').read_text())
        self.assertEqual(grant['resource']['model_id'], DEFAULT['bedrock_model'])


if __name__ == '__main__':
    unittest.main()
