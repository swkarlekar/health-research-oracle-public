"""Comparison runner records failures/resumes without spending or mixing trials."""
import json
from unittest.mock import patch

from support import FixtureGraph, SCOPE, ScriptedModel
from test_local_vectors import VectorFixture
from oracle.corpus import HarnessError
from oracle.evaluation import run_arm


class EvaluationTests(VectorFixture):
    def test_local_comparison_records_real_pipeline_and_resume_skips_model(self):
        queries, _ = self.queries()
        queries.embed('Synthetic alpha')
        cases = [{'id': 1, 'question': 'Synthetic alpha', 'scope': SCOPE}]
        options = {'directory': self.root / 'evaluation', 'corpus_db': self.db,
            'registry': self.registry, 'cases': cases, 'model_factory': ScriptedModel}
        options['local_identity'] = {'model': 'synthetic'}
        with patch('oracle.evaluation.GraphStore', side_effect=lambda *args: FixtureGraph(self.corpus)), \
             patch('socket.socket.connect', side_effect=AssertionError('network forbidden')):
            rows = run_arm('local-local', self.index, queries, **options)
            self.assertEqual(rows[0]['status'], 'supported')
            self.assertEqual(rows[0]['calculated_model_cost_usd'], 0)
            options['model_factory'] = lambda: (_ for _ in ()).throw(AssertionError('duplicate model invocation'))
            self.assertEqual(run_arm('local-local', self.index, queries, **options), rows)
        report = json.loads((self.root / 'evaluation/local-local/scenario-01.debug.json').read_text())
        self.assertEqual(report['events'][0]['retrieval']['engine'], 'local_titan_vectors')

    def test_interrupted_scenario_is_not_silently_repeated(self):
        queries, _ = self.queries()
        directory = self.root / 'evaluation/local-local'
        directory.mkdir(parents=True)
        (directory / 'scenario-01.started.json').write_text('{}')
        with patch('oracle.evaluation.GraphStore', side_effect=lambda *args: FixtureGraph(self.corpus)), \
             self.assertRaisesRegex(HarnessError, 'interrupted'):
            run_arm('local-local', self.index, queries, directory=directory.parent,
                registry=self.registry, local_identity={'model': 'synthetic'},
                cases=[{'id': 1, 'question': 'Synthetic alpha', 'scope': SCOPE}])
