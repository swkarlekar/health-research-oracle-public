"""Bundled sample chats: seeded once, validated against the local sources, never calling a model or AWS."""
from contextlib import contextmanager
import gzip
import json
from unittest.mock import patch

from support import Fixture, FixtureGraph, ScriptedModel
from oracle import saved_runs
from oracle.corpus import base
from oracle.web import LocalApp

QUESTION = 'What does the synthetic alpha rule require?'
SCENARIOS = {21: {'id': 21, 'title': 'Synthetic simplified', 'demo_label': 'Advocate 1 · Synthetic topic · simplified',
                  'question': QUESTION, 'advocate_case': 1, 'variant': 'simplified'},
             22: {'id': 22, 'title': 'Synthetic original', 'demo_label': 'Advocate 1 · Synthetic topic · original',
                  'question': QUESTION + ' (original)', 'advocate_case': 1, 'variant': 'original'}}


class SavedRunTests(Fixture):
    def setUp(self):
        super().setUp()
        self.dir = self.root / 'saved'
        self.dir.mkdir()
        self.graph = FixtureGraph(self.corpus)
        self.app = LocalApp(self.db, self.registry, self.root / 'graph.sqlite', self.root / 'ui.sqlite',
                            model_factory=lambda: ScriptedModel(), budget_path=self.root / 'budget.sqlite')
        graph, app = self.graph, self.app
        patcher = patch.object(saved_runs, 'GraphStore', lambda path, snapshot: graph)
        patcher.start()
        self.addCleanup(patcher.stop)

    def write(self, number, question, **changes):
        text = self.corpus.chunk('chunk0')['text']
        result = {'status': 'supported', 'model_invoked': True, 'findings': [], 'uncertainties': [], 'clarifying_questions': [],
                  'scope': {'question': question, 'program': 'New York Medicaid', 'plan_type': 'MLTC', 'research_date': 'unknown', 'assumptions': []},
                  'quotes': [{'chunk_id': 'chunk0', 'start': 0, 'end': len(text), 'text': text, 'citation': {}}],
                  'harness': {'model': 'qwen.synthetic', 'registry_sha256': base.digest(base.canonical(self.corpus.registry))}}
        result.update(changes)
        report = {'status': 'accepted', 'events': [], 'result': result}
        (self.dir / f'scenario-{number:02d}.debug.json.gz').write_bytes(gzip.compress(json.dumps(report).encode()))

    def seed(self):
        messages = []
        added = saved_runs.seed(self.app, self.dir, log=messages.append, scenarios=SCENARIOS)
        return added, messages

    def test_seeds_once_in_scenario_order_with_readable_titles(self):
        self.write(21, QUESTION)
        self.write(22, QUESTION + ' (original)')
        with patch('socket.socket.connect', side_effect=AssertionError('network forbidden')):
            added, messages = self.seed()
            self.assertEqual((added, messages), (2, []))
            self.assertEqual(self.seed()[0], 0)  # a second start adds nothing
        threads = self.app.threads()
        self.assertEqual([t['title'] for t in threads], ['Case 1 · simplified · Synthetic topic', 'Case 1 · original · Synthetic topic'])
        run = self.app.thread(threads[0]['id'])[0]
        self.assertEqual((run['saved_example'], run['status'], run['question']), (saved_runs.SAMPLE_RUN, 'done', QUESTION))
        self.assertIsNotNone(run['answer'])
        self.assertIn('events', self.app.report(run['id']))

    def test_a_users_own_chats_stay_above_the_samples_and_are_untouched(self):
        self.write(21, QUESTION)
        mine = self.app.add_run('My own question', 'My own question', {'program': 'x'})
        self.seed()
        titles = [t['title'] for t in self.app.threads()]
        self.assertEqual(titles[0], 'My own question')
        self.assertEqual(self.app.row(mine)['saved_example'], 0)

    def test_mismatched_sources_or_questions_are_skipped_not_shown(self):
        self.write(21, QUESTION, harness={'model': 'qwen.synthetic', 'registry_sha256': '0' * 64})
        self.write(22, 'A different question')
        added, messages = self.seed()
        self.assertEqual(added, 0)
        self.assertEqual(len(messages), 2)
        self.assertEqual(self.app.threads(), [])

    def test_quotations_must_match_the_saved_source_text(self):
        self.write(21, QUESTION, quotes=[{'chunk_id': 'chunk0', 'start': 0, 'end': 5, 'text': 'forged'}])
        self.assertEqual(self.seed()[0], 0)

    def test_simulated_or_non_model_runs_are_rejected(self):
        self.write(21, QUESTION, simulation={'enabled': True})
        self.write(22, QUESTION + ' (original)', model_invoked=False)
        self.assertEqual(self.seed()[0], 0)

    def test_missing_directory_adds_nothing(self):
        self.assertEqual(saved_runs.seed(self.app, self.root / 'nope', scenarios=SCENARIOS), 0)
