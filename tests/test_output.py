"""Customer output and opt-in diagnostics share the same validated evidence."""
import contextlib
import copy
import io
import json
from unittest.mock import patch

from support import RoutedModel, Fixture, FixtureGraph, SCOPE, ScriptedModel
from oracle.__main__ import main
from oracle.context import ExpandedCorpus
from oracle.corpus import ROOT, HarnessError
from oracle.harness import Harness
from oracle.output import DebugTrace, demo_result, demo_markdown, debug_markdown
from oracle.policy import check_input, inferred_scope
from oracle.quotations import passages, generation_payload, quote_options, resolve_quotes


class OutputTests(Fixture):
    def test_api_check_handles_qwen_marker_and_nova_payload_without_network(self):
        from oracle.api_check import run_api_check
        for model in ('qwen.qwen3-32b-v1:0', 'amazon.nova-lite-v1:0'):
            with self.subTest(model=model), patch('socket.socket.connect', side_effect=AssertionError('network forbidden')):
                result = run_api_check(self.db, self.registry, FixtureGraph(self.corpus),
                                       'Synthetic alpha', SCOPE, model_id=model)
            self.assertEqual(result['simulation']['aws_converse_calls'], 2)
            self.assertFalse(result['simulation']['actual_llm_inference'])

    def test_shared_provenance_preserves_excerpt_specific_qualifications(self):
        packet = self.corpus.packet('Synthetic alpha', SCOPE, 1)
        extra = copy.deepcopy(packet['evidence'][0])
        extra['evidence_id'] = 'E2'
        extra['uncertainty_flags'] = ['table_header_binding_unverified']
        packet['evidence'].append(extra)
        payload = self.corpus.model_payload(packet)
        self.assertEqual(payload['evidence'][1]['source_metadata_overrides']['uncertainty_flags'], extra['uncertainty_flags'])
        self.assertNotIn('table_header_binding_unverified', payload['document_signals'][0]['source_metadata']['uncertainty_flags'])

    def test_positive_review_notes_do_not_become_unresolved_issues(self):
        class WithNotes(ScriptedModel):
            def complete(self, system, payload, schema):
                result = super().complete(system, payload, schema)
                if 'response' in result:
                    result['review_notes'] = ['The quoted source supports the retained synthetic finding.']
                return result
        result = Harness(self.corpus, WithNotes(), 1).run('Synthetic alpha', SCOPE)
        self.assertEqual(result['status'], 'supported')
        self.assertFalse(result['harness']['review']['issues'])
        self.assertIn('supports the retained', result['harness']['review']['review_notes'][0])

    def test_concise_view_preserves_quotes_links_and_material_uncertainty(self):
        result = Harness(self.corpus, ScriptedModel(conflict=True), 1).run('Synthetic alpha', SCOPE)
        view = demo_result(result)
        self.assertNotIn('corpus_coverage', view)
        self.assertNotIn('source_notices', view)
        self.assertNotIn('harness', view)
        self.assertEqual(view['quotes'][0]['text'], result['quotes'][0]['text'])
        self.assertEqual(view['uncertainties'][0]['description'], result['uncertainties'][0]['description'])
        markdown = demo_markdown(result)
        self.assertIn('https://example.org/synthetic/', markdown)
        self.assertIn('Café 🏥.', markdown)
        self.assertIn('which version applies', markdown)

    def test_trace_captures_each_search_model_context_review_and_validation(self):
        trace = DebugTrace()
        corpus = ExpandedCorpus(self.corpus, FixtureGraph(self.corpus))
        result = Harness(corpus, ScriptedModel(search=True, conflict=True), 1, trace=trace).run('Synthetic alpha', SCOPE)
        report = trace.report(result)
        stages = [event['stage'] for event in report['events']]
        self.assertIn('initial_retrieval_and_expansion', stages)
        self.assertIn('additional_retrieval_and_expansion', stages)
        self.assertEqual(stages.count('model_started'), 3)
        self.assertEqual(stages.count('model_completed'), 3)
        self.assertIn('draft_validation', stages)
        self.assertIn('final_validation', stages)
        self.assertTrue(any(e['chunk_id'].startswith('ctx:') for e in report['evidence']))
        self.assertIn('Document access and graph connections', debug_markdown(report))
        starts = [e for e in report['events'] if e['stage'] == 'model_started']
        self.assertIn('temporal_policy', starts[0]['supporting_input'])

    def test_trace_never_collects_provider_private_reasoning_fields(self):
        trace = DebugTrace()
        trace.model_completed('draft', {'status': 'not_found', 'thinking': 'PRIVATE_SENTINEL', 'reasoning_content': 'PRIVATE_SENTINEL'})
        trace.model_completed('review', {'checks': {}, 'response': {'status': 'not_found', 'thinking': 'PRIVATE_SENTINEL'}, 'thinking': 'PRIVATE_SENTINEL'})
        self.assertNotIn('PRIVATE_SENTINEL', json.dumps(trace.report({'status': 'not_found'})))

    def test_connected_but_unloaded_document_is_not_mislabeled_as_read(self):
        graph = FixtureGraph(self.corpus)
        graph.edges = [{'relationship_id': 'r1', 'source_document_id': 'doc0', 'target_document_id': 'doc1',
            'relation_type': 'references', 'status': 'verified_reference', 'target_reference': 'alpha',
            'qualifiers': {}, 'evidence': [{'section_id': 'section0', 'start': 0, 'end': 10}]}]
        trace = DebugTrace()
        corpus = ExpandedCorpus(self.corpus, graph, max_related_documents=0)
        with patch.object(self.corpus, 'search', return_value=[self.corpus.chunk('chunk0')]):
            result = Harness(corpus, trace=trace).run('Synthetic alpha', SCOPE, evidence_only=True)
        report = trace.report(result)
        connected = next(d for d in report['documents'] if d['document_id'] == 'doc1')
        self.assertTrue(connected['graph_connected'])
        self.assertFalse(connected['retrieved'])
        self.assertFalse(connected['expanded_context_loaded'])

    def command(self, name='ask'):
        return ['--db', str(self.db), '--registry', str(self.registry), name, 'Synthetic alpha',
                '--program', SCOPE['program'], '--plan-type', SCOPE['plan_type'],
                '--research-date', SCOPE['research_date'], '--no-expansion']

    def test_demo_runs_real_inference_path_and_writes_two_separate_views(self):
        output, debug = self.root / 'demo.md', self.root / 'debug.json'
        model = RoutedModel()
        with patch('oracle.__main__._models', return_value=(model, model)), \
             patch('oracle.aws.aws_client', side_effect=AssertionError('AWS forbidden')):
            status = main(self.command('demo') + ['--output', str(output), '--debug-output', str(debug)])
        self.assertEqual(status, 0)
        self.assertEqual(model.calls, 4)
        report = json.loads(debug.read_text())
        self.assertNotIn('simulation', report['result'])
        self.assertTrue(report['result']['model_invoked'])
        self.assertIn('alpha rule', output.read_text())
        self.assertNotIn('Source status and validation', output.read_text())

    def test_rejected_model_answer_keeps_debug_evidence_and_rejection(self):
        tracefile = self.root / 'rejected.json'
        model = RoutedModel(research=ScriptedModel(bad_support=True))
        with patch('oracle.__main__._models', return_value=(model, model)), contextlib.redirect_stderr(io.StringIO()):
            status = main(self.command() + ['--debug-output', str(tracefile)])
        self.assertEqual(status, 2)
        report = json.loads(tracefile.read_text())
        self.assertEqual(report['status'], 'failed')
        self.assertIsNone(report['result'])
        self.assertTrue(report['evidence'])
        self.assertIn('could not establish support', report['error'])
        self.assertEqual(report['events'][-1]['stage'], 'rejected')

    def test_all_user_scenarios_are_valid_research_inputs_with_stable_ids(self):
        all_cases = json.loads((ROOT / 'examples/scenarios.json').read_text())['scenarios']
        self.assertEqual([item['id'] for item in all_cases[:7]], list(range(21, 28)))
        self.assertEqual(len({item['id'] for item in all_cases}), 27)
        cases = [item for item in all_cases if item['id'] <= 20]
        self.assertEqual([item['id'] for item in cases], list(range(1, 21)))
        for case in all_cases:
            check_input(case['question'], case['scope'])
            self.assertNotIn('acceptable_answer', case)
        benchmarks = json.loads((ROOT / 'examples/advocate-ground-truth.json').read_text())['cases']
        by_id = {item['id']: item for item in all_cases}
        for case in benchmarks:
            for variant in ('simplified', 'original'):
                identity = case[variant + '_scenario_id']
                if identity is not None:
                    self.assertEqual(by_id[identity]['advocate_case'], case['id'])
                    self.assertEqual(by_id[identity]['variant'], variant)
        self.assertIsNone(benchmarks[3]['original_scenario_id'])
        self.assertIn('no written notice', cases[0]['question'])
        self.assertIn('$40,000', cases[14]['question'])
        self.assertEqual([c['expected_behavior']['outcome'] for c in cases[15:]], [
            'insufficient_evidence_or_unsupported_conclusion', 'needs_clarification',
            'insufficient_evidence_or_unsupported_conclusion', 'insufficient_evidence_or_unsupported_conclusion', 'outside_system_scope'])
        scope = inferred_scope(cases[0]['question'])
        self.assertEqual(scope, cases[0]['scope'])

    def test_long_scenario_retrieval_reaches_question_after_narrative(self):
        question = 'A narrative with unrelated details. ' * 12 + 'What rules govern synthetic alpha, and what addendum applies?'
        found = self.corpus.search(question, 2)
        self.assertEqual({item['document_id'] for item in found}, {'doc0', 'doc1'})
        self.assertEqual(self.corpus.last_search['strategy'], 'lexical_question_clauses')
        self.assertEqual(len(self.corpus.last_search['clause_expressions']), 2)
        self.assertNotIn('narrati', ' '.join(self.corpus.last_search['clause_expressions']))

    def test_selecting_case_supplies_scope_without_extra_personal_questions(self):
        output = self.root / 'case.json'
        with patch('oracle.__main__.Harness.run', return_value={'status': 'not_found', 'model_invoked': False}) as run:
            status = main(['--db', str(self.db), '--registry', str(self.registry), 'ask', '--case', '1',
                           '--no-expansion', '--evidence-only', '--output', str(output)])
        self.assertEqual(status, 0)
        self.assertIn('72-year-old', run.call_args.args[0])
        self.assertEqual(run.call_args.args[1]['plan_type'], 'MLTC')

    def test_output_paths_must_be_distinct_before_inference(self):
        output = self.root / 'same.json'
        with patch('oracle.__main__._models') as models, contextlib.redirect_stderr(io.StringIO()):
            status = main(self.command() + ['--output', str(output), '--debug-output', str(output)])
        self.assertEqual(status, 2)
        models.assert_not_called()
        self.assertFalse(output.exists())

    def test_passage_selectors_preserve_all_unicode_text_and_resolve_exactly(self):
        item = {'evidence_id': 'E1', 'chunk_id': 'unicode', 'text': ('Café 🏥 rule with exceptions.\n\n' * 180)}
        parts = passages(item)
        self.assertGreater(len(parts), 1)
        self.assertEqual(''.join(p['text'] for p in parts), item['text'])
        self.assertTrue(all(len(p['text']) <= 1400 for p in parts))
        packet = {'evidence': [item]}
        payload = generation_payload(packet)
        self.assertNotIn('text', payload['evidence'][0])
        self.assertEqual(''.join(p['text'] for p in payload['evidence'][0]['passages']), item['text'])
        response = resolve_quotes(packet, {'quotes': [parts[1]['quote_id']]})
        span = response['quotes'][0]
        self.assertEqual(item['text'][span['start']:span['end']], parts[1]['text'])
        with self.assertRaises(HarnessError):
            resolve_quotes(packet, {'quotes': ['E99.Q1']})

    def test_selector_schema_cannot_accept_invented_ids_or_offsets(self):
        from oracle.harness import constrained_schema
        from oracle.corpus import base
        packet = self.corpus.packet('Synthetic alpha', SCOPE, 1)
        schema = constrained_schema(packet)['properties']['quotes']
        base.validate_schema(['E1.Q1'], schema)
        with self.assertRaises(HarnessError):
            base.validate_schema(['E1.Q999'], schema)
        with self.assertRaises(HarnessError):
            base.validate_schema([{'chunk_id': 'chunk0', 'start': 0, 'end': 99999}], schema)

    def test_model_input_reuses_exact_parent_text_without_dropping_evidence(self):
        corpus = ExpandedCorpus(self.corpus, FixtureGraph(self.corpus))
        packet = corpus.packet('Synthetic alpha', SCOPE, 1)
        payload = generation_payload(corpus.model_payload(packet))
        self.assertEqual(len(packet['evidence']), 2)
        self.assertEqual(len(payload['evidence']), 1)
        child_id, parent_id = next(iter(payload['covered_retrieval_hits'].items()))
        child = next(e for e in packet['evidence'] if e['evidence_id'] == child_id)
        parent = next(e for e in payload['evidence'] if e['evidence_id'] == parent_id)
        self.assertIn(child['text'], ''.join(p['text'] for p in parent['passages']))
        self.assertNotIn('title', parent)
        metadata = payload['document_signals'][0]['source_metadata']
        self.assertEqual(metadata['source_url'], child['source_url'])
        from oracle.harness import constrained_schema
        ids = constrained_schema(packet)['properties']['findings']['items']['properties']['evidence_ids']['items']['enum']
        self.assertNotIn(child_id, ids)
        self.assertIn(parent_id, ids)
