"""Replay actual boundary classifications and verify the real harness gates offline."""
import json
from pathlib import Path
from unittest.mock import patch

from oracle.corpus import HarnessError
from oracle.harness import Harness
from oracle.output import DebugTrace, demo_result, demo_markdown
from support import Fixture, SCOPE, ScriptedModel, RoutedModel

CASES = {row['id']: row for row in json.loads(
    (Path(__file__).parent / 'fixtures/request_routing.json').read_text())['cases']}


class RequestRoutingTests(Fixture):
    def test_original_negative_requests_never_retrieve_or_make_source_claims(self):
        for identity in range(16, 21):
            case = CASES[identity]
            model, trace = RoutedModel(case), DebugTrace()
            with self.subTest(case=identity), patch.object(self.corpus, 'packet') as retrieve, \
                 patch('socket.socket.connect', side_effect=AssertionError('network forbidden')):
                result = Harness(self.corpus, model, trace=trace, request_guard=True).run(case['question'], case['scope'])
            retrieve.assert_not_called()
            self.assertEqual(model.calls, 2)
            self.assertEqual(result['harness']['searches'], 0)
            self.assertEqual(result['harness']['model_calls'], 2)
            self.assertEqual(result['findings'], [])
            self.assertEqual(result['quotes'], [])
            self.assertTrue(demo_result(result)['request_boundary_only'])
            self.assertIn('no source search', demo_markdown(result).lower())
            text = ' '.join(item['description'] for item in result['uncertainties'])
            expected = {16: 'cannot guarantee', 17: 'does not calculate', 18: 'allegation remains unverified',
                        19: 'Missing authority', 20: 'Do not provide'}
            self.assertIn(expected[identity], text)
            if identity == 17:
                self.assertEqual(result['status'], 'clarification_needed')
                self.assertEqual(len(result['clarifying_questions']), 3)
            self.assertFalse(trace.evidence)

    def mixed(self, research=None):
        return Harness(self.corpus, RoutedModel(CASES[222], research), request_guard=True)

    def test_mixed_request_researches_rules_and_preserves_private_access_limit(self):
        model, trace = RoutedModel(CASES[222]), DebugTrace()
        result = Harness(self.corpus, model, trace=trace, request_guard=True).run('Synthetic alpha', SCOPE)
        self.assertEqual(result['harness']['model_calls'], 4)
        self.assertEqual(result['harness']['searches'], 1)
        self.assertEqual(result['status'], 'partial')
        self.assertTrue(result['quotes'])
        self.assertIn('cannot access private', str(result['uncertainties']))
        self.assertFalse(result.get('request_boundary_only'))
        self.assertEqual([event['role'] for event in trace.events if event['stage'] == 'model_started'],
                         ['request_classification', 'public_research_check', 'draft', 'review'])

    def test_existing_supported_issues_and_support_gates_cannot_be_downgraded_away(self):
        for failure in ('issues', 'support', 'applicability', 'responsiveness'):
            research = ScriptedModel()
            original = research.complete
            def complete(system, payload, schema):
                value = original(system, payload, schema)
                if 'checks' in value:
                    if failure == 'issues':
                        value['issues'] = ['An unresolved synthetic problem remains.']
                    else:
                        value['checks'][failure] = False
                return value
            research.complete = complete
            with self.subTest(failure=failure), self.assertRaises(HarnessError):
                self.mixed(research).run('Synthetic alpha', SCOPE)

    def test_invalid_draft_and_false_boundary_review_remain_rejected(self):
        for failure in ('quote', 'boundary'):
            research = ScriptedModel()
            original = research.complete
            def complete(system, payload, schema):
                value = original(system, payload, schema)
                if failure == 'quote' and research.calls == 1:
                    value['quotes'] = ['E999.Q1']
                if failure == 'boundary' and 'checks' in value:
                    value['checks']['boundary_safe'] = False
                return value
            research.complete = complete
            with self.subTest(failure=failure), self.assertRaises(HarnessError):
                self.mixed(research).run('Synthetic alpha', SCOPE)

    def test_additional_search_is_still_bounded_with_five_total_calls(self):
        research = ScriptedModel(search=True, conflict=True)
        result = self.mixed(research).run('Synthetic alpha', SCOPE)
        self.assertEqual(result['harness']['searches'], 2)
        self.assertEqual(result['harness']['model_calls'], 5)
        self.assertEqual(result['harness']['max_model_calls'], 5)

    def test_evidence_only_stays_model_free(self):
        model = RoutedModel(CASES[222])
        result = Harness(self.corpus, model, request_guard=True).run('Synthetic alpha', SCOPE, evidence_only=True)
        self.assertEqual(model.calls, 0)
        self.assertEqual(result['status'], 'evidence_only')

    def test_malformed_router_stops_before_search_or_draft(self):
        model = RoutedModel(CASES[222])
        model.case = {**model.case, 'classification': {'requests': [], 'missing_attributes': ['name']}}
        with patch.object(self.corpus, 'packet') as retrieve, self.assertRaises(HarnessError):
            Harness(self.corpus, model, request_guard=True).run('Synthetic alpha', SCOPE)
        retrieve.assert_not_called()
        self.assertEqual(model.research.calls, 0)

    def test_trace_excludes_nested_private_reasoning_fields(self):
        trace = DebugTrace()
        trace.model_completed('request_classification', {
            'requests': [{'category': 'public_rules', 'request_text': 'Rules?', 'private_reasoning': 'SECRET'}],
            'missing_attributes': [], 'chain_of_thought': 'SECRET'})
        self.assertNotIn('SECRET', json.dumps(trace.report(error='Synthetic rejected response')))
