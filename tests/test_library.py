"""Sidebar document lookup: listing, phrase search, and whole-document viewing without any model call."""
from contextlib import contextmanager
import hashlib
import json
from unittest.mock import patch

from support import Fixture, FixtureGraph, ScriptedModel
from oracle import library
from oracle.context import ExpandedCorpus
from oracle.corpus import ApprovedCorpus, HarnessError
from oracle.viewer import CitationViewer
from oracle.web import LocalApp


class TitleTests(Fixture):
    def test_line_wrap_slashes_and_bare_section_numbers_are_cleaned(self):
        clean = library.display_title
        self.assertEqual(clean('N.Y. Public / Health Law § 4914 – Procedures', ''), 'N.Y. Public Health Law § 4914 – Procedures')
        self.assertEqual(clean('N.Y. / Public Health Law § 4903 – Utilization review', ''), 'N.Y. Public Health Law § 4903 – Utilization review')
        self.assertEqual(clean('§ 4905 / – Required', ''), '§ 4905 – Required')
        self.assertEqual(clean('§ 438.71', 'eCFR :: 42 CFR 438.71 -- Beneficiary support system.'),
                         '42 CFR 438.71 — Beneficiary support system.')
        self.assertEqual(clean('FE-ABD', 'Facilitated Enrollment for the Aged (FE-ABD) | Community Service Society of New York'),
                         'Facilitated Enrollment for the Aged (FE-ABD)')
        # A repeated citation after the slash is dropped rather than appended.
        self.assertEqual(clean('N.Y. Social Services Law § 365-a – Adequacy / N.Y. Social Services Law § 365-a(8)', ''),
                         'N.Y. Social Services Law § 365-a – Adequacy')

    def test_categories_follow_host_and_nycrr_titles(self):
        self.assertEqual(library.category('https://www.ecfr.gov/x'), 'ecfr')
        self.assertEqual(library.category('https://www.law.cornell.edu/cfr/text/18/505.14', '18 NYCRR § 505.14'), 'nyreg')
        self.assertEqual(library.category('https://www.law.cornell.edu/uscode/text/42/1396a'), 'uscode')
        self.assertEqual(library.category('https://codes.findlaw.com/ny/x'), 'nylaw')
        self.assertEqual(library.category('https://www.health.ny.gov/x'), 'nyagency')
        self.assertEqual(library.category('https://example.org/x'), 'other')


class LibraryTests(Fixture):
    def app(self, graph=None):
        app = LocalApp(self.db, self.registry, self.root / 'graph.sqlite', self.root / 'ui.sqlite',
                       model_factory=lambda: ScriptedModel(), budget_path=self.root / 'budget.sqlite')

        @contextmanager
        def source_corpus():
            source = ApprovedCorpus(self.db, self.registry)
            try:
                yield ExpandedCorpus(source, graph or FixtureGraph(source))
            finally:
                source.close()
        app.source_corpus = source_corpus
        return app

    def test_lists_only_approved_documents_without_network(self):
        app = self.app()
        with patch('socket.socket.connect', side_effect=AssertionError('network forbidden')), \
                patch('oracle.aws.aws_client', side_effect=AssertionError('AWS forbidden')):
            listing = app.documents()
            self.assertEqual({item['id'] for item in listing['documents']}, {'doc0', 'doc1'})
            self.assertEqual([c['id'] for c in listing['categories']][:2], ['ecfr', 'uscode'])
            found = app.documents('alpha addendum')
        self.assertEqual([m['id'] for m in found['matches']], ['doc1'])
        self.assertIn(library.HIGHLIGHT_OPEN + 'addendum' + library.HIGHLIGHT_CLOSE, found['matches'][0]['snippet'])

    def test_unapproved_documents_are_hidden_and_unopenable(self):
        graph = FixtureGraph(self.corpus)  # built while both sources are still approved
        policy = json.loads(self.registry.read_text())
        policy['sources'][0]['retrieval_allowed'] = False
        self.registry.write_text(json.dumps(policy))
        app = self.app(graph)
        self.assertEqual([item['id'] for item in app.documents()['documents']], ['doc1'])
        self.assertEqual([m['id'] for m in app.documents('synthetic')['matches']], ['doc1'])
        with self.assertRaises(HarnessError):
            app.document_viewer('doc0')
        with self.assertRaises(HarnessError):
            app.document_viewer('../../etc/passwd')

    def test_query_without_searchable_terms_returns_nothing(self):
        app = self.app()
        self.assertEqual(app.documents('!!! ??')['matches'], [])
        self.assertEqual(app.documents('"; DROP TABLE documents; --')['matches'], [])


class DocumentViewTests(Fixture):
    def doc(self, raw_root, raw_name, data):
        (raw_root / raw_name).write_bytes(data)
        return {'document_id': 'd', 'title': 'Synthetic', 'source_url': 'https://example.org/d', 'text': 'Plain text',
                'raw_path': 'raw/' + raw_name, 'raw_sha256': hashlib.sha256(data).hexdigest()}

    def viewer(self):
        raw_root = self.root / 'raw'
        raw_root.mkdir()
        return CitationViewer(None, raw_root), raw_root

    def test_text_source_opens_whole_without_quote_or_original_pdf(self):
        viewer, raw_root = self.viewer()
        view = viewer.describe_document(self.doc(raw_root, 'd.html', b'<p>Plain text</p>'), None, 'text')
        self.assertEqual((view['kind'], view['quote'], view['text']), ('text', '', 'Plain text'))
        self.assertTrue(view['original_available'])

    def test_pdf_pages_search_matches_and_hash_check(self):
        import fitz
        viewer, raw_root = self.viewer()
        pdf = fitz.open()
        for text in ('First page about hearings', 'Second page mentions a Fair Hearing twice. Fair Hearing.'):
            pdf.new_page().insert_text((72, 100), text)
        data = pdf.tobytes()
        doc = self.doc(raw_root, 'd.pdf', data)
        view = viewer.describe_document(doc, None, 'fair hearing')
        self.assertEqual((view['kind'], view['page_count'], view['find_pages']), ('pdf', 2, [2]))
        self.assertEqual(view['find_count'], 2)
        self.assertEqual(len(view['page_layouts']), 2)
        self.assertNotIn('text', view)
        self.assertEqual(viewer.describe_document(doc, 2, '')['find_pages'], [])
        self.assertEqual(viewer.document_image(doc, 1)[:4], b'\x89PNG')
        with self.assertRaises(HarnessError):
            viewer.describe_document(doc, 3, '')
        with self.assertRaises(HarnessError):
            viewer.document_image(doc, 0)
        doc['raw_sha256'] = '0' * 64  # a changed original is never shown
        changed = viewer.describe_document(doc, None, '')
        self.assertFalse(changed['original_available'])
        self.assertIn('differs', changed['original_note'])
