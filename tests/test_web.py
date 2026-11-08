"""Local UI storage, research boundaries, and source-viewer integrity checks."""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import time
import threading
from unittest.mock import patch

from support import Fixture, FixtureGraph, SCOPE, ScriptedModel
from oracle.context import ExpandedCorpus
from oracle.corpus import HarnessError
from oracle.harness import Harness
from oracle.viewer import CitationViewer, match_boxes
from oracle.web import LocalApp, LocalHandler, BusyError
from oracle.corpus import base


class WebTests(Fixture):
    def test_ui_infers_plan_without_requiring_a_duplicate_form_field(self):
        app = self.app()
        for question, expected in [('New York Medicaid MLTC rules', 'MLTC'),
                                   ('General Medicaid research', 'unknown')]:
            run = app.submit({'question': question, 'scope': {'program': 'New York Medicaid'}})
            self.assertEqual(run['scope']['plan_type'], expected)
            self.assertNotEqual(run['status'], 'clarification_needed')
            self.done(app, run['id'])

    def test_default_model_settings_are_bedrock_and_do_not_authorize_calls(self):
        app = self.app()
        with patch('socket.socket.connect', side_effect=AssertionError('network forbidden')):
            settings = app.models.public()
        self.assertEqual(settings['provider'], 'bedrock')
        self.assertEqual(settings['bedrock_model'], 'qwen.qwen3-32b-v1:0')
        self.assertFalse(settings['aws_enabled'])

    def test_aws_disable_cannot_be_undone_by_an_earlier_inflight_save(self):
        app = self.app()
        entered, release = threading.Event(), threading.Event()
        order = []
        def save(payload):
            entered.set()
            self.assertTrue(release.wait(2))
            app.models.value['authorization'] = {'test': True}
            order.append('save')
            return {}
        def disable():
            app.models.value['authorization'] = None
            order.append('disable')
            return {}
        with patch.object(app.models, 'save', side_effect=save), patch.object(app.models, 'disable', side_effect=disable):
            first = threading.Thread(target=app.save_settings, args=({},))
            second = threading.Thread(target=app.disable_aws)
            first.start()
            self.assertTrue(entered.wait(2))
            second.start()
            release.set()
            first.join(2);second.join(2)
        self.assertEqual(order, ['save', 'disable'])
        self.assertIsNone(app.models.value['authorization'])

    def test_ui_shaped_followup_preserves_date_unless_explicitly_cleared(self):
        app = self.app()
        first = app.submit({'question': 'Synthetic alpha', 'scope': {**SCOPE, 'research_date': '2024-01-01'}})
        self.done(app, first['id'])
        payload = {'question': 'What about its exceptions?', 'thread_id': first['thread_id'],
                   'scope': {**SCOPE, 'research_date': 'unknown'}}
        follow = app.submit(payload)
        self.done(app, follow['id'])
        self.assertEqual(follow['scope']['research_date'], '2024-01-01')
        payload.update(question='Use an unspecified research date.', clear_research_date=True)
        cleared = app.submit(payload)
        self.done(app, cleared['id'])
        self.assertEqual(cleared['scope']['research_date'], 'unknown')
    def test_saved_bedrock_report_is_imported_offline_and_revalidates_quotations(self):
        app = self.app()
        run = app.submit({'question': 'Synthetic alpha', 'scope': SCOPE})
        self.done(app, run['id'])
        report = app.report(run['id'])
        report['result']['harness']['model'] = 'qwen.qwen3-32b-v1:0'
        examples = self.root / 'examples'
        examples.mkdir(exist_ok=True)
        (examples / 'scenarios.json').write_text(json.dumps({'scenarios': [{'id': 1, 'question': 'Synthetic alpha'}]}))
        directory = self.root / 'artifacts/aws-eval'
        directory.mkdir(parents=True)
        path = directory / 'scenario-01.debug.json'
        path.write_text(json.dumps(report))
        audit = directory / 'content-audit.json'
        audit.write_text(json.dumps({'results': [{'result_sha256': base.digest(base.canonical(report['result'])),
                                                  'status': 'reviewed'}]}))
        with patch('oracle.web.ROOT', self.root), patch('oracle.aws.aws_client', side_effect=AssertionError('AWS forbidden')):
            saved = app.import_example(1)
            self.assertEqual(saved['saved_example'], 2)
            self.assertEqual(saved['answer']['status'], 'supported')
            before = len(app.threads())
            report['result']['quotes'][0]['text'] += ' altered'
            path.write_text(json.dumps(report))
            audit.write_text(json.dumps({'results': [{'result_sha256': base.digest(base.canonical(report['result'])),
                                                      'status': 'reviewed'}]}))
            with self.assertRaises(HarnessError):
                app.import_example(1)
            self.assertEqual(len(app.threads()), before)
            # A previous import is also withheld after a content audit rejects it.
            audit.write_text(json.dumps({'results': [{'result_sha256': base.digest(base.canonical(app.result(saved['id']))),
                                                      'status': 'rejected'}]}))
            self.assertIsNone(app.public(app.row(saved['id']))['answer'])
            self.assertIn('content review', app.public(app.row(saved['id']))['error'])

    def app(self, factory=None):
        app = LocalApp(self.db, self.registry, self.root / 'graph.sqlite', self.root / 'ui.sqlite',
                       model_factory=factory or (lambda: ScriptedModel()), budget_path=self.root / 'budget.sqlite')
        @contextmanager
        def corpus():
            # Each worker owns its SQLite connection, just like the live server.
            from oracle.corpus import ApprovedCorpus
            source = ApprovedCorpus(self.db, self.registry)
            try:
                yield ExpandedCorpus(source, FixtureGraph(source))
            finally:
                source.close()
        app.corpus = corpus
        return app

    def done(self, app, identity):
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            row = app.row(identity)
            if row['status'] != 'running':
                return app.public(row)
            time.sleep(.01)
        self.fail('Local worker did not finish')

    def test_real_harness_path_history_and_debug_persist_without_aws(self):
        app = self.app()
        with patch('oracle.aws.aws_client', side_effect=AssertionError('AWS forbidden')):
            run = app.submit({'question': 'Synthetic alpha', 'scope': SCOPE})
            finished = self.done(app, run['id'])
        self.assertEqual(finished['status'], 'done')
        self.assertEqual(finished['answer']['status'], 'supported')
        self.assertNotIn('harness', finished['answer'])
        report = app.report(run['id'])
        self.assertEqual(report['result']['harness']['model_calls'], 2)
        self.assertTrue(report['evidence'])
        restored = self.app()
        self.assertEqual(restored.thread(run['thread_id'])[0]['question'], 'Synthetic alpha')
        self.assertEqual(restored.report(run['id'])['status'], 'supported')

    def test_identifying_input_is_rejected_before_any_history_is_saved(self):
        app = self.app()
        with self.assertRaises(HarnessError):
            app.submit({'question': 'Research for member: person@example.org', 'scope': SCOPE})
        self.assertFalse(app.threads())

    def test_missing_scope_asks_without_corpus_or_model(self):
        app = self.app(lambda: self.fail('model should not be constructed'))
        with patch.object(app, 'corpus', side_effect=AssertionError('corpus forbidden')):
            run = app.submit({'question': 'Synthetic alpha', 'scope': {}})
        self.assertEqual(run['answer']['status'], 'clarification_needed')
        self.assertFalse(app.report(run['id'])['result']['model_invoked'])

    def test_followup_uses_only_user_context_and_preserves_governing_date(self):
        app = self.app()
        scope = dict(SCOPE, research_date='2024-01-01')
        first = app.submit({'question': 'Synthetic alpha', 'scope': scope})
        self.done(app, first['id'])
        followup = app.submit({'question': 'Does the addendum change it?', 'thread_id': first['thread_id']})
        self.done(app, followup['id'])
        row = app.row(followup['id'])
        self.assertIn('Synthetic alpha', row['effective_question'])
        self.assertIn('Latest user message', row['effective_question'])
        self.assertNotIn('The synthetic source contains', row['effective_question'])
        self.assertEqual(json.loads(row['scope_json'])['research_date'], '2024-01-01')
        self.assertTrue(app.public(row)['uses_history'])

    def test_standalone_message_does_not_silently_inherit_context(self):
        app = self.app()
        first = app.submit({'question': 'Synthetic alpha', 'scope': SCOPE})
        self.done(app, first['id'])
        other = app.submit({'question': 'Synthetic addendum', 'scope': SCOPE,
                            'thread_id': first['thread_id'], 'use_history': False})
        self.done(app, other['id'])
        self.assertEqual(app.row(other['id'])['effective_question'], 'Synthetic addendum')

    def test_concurrent_inference_is_rejected_without_saving_an_extra_run(self):
        app = self.app()
        app.lock.acquire()
        try:
            with self.assertRaises(BusyError):
                app.submit({'question': 'Synthetic alpha', 'scope': SCOPE})
        finally:
            app.lock.release()
        self.assertFalse(app.threads())

    def test_failed_model_keeps_debug_evidence_and_no_accepted_answer(self):
        class Broken:
            def complete(self, *args):
                raise HarnessError('Local model unavailable')
        app = self.app(Broken)
        run = app.submit({'question': 'Synthetic alpha', 'scope': SCOPE})
        finished = self.done(app, run['id'])
        self.assertEqual(finished['status'], 'failed')
        self.assertIsNone(finished['answer'])
        self.assertTrue(app.report(run['id'])['evidence'])
        self.assertIn('unavailable', app.report(run['id'])['error'])

    def test_host_origin_and_token_checks_block_cross_site_writes(self):
        handler = LocalHandler.__new__(LocalHandler)
        handler.server = SimpleNamespace(server_address=('127.0.0.1', 8765), app=SimpleNamespace(token='local-token'))
        handler.headers = {'Host': '127.0.0.1:8765', 'Origin': 'http://127.0.0.1:8765', 'X-Oracle-Token': 'local-token'}
        self.assertTrue(handler.allowed(True))
        handler.headers['Origin'] = 'https://untrusted.example'
        self.assertFalse(handler.allowed(True))
        handler.headers.pop('Origin')
        handler.headers['X-Oracle-Token'] = 'wrong'
        self.assertFalse(handler.allowed(True))
        handler.headers['Host'] = 'rebound.example:8765'
        self.assertFalse(handler.allowed(False))

    def test_viewer_keeps_exact_unicode_quote_and_rejects_forgery(self):
        corpus = ExpandedCorpus(self.corpus, FixtureGraph(self.corpus))
        result = Harness(corpus, ScriptedModel(), 1).run('Synthetic alpha', SCOPE)
        viewer = CitationViewer(corpus, self.root / 'raw')
        frozen = viewer.freeze(result, 0)
        description = frozen.describe(result, 0)
        self.assertEqual(description['kind'], 'text')
        self.assertEqual(description['quote'], result['quotes'][0]['text'])
        self.assertIn('Café 🏥', description['quote'])
        result['quotes'][0]['text'] += ' FORGED'
        with self.assertRaises(HarnessError):
            frozen.describe(result, 0)
        with self.assertRaises(HarnessError):
            viewer.describe(result, 0)

    def test_original_source_hash_and_path_confinement_are_enforced(self):
        raw = self.root / 'raw'
        raw.mkdir()
        source = raw / 'source.txt'
        source.write_bytes(b'Synthetic source')
        viewer = CitationViewer(None, raw)
        doc = {'raw_path': 'raw/source.txt', 'raw_sha256': hashlib.sha256(source.read_bytes()).hexdigest()}
        self.assertEqual(viewer.raw_file(doc)[1], b'Synthetic source')
        source.write_bytes(b'MODIFIED')
        with self.assertRaises(HarnessError):
            viewer.raw_file(doc)
        outside = self.root / 'outside.txt'
        outside.write_bytes(b'Outside')
        doc = {'raw_path': str(outside), 'raw_sha256': hashlib.sha256(b'Outside').hexdigest()}
        with self.assertRaises(HarnessError):
            viewer.raw_file(doc)
        (raw / 'link.txt').symlink_to(outside)
        doc['raw_path'] = 'raw/link.txt'
        with self.assertRaises(HarnessError):
            viewer.raw_file(doc)

    def test_partial_pdf_matches_are_labeled_and_never_invent_boxes(self):
        class Page:
            def search_for(self, text):
                return [(10., 20., 180., 35.)] if text == 'This longer source line is available on the PDF page.' else []
        fragment = 'This longer source line is available on the PDF page.\nA missing line that also contains enough words to search.'
        boxes, methods = match_boxes(Page(), [fragment])
        self.assertEqual(boxes, [[10., 20., 180., 35.]])
        self.assertIn('partial_line_matches', methods)
        boxes, methods = match_boxes(Page(), ['No reliable match in this document.'])
        self.assertFalse(boxes)
        self.assertFalse(methods)
