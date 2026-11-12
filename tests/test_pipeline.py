"""Research pipeline on the synthetic corpus: scripted stage outputs, no network, no numpy required."""
import copy
import unittest
from unittest.mock import patch

from support import FixtureGraph, SCOPE
from test_local_vectors import VectorFixture
from oracle.context import ExpandedCorpus
from oracle.corpus import HarnessError
from oracle.harness import Harness
from oracle.local_vectors import CachedTitanQueries, LocalVectorCorpus
from oracle.output import DebugTrace
from oracle.paragraphs import split_paragraphs
from oracle.pipeline import classify, off_scope, assemble
from oracle.pipeline_prompts import ANSWER_POLICY, DOUBTS_POLICY, PLANNER_POLICY, READER_POLICY, TRIAGE_POLICY, VERIFY_POLICY


UNDATED = {**SCOPE, 'research_date': 'unknown'}


class StageModel:
    """Returns a fixed, schema-shaped output per stage and records what each stage was shown."""
    model = 'synthetic-stages'

    def __init__(self, **overrides):
        self.overrides, self.seen, self.usage = overrides, [], []

    def complete(self, system, payload, schema):
        if system.startswith(PLANNER_POLICY[:80]):
            stage = 'plan'
        else:
            stage = next(name for name, text in (('read', READER_POLICY), ('triage', TRIAGE_POLICY), ('answer', ANSWER_POLICY),
                                                 ('verify', VERIFY_POLICY), ('doubts', DOUBTS_POLICY)) if system.startswith(text[:80]))
        self.seen.append((stage, copy.deepcopy(payload)))
        if stage in self.overrides:
            value = self.overrides[stage]
            return value(payload) if callable(value) else copy.deepcopy(value)
        if stage == 'plan':
            return {'restated_question': 'What does the synthetic alpha rule provide?', 'issues': ['Synthetic alpha rule'],
                    'legal_concepts': ['synthetic alpha'], 'passages': ['The synthetic alpha rule applies.'],
                    'key_phrases': ['alpha rule'], 'likely_documents': ['D1: Synthetic alpha']}
        if stage == 'read':
            return {'selected': [{'id': p['id'], 'grade': 3, 'reason': 'states the rule'}
                                 for e in payload['excerpts'] for p in e['passages']] + [{'id': 'S9.9', 'grade': 3, 'reason': 'invented'}]}
        if stage == 'doubts':
            return {'doubts': []}
        if stage == 'verify':
            return {'findings': [{'index': 1, 'problem': '', 'verdict': 'supported', 'corrected_text': ''},
                                 {'index': 2, 'problem': 'bans beta', 'verdict': 'unsupported', 'corrected_text': ''}]}
        ids = [p['quote_id'] for s in payload['sources'] for p in s['passages']]
        if stage == 'triage':
            return {'needs': ['What does the alpha rule provide?'],
                    'key_passages': [{'quote_id': q, 'rule': 'alpha', 'applies_to': 'synthetic'} for q in ids] +
                                    [{'quote_id': 'E99.Q1', 'rule': 'invented', 'applies_to': 'nothing'}]}
        if stage == 'answer':
            return {'findings': [{'text': 'The synthetic source states an alpha rule.', 'quote_ids': [ids[0]]},
                                 {'text': 'The synthetic source also bans beta.', 'quote_ids': [ids[-1]]},
                                 {'text': 'An invented citation.', 'quote_ids': ['E99.Q1']}],
                    'basis': 'The alpha rule is stated directly.', 'answer': 'Yes - the alpha rule applies.',
                    'answer_quote_ids': [ids[0], 'E99.Q1'], 'not_addressed': [], 'status': 'supported'}
        raise AssertionError('unexpected stage ' + stage)


class PipelineTests(VectorFixture):
    def harness(self, model, embed=True, **options):
        cache, _ = self.queries()
        corpus = LocalVectorCorpus(self.db, self.registry, self.index, cache if embed else CachedTitanQueries(self.query_path))
        self.addCleanup(corpus.close)
        self.trace = DebugTrace()
        return Harness(ExpandedCorpus(corpus, FixtureGraph(corpus)), model, trace=self.trace, pipeline='research', **options)

    def run_research(self, model, scope=UNDATED, **options):
        with patch('oracle.paragraphs.PARAGRAPH_VECTORS', self.root / 'absent.sqlite'):
            return self.harness(model, **options).run('Synthetic alpha', scope)

    def test_paragraph_split_is_a_lossless_partition(self):
        text = 'Heading\n\n' + 'First sentence of a rule. ' * 80 + '\n\nShort.\n\n' + 'x' * 2500
        spans = split_paragraphs(text)
        self.assertEqual(''.join(text[a:b] for a, b in spans), text)
        self.assertTrue(all(b - a <= 1100 for a, b in spans))

    def test_authority_labels_and_scope(self):
        self.assertEqual(classify('MLTC Partial Capitation Model Contract'), ('Model contract: MLTC partial capitation', 'MLTC'))
        self.assertTrue(off_scope('42 C.F.R. Ch. VI, Sub. B, Pt. 422', 'MLTC'))
        self.assertTrue(off_scope('Medicaid Advantage Plus (MAP) Model Contract', 'MLTC'))
        self.assertFalse(off_scope('Medicaid Advantage Plus (MAP) Model Contract', 'unknown'))
        self.assertFalse(off_scope('42 C.F.R. Ch. IV, Sub. C, Pt. 438', 'MLTC'))

    def test_full_run_resolves_ids_drops_unsupported_and_leads_with_the_answer(self):
        model = StageModel()
        result = self.run_research(model)
        self.assertEqual(result['status'], 'supported')
        self.assertEqual(result['findings'][0]['text'], 'Yes - the alpha rule applies.')
        texts = [f['text'] for f in result['findings']]
        self.assertIn('The synthetic source states an alpha rule.', texts)
        self.assertNotIn('The synthetic source also bans beta.', texts)        # verifier: unsupported
        self.assertNotIn('An invented citation.', texts)                       # unknown passage id
        for quote in result['quotes']:                                         # exact canonical text only
            self.assertEqual(quote['text'], self.corpus.chunk(quote['chunk_id'])['text'][quote['start']:quote['end']])
        research = result['harness']['research']
        self.assertEqual(result['harness']['pipeline'], 'research')
        self.assertEqual(research['findings_dropped'], 1)
        self.assertNotIn('E99.Q1', research['key_passages'])
        stages = [s for s, _ in model.seen]
        self.assertEqual(stages[0], 'plan')
        self.assertEqual(stages[-4:-2], ['triage', 'answer'])
        self.assertEqual(sorted(stages[-2:]), ['doubts', 'verify'])
        self.assertEqual(result['harness']['model_calls'], len(stages))
        # The answer stage sees only the triaged passages and the needs.
        answer_payload = next(p for s, p in model.seen if s == 'answer')
        self.assertEqual(set(answer_payload), {'needs', 'question', 'scope', 'sources'})
        roles = [e['role'] for e in self.trace.events if e['stage'] == 'model_completed']
        self.assertEqual(sorted(set(roles)), ['answer', 'doubts', 'plan', 'read', 'triage', 'verify'])

    def test_overstated_finding_is_replaced_by_the_checked_text(self):
        verify = {'findings': [{'index': 1, 'problem': 'x', 'verdict': 'overstated', 'corrected_text': 'The source states an alpha rule.'},
                               {'index': 2, 'problem': '', 'verdict': 'supported', 'corrected_text': ''}]}
        result = self.run_research(StageModel(verify=verify))
        texts = [f['text'] for f in result['findings']]
        self.assertIn('The source states an alpha rule.', texts)
        self.assertNotIn('The synthetic source states an alpha rule.', texts)
        self.assertEqual(result['harness']['research']['findings_corrected'], 1)

    def test_nothing_selected_is_not_found_without_an_answer_call(self):
        model = StageModel(read={'selected': []})
        result = self.run_research(model)
        self.assertEqual(result['status'], 'not_found')
        self.assertEqual(result['findings'], [])
        self.assertNotIn('answer', [s for s, _ in model.seen])

    def test_gaps_qualify_the_status_and_advice_is_removed(self):
        def answer(payload):
            ids = [p['quote_id'] for s in payload['sources'] for p in s['passages']]
            return {'findings': [{'text': 'The synthetic source states an alpha rule.', 'quote_ids': [ids[0]]},
                                 {'text': 'You should file an appeal now.', 'quote_ids': [ids[0]]}],
                    'basis': 'Stated directly.', 'answer': 'Yes - the alpha rule applies.', 'answer_quote_ids': [ids[0]],
                    'not_addressed': ['The passages do not mention "gamma agreements".'], 'status': 'supported'}
        result = self.run_research(StageModel(answer=answer, verify={'findings': []}))
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['uncertainties'][0]['kind'], 'not_addressed')
        self.assertFalse(any('You should' in f['text'] for f in result['findings']))

    def test_ambiguous_source_becomes_a_follow_up_question(self):
        def doubts(payload):
            ids = [p['quote_id'] for s in payload['sources'] for p in s['passages']]
            return {'doubts': [{'issue': 'The alpha sentence (' + ids[0] + ') does not say which group it covers.',
                                'readings': 'It may cover everyone, or only new members.',
                                'question': 'Does the alpha rule cover existing members?', 'quote_ids': [ids[0], 'E99.Q1']},
                               {'issue': 'Unknown.', 'readings': 'x', 'question': 'What is the member number?', 'quote_ids': []}]}
        model = StageModel(doubts=doubts)
        result = self.run_research(model)
        self.assertEqual(result['status'], 'partial')
        items = [u for u in result['uncertainties'] if u['kind'] == 'ambiguous_source']
        self.assertEqual(len(items), 1)                                         # the personal-detail question is dropped
        self.assertEqual(items[0]['question_for_user'], 'Does the alpha rule cover existing members?')
        self.assertTrue(items[0]['evidence_ids'])
        self.assertNotRegex(items[0]['description'], r'E\d+\.Q\d+')            # internal passage ids never reach the advocate
        self.assertIn('draft_answer', next(p for s, p in model.seen if s == 'doubts'))

    def test_failed_reader_batch_and_failed_check_do_not_sink_the_run(self):
        def broken(payload):
            raise HarnessError('synthetic provider failure')
        result = self.run_research(StageModel(verify=broken))
        self.assertEqual(result['status'], 'supported')
        self.assertIn('synthetic provider failure', result['harness']['research']['verify_error'])
        result = self.run_research(StageModel(read=broken))
        self.assertEqual(result['status'], 'not_found')
        self.assertTrue(result['harness']['research']['read_failures'])

    def test_unapproved_embedding_miss_stops_before_any_model_call(self):
        model = StageModel()
        with patch('oracle.paragraphs.PARAGRAPH_VECTORS', self.root / 'absent.sqlite'):
            with self.assertRaisesRegex(HarnessError, 'Explicit approval'):
                self.harness(model, embed=False).run('Synthetic alpha', UNDATED)
        self.assertEqual(model.seen, [])

    def test_separate_answer_model_writes_only_the_last_three_calls(self):
        worker, writer = StageModel(), StageModel()
        self.run_research(worker, reviewer=writer)
        self.assertEqual(sorted({s for s, _ in worker.seen}), ['plan', 'read'])
        self.assertEqual(sorted(s for s, _ in writer.seen), ['answer', 'doubts', 'triage', 'verify'])

    def test_assemble_caps_quotes_and_keeps_every_cited_chunk_quoted(self):
        packet = {'scope': {'question': 'q', 'program': None, 'plan_type': None, 'research_date': None, 'assumptions': []},
                  'evidence': [{'chunk_id': f'c{i}', 'evidence_id': f'E{i}'} for i in range(1, 41)]}
        quotes = {f'E{i}.Q1': {'chunk_id': f'c{i}', 'start': 0, 'end': 5, 'grade': 3} for i in range(1, 41)}
        keys = list(quotes)[:14]
        findings = [{'text': f'Finding {i}.', 'quote_ids': [keys[i]]} for i in range(10)]
        draft = {'answer': 'Yes.', 'answer_quote_ids': keys[:2], 'not_addressed': [], 'status': 'supported'}
        response = assemble(packet, quotes, keys, draft, findings)
        self.assertLessEqual(len(response['quotes']), 30)
        quoted = {q['chunk_id'] for q in response['quotes']}
        for finding in response['findings']:
            self.assertTrue({'c' + e[1:] for e in finding['evidence_ids']} <= quoted)


if __name__ == '__main__':
    unittest.main()
