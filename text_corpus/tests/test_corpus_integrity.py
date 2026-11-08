"""Meaningful correctness checks for quote provenance, parsing, and abstention inputs."""
import importlib.util
import contextlib
import io
import json
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'process_corpus.py'
spec = importlib.util.spec_from_file_location('process_corpus', SCRIPT)
processor = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = processor
spec.loader.exec_module(processor)


class CorpusIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / 'raw').mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def record(self, name, data, media='text/plain', **kw):
        (self.root / 'raw' / name).write_bytes(data)
        return {'source_id': 'source_test', 'title': 'Example rules', 'source_url': 'https://example.org/rules', 'resolved_url': 'https://example.org/rules', 'raw_path': 'raw/' + name, 'media_type': media, 'status': 'downloaded', 'source_origin': 'test_fixture', 'metadata': {}, **kw}

    def test_every_source_character_survives_chunking_and_offsets(self):
        text = ('First paragraph states “may,” not “must.” Café 🏥.\n\n' * 43) + ('Second paragraph includes an exception.\n' * 47) + 'Final condition.'
        record = self.record('rules.txt', text.encode())
        document, sections, chunks = processor.process_record(record, self.root, 400, 60)
        self.assertEqual(document['processing_status'], 'processed')
        self.assertEqual(document['text'], text)
        self.assertGreater(len(chunks), 5)
        processor.validate_record(document, sections, chunks)
        for chunk in chunks:
            self.assertEqual(text[chunk['document_start_char']:chunk['document_end_char']], chunk['text'])
            self.assertLessEqual(len(chunk['text']), 400)

    def test_html_tables_keep_column_labels_and_original_order(self):
        html = b'''<html><head><title>Rules</title></head><body><nav>Site links</nav><main><h1>Appeal timelines</h1><p>Read the final column.</p><table><tr><th>Issue</th><th>Old</th><th>Effective</th></tr><tr><td>Notice</td><td>30 days</td><td>10 days</td></tr></table><h2>Exception</h2><p>Only if <strong>all</strong> conditions apply.</p></main></body></html>'''
        document, sections, chunks = processor.process_record(self.record('rules.html', html, 'text/html'), self.root, 400, 60)
        self.assertEqual(document['processing_status'], 'processed')
        self.assertNotIn('Site links', document['text'])
        self.assertIn('Issue\tOld\tEffective\nNotice\t30 days\t10 days', document['text'])
        self.assertIn('Only if all conditions apply.', document['text'])
        tables = [table for section in sections for table in section['tables']]
        self.assertEqual(tables[0]['headers'], [['Issue', 'Old', 'Effective']])
        self.assertTrue(any(chunk['table_context'] for chunk in chunks))
        processor.validate_record(document, sections, chunks)

    def test_challenge_response_never_enters_corpus(self):
        data = b'<html><head><title>Access Denied</title></head><body><h1>Request blocked</h1><p>Verify you are human</p></body></html>'
        document, sections, chunks = processor.process_record(self.record('blocked.html', data, 'text/html'), self.root, 400, 60)
        self.assertEqual(document['processing_status'], 'excluded_access_page')
        self.assertEqual(sections, [])
        self.assertEqual(chunks, [])

    def test_date_and_supersession_are_evidence_only(self):
        text = 'Date of issuance: April 7, 2026. Effective Date: June 1, 2026. This policy supersedes Policy 24.02.'
        document, sections, chunks = processor.process_record(self.record('date.txt', text.encode(), metadata={'http_last_modified': 'Wed, 08 Apr 2026 01:00:00 GMT'}), self.root, 400, 60)
        self.assertEqual(document['legal_status'], 'not_determined')
        self.assertEqual(document['date_signals']['http_last_modified_raw'], 'Wed, 08 Apr 2026 01:00:00 GMT')
        self.assertEqual(len(document['date_signals']['text_mentions']), 2)
        self.assertTrue(any(signal['matched_text'] == 'supersedes' for signal in document['relationship_signals']))
        self.assertTrue(all(signal['interpretation'] is None for signal in document['relationship_signals']))
        for signal in document['date_signals']['text_mentions'] + document['relationship_signals']:
            self.assertEqual(sections[0]['text'][signal['context_start_char']:signal['context_end_char']], signal['context'])

    def test_pdf_metadata_and_no_text_are_retained_without_ocr(self):
        import fitz
        pdf = fitz.open()
        page = pdf.new_page()
        page.insert_text((72, 72), 'The date must not be inferred from file metadata.')
        pdf.new_page()
        pdf.set_metadata({'title': 'PDF test', 'modDate': 'D:20260102030405Z'})
        document, sections, chunks = processor.process_record(self.record('rules.pdf', pdf.tobytes(), 'application/pdf'), self.root, 400, 60)
        self.assertEqual(document['date_signals']['pdf_modification_date_raw'], 'D:20260102030405Z')
        self.assertEqual(document['legal_status'], 'not_determined')
        self.assertIn('pdf_page_with_little_or_no_extractable_text', document['uncertainty_flags'])
        self.assertFalse(document['extraction']['ocr_used'])
        self.assertEqual(sections[0]['page'], 1)

    def test_pending_and_outside_paths_do_not_process(self):
        document, sections, chunks = processor.process_record({'source_id': 'missing', 'status': 'failed', 'source_url': 'https://example.org/no'}, self.root, 400, 60)
        self.assertEqual(document['processing_status'], 'not_acquired')
        document, sections, chunks = processor.process_record({'source_id': 'outside', 'status': 'downloaded', 'raw_path': '/etc/hosts'}, self.root, 400, 60)
        self.assertEqual(document['processing_status'], 'extraction_failed')
        self.assertEqual(chunks, [])

    def test_sqlite_returns_exact_chunk_and_citation_metadata(self):
        document, sections, chunks = processor.process_record(self.record('query.txt', b'The plan sends a disenrollment notice.'), self.root, 400, 60)
        db = self.root/'corpus.sqlite'
        processor.build_sqlite(db, [document], chunks)
        with sqlite3.connect(db) as con:
            result = con.execute('SELECT f.text,c.metadata_json FROM chunks_fts f JOIN chunks c ON c.chunk_id=f.chunk_id WHERE chunks_fts MATCH ?', ('disenrollment',)).fetchone()
        self.assertEqual(result[0], chunks[0]['text'])
        self.assertEqual(json.loads(result[1])['text_sha256'], chunks[0]['text_sha256'])

    def test_web_capture_is_explicitly_not_original_bytes(self):
        payload = {'title': 'Captured source', 'capture_reference': 'example-ref', 'capture_scope': 'excerpt', 'sections': [{'text': 'This is source language.'}]}
        document, sections, chunks = processor.process_record(self.record('capture.json', json.dumps(payload).encode(), 'application/json', status='web_extracted'), self.root, 400, 60)
        self.assertIn('original_source_bytes_not_downloaded', document['uncertainty_flags'])
        self.assertEqual(document['extraction']['metadata']['capture_scope'], 'excerpt')
        self.assertEqual(chunks[0]['text'], 'This is source language.')


    def test_utf8_and_ecfr_root_do_not_import_navigation(self):
        body = '<html><title>eCFR</title><body><p>Global unrelated navigation</p><div class="part" id="part-438"><h1>PART 438—MANAGED CARE</h1><div class="section" id="438.1"><h4>§ 438.1 Basis</h4><p>The agency’s rule applies.</p></div></div></body></html>'
        document, sections, chunks = processor.process_record(self.record('ecfr.html', body.encode(), 'text/html'), self.root, 400, 60)
        self.assertNotIn('Global unrelated', document['text'])
        self.assertIn('PART 438—MANAGED CARE', document['text'])
        self.assertIn('agency’s', document['text'])
        self.assertEqual(document['extraction']['metadata']['decoded_encoding'], 'utf-8')

    def test_merged_header_spans_and_final_column_have_coordinates(self):
        body = b'<html><body><table><tr><th colspan="3">All periods</th></tr><tr><th>Issue</th><th>Earlier</th><th>Operative column</th></tr><tr><td>Notice</td><td>30 days</td><td>10 days</td></tr></table></body></html>'
        document, sections, chunks = processor.process_record(self.record('table.html', body, 'text/html'), self.root, 400, 60)
        table = sections[0]['tables'][0]
        self.assertEqual(table['column_count'], 3)
        row = table['row_spans'][-1]
        self.assertEqual(row['cells'][-1]['column_start'], 2)
        self.assertEqual(row['cells'][-1]['text'], '10 days')
        for row in table['row_spans']:
            for cell in row['cells']:
                self.assertEqual(sections[0]['text'][cell['start_char']:cell['end_char']], cell['text'])
        self.assertEqual(table['header_cells'][0]['colspan'], 3)

    def test_index_is_retained_but_excluded_from_default_index(self):
        rec = self.record('index.txt', b'Collection lists part 438 but contains no regulations.', source_role='collection_index')
        (self.root / 'manifests').mkdir()
        (self.root / 'manifests' / 'acquisition.jsonl').write_text(json.dumps(rec)+'\n')
        processor.main(['--root', str(self.root)])
        documents = [json.loads(line) for line in (self.root/'processed/documents.jsonl').read_text().splitlines()]
        self.assertEqual(documents[0]['processing_status'], 'index_only')
        self.assertFalse(documents[0]['retrieval_included'])
        self.assertIn('Collection lists', documents[0]['text'])
        self.assertEqual((self.root/'processed/chunks.jsonl').read_text(), '')
        with sqlite3.connect(self.root/'processed/corpus.sqlite') as con:
            self.assertEqual(con.execute('SELECT count(*) FROM chunks_fts').fetchone()[0], 0)

    def test_acquisition_hash_mismatch_fails_closed(self):
        rec = self.record('changed.txt', b'The source changed after acquisition.', sha256='0'*64)
        document, sections, chunks = processor.process_record(rec, self.root, 400, 60)
        self.assertEqual(document['processing_status'], 'extraction_failed')
        self.assertIn('SHA256', document['error'])
        self.assertEqual(chunks, [])

    def test_review_and_origin_metadata_are_retained(self):
        rec = self.record('review.txt', b'This paragraph is evidence awaiting review.',
            source_role='primary_legal_text', brief_reference_ids=['brief-ref-1'],
            source_list_match='pending_original_resource_inventory', review_flags=['human_review_pending'])
        document, sections, chunks = processor.process_record(rec, self.root, 400, 60)
        self.assertEqual(document['source_role'], 'primary_legal_text')
        self.assertEqual(document['acquisition_record']['brief_reference_ids'], ['brief-ref-1'])
        self.assertIn('original_resource_inventory_not_reconciled', chunks[0]['uncertainty_flags'])
        self.assertIn('human_review_pending', chunks[0]['uncertainty_flags'])

    def test_pdf_table_columns_remain_distinct_from_page_flow(self):
        import fitz
        pdf = fitz.open()
        page = pdf.new_page()
        for x in (50, 180, 310, 440):
            page.draw_line((x, 50), (x, 150))
        for y in (50, 100, 150):
            page.draw_line((50, y), (440, y))
        values = [['Issue', 'Earlier', 'Applicable'], ['Notice', '30 days', '10 days']]
        for r, row in enumerate(values):
            for c, value in enumerate(row):
                page.insert_text((60+130*c, 75+50*r), value)
        document, sections, chunks = processor.process_record(self.record('table.pdf', pdf.tobytes(), 'application/pdf'), self.root, 400, 60)
        tables = [section for section in sections if section['kind'] == 'pdf_table_alternate_representation']
        self.assertTrue(tables)
        self.assertEqual(tables[0]['tables'][0]['rows'], values)
        self.assertIn('Notice\t30 days\t10 days', tables[0]['text'])
        self.assertIn('pdf_table_geometry_and_headers_require_review', document['uncertainty_flags'])
        processor.validate_record(document, sections, chunks)

    def test_cornell_rule_and_notes_survive_comparison_panel_removal(self):
        body = b'''<html><body><div id="content"><h1>Section 415.3</h1>
        <ul class="nav nav-tabs"><li>State Regulations</li><li>Compare</li></ul>
        <div id="tab_default_1"><div class="statereg-text"><p>The facility shall protect each resident.</p></div>
        <div class="statereg-notes"><h2>Notes</h2><p>Amended September 23, 2015.</p></div></div>
        <div id="tab_point_in-time"><p>State regulations are updated quarterly.</p><div>No prior version found.</div></div>
        </div></body></html>'''
        document, sections, chunks = processor.process_record(self.record('cornell.html', body, 'text/html'), self.root, 400, 60)
        self.assertIn('The facility shall protect each resident.', document['text'])
        self.assertIn('Amended September 23, 2015.', document['text'])
        self.assertNotIn('Compare', document['text'])
        self.assertNotIn('No prior version found.', document['text'])
        self.assertEqual(document['extraction']['metadata']['source_update_notices'][0]['text'], 'State regulations are updated quarterly.')
        processor.validate_record(document, sections, chunks)

    def test_source_qualifications_reach_chunks_without_legal_inference(self):
        scope = {'status': 'provisional_topic_match_pending_original_curated_inventory',
                 'basis': 'The source labels Article 3 without specifying its subchapter.'}
        rec = self.record('qualified.txt', b'An explicit source paragraph.',
            source_role='regulatory_text_secondary_host', source_host_role='secondary_mirror',
            scope_resolution=scope, requires_human_review=True, match_confidence='medium')
        document, sections, chunks = processor.process_record(rec, self.root, 400, 60)
        self.assertEqual(document['acquisition_metadata']['scope_resolution'], scope)
        self.assertEqual(document['acquisition_metadata']['source_host_role'], 'secondary_mirror')
        for flag in ['secondary_host_requires_primary_source_verification',
                     'provisional_collection_topic_match_requires_human_review',
                     'source_requires_human_review', 'source_match_confidence_medium_requires_review']:
            self.assertIn(flag, chunks[0]['uncertainty_flags'])
        historical = self.record('historical.txt', b'Text from the declared edition.',
            metadata={'edition_year': '2024', 'edition_status': 'historical_edition_not_currentness_verified'})
        document, sections, chunks = processor.process_record(historical, self.root, 400, 60)
        self.assertIn('historical_edition_2024_currentness_not_verified', chunks[0]['uncertainty_flags'])
        self.assertEqual(document['legal_status'], 'not_determined')
        only_old_date = self.record('date_only.txt', b'Issued January 1, 1990.')
        document, sections, chunks = processor.process_record(only_old_date, self.root, 400, 60)
        self.assertFalse(any('historical_edition' in flag for flag in document['uncertainty_flags']))

    def test_collection_kind_excludes_index_without_a_source_role(self):
        rec = self.record('landing.txt', b'A collection of document links.', source_kind='collection_landing')
        document, sections, chunks = processor.process_record(rec, self.root, 400, 60)
        self.assertEqual(document['source_role'], 'collection_index')
        self.assertEqual(document['processing_status'], 'index_only')
        self.assertFalse(document['retrieval_included'])

    def test_publisher_status_stub_does_not_become_a_repeal_judgment(self):
        body = '''<html><body><div id="content"><h1>§§ 415.6 to 415.8 - Repealed</h1>
        <div class="statereg-notes"><h2>Notes</h2><div class="statereg-note">N.Y. Comp. Codes R. &amp; Regs. Tit. 10 § 415.6</div><div class="statereg-note"></div></div>
        <div id="tab_point_in-time"><p>State regulations are updated quarterly.</p><div>No prior version found.</div></div>
        </div></body></html>'''
        document, sections, chunks = processor.process_record(self.record('stub.html', body.encode(), 'text/html'), self.root, 400, 60)
        self.assertIn('Repealed', document['text'])
        self.assertEqual(document['legal_status'], 'not_determined')
        self.assertTrue(document['retrieval_included'])
        self.assertIn('publisher_labeled_status_stub_requires_review', chunks[0]['uncertainty_flags'])
        self.assertIn('publisher_page_has_no_rule_body', chunks[0]['uncertainty_flags'])
        self.assertIsNone(document['extraction']['metadata']['publisher_status_observations'][0]['interpretation'])
        self.assertNotIn('No prior version found.', document['text'])

    def test_bare_citation_placeholder_excluded_but_notes_preserved(self):
        body = '''<html><body><div id="content"><h1>§ 415.6</h1><div class="statereg-notes"><h2>Notes</h2>
        <div class="statereg-note">N.Y. Comp. Codes R. &amp; Regs. Tit. 10 § 415.6</div>{note}</div></div></body></html>'''
        document, sections, chunks = processor.process_record(self.record('empty.html', body.format(note='').encode(), 'text/html'), self.root, 400, 60)
        self.assertEqual(document['processing_status'], 'excluded_no_rule_body_placeholder')
        self.assertFalse(document['retrieval_included'])
        self.assertEqual(document['legal_status'], 'not_determined')
        note = '<div class="statereg-note">Amended September 23, 2015.</div>'
        document, sections, chunks = processor.process_record(self.record('notes.html', body.format(note=note).encode(), 'text/html'), self.root, 400, 60)
        self.assertIn('Amended September 23, 2015.', document['text'])
        self.assertTrue(document['retrieval_included'])
        self.assertIn('publisher_notes_only_no_rule_body_requires_review', document['uncertainty_flags'])

    def test_mixed_encoding_preserves_valid_utf8_and_records_legacy_bytes(self):
        valid = 'The agency’s § rule — Café 🏥. '
        raw = b'<html><head><meta charset="UTF-8"></head><body><main><p>' + valid.encode('utf-8') + b'NYCRR \xa7 85.37; member\x92s notice.</p></main></body></html>'
        record = self.record('mixed.html', raw, 'text/html', http_headers={'Content-Type': 'text/html; charset=UTF-8'})
        document, sections, chunks = processor.process_record(record, self.root, 400, 60)
        self.assertEqual(document['processing_status'], 'processed')
        self.assertIn(valid, document['text'])
        self.assertIn('NYCRR § 85.37; member’s notice.', document['text'])
        self.assertIn('encoding_uncertain', chunks[0]['uncertainty_flags'])
        decoding = document['extraction']['metadata']['decoding']
        self.assertTrue(decoding['declared_charset_decoding_failed'])
        self.assertTrue(decoding['valid_utf8_sequences_preserved'])
        self.assertEqual(decoding['fallback_byte_count'], 2)
        self.assertEqual((self.root/'raw/mixed.html').read_bytes(), raw)
        for item in decoding['fallback_bytes']:
            self.assertEqual(raw[item['byte_offset']], int(item['byte_hex'], 16))
        processor.validate_record(document, sections, chunks)

    def test_valid_utf8_is_unchanged_and_undefined_bytes_are_not_dropped(self):
        value = '§ rules — Café 🏥 and the agency’s notice.'
        decoded, metadata = processor.decode_html_details(value.encode('utf-8'), 'text/html; charset=UTF-8')
        self.assertEqual(decoded, value)
        self.assertEqual(metadata['selected_encoding'], 'utf-8')
        self.assertFalse(metadata['encoding_uncertain'])
        self.assertEqual(metadata['fallback_byte_count'], 0)
        decoded, metadata = processor.decode_html_details(b'Preserve \x81 here.', 'text/html; charset=UTF-8')
        self.assertIn('\x81', decoded)
        self.assertNotIn('\ufffd', decoded)
        self.assertTrue(metadata['encoding_uncertain'])
        self.assertEqual(metadata['fallback_bytes'][0]['mapping'], 'latin-1')

    def test_incremental_repair_reuses_pdf_and_records_mixed_versions(self):
        import fitz
        pdf = fitz.open()
        page = pdf.new_page()
        page.insert_text((72, 72), 'A previously extracted source remains unchanged.')
        good = self.record('healthy.pdf', pdf.tobytes(), 'application/pdf', source_id='healthy')
        raw = b'<html><body><main>A member\x92s policy notice.</main></body></html>'
        bad = self.record('failed.html', raw, 'text/html', source_id='repair_me', http_headers={'Content-Type': 'text/html; charset=UTF-8'})
        manifest = self.root/'manifests/acquisition.jsonl'
        manifest.parent.mkdir()
        manifest.write_text(json.dumps(good)+'\n'+json.dumps(bad)+'\n')
        original_error = UnicodeDecodeError('utf-8', b'\x92', 0, 1, 'invalid start byte')
        with contextlib.redirect_stdout(io.StringIO()), patch.object(processor, 'PROCESSOR_VERSION', 'previous-fixture-version'), patch.object(processor, 'extract_html', side_effect=original_error):
            processor.main(['--root', str(self.root)])
        output = self.root/'processed'
        before = {d['document_id']: d for d in [json.loads(line) for line in (output/'documents.jsonl').read_text().splitlines()]}
        self.assertEqual(before['repair_me']['processing_status'], 'extraction_failed')
        # Prove repair never reopens the unchanged PDF: only canonical records
        # are needed for this path, so the test fixture can remove its raw file.
        (self.root/'raw/healthy.pdf').unlink()
        with contextlib.redirect_stdout(io.StringIO()), patch.object(processor, 'process_record', wraps=processor.process_record) as extractor:
            processor.main(['--root', str(self.root), '--repair-failed'])
        self.assertEqual(extractor.call_count, 1)
        self.assertEqual(extractor.call_args.args[0]['source_id'], 'repair_me')
        after = {d['document_id']: d for d in [json.loads(line) for line in (output/'documents.jsonl').read_text().splitlines()]}
        self.assertEqual(after['healthy'], before['healthy'])
        self.assertEqual(after['repair_me']['processing_status'], 'processed')
        self.assertIn('encoding_uncertain', after['repair_me']['uncertainty_flags'])
        report = json.loads((output/'processing_report.json').read_text())
        self.assertEqual(report['incremental_repair']['reprocessed_source_ids'], ['repair_me'])
        self.assertEqual(report['incremental_repair']['reused_document_count'], 1)
        self.assertEqual(report['document_processor_version_counts'], {'previous-fixture-version': 1, processor.PROCESSOR_VERSION: 1})
        with sqlite3.connect(output/'corpus.sqlite') as con:
            self.assertIn('member’s policy', con.execute("SELECT text FROM chunks_fts WHERE chunks_fts MATCH 'policy'").fetchone()[0])
        changed = {**good, 'title': 'Changed source qualification'}
        manifest.write_text(json.dumps(changed)+'\n'+json.dumps(bad)+'\n')
        with self.assertRaisesRegex(ValueError, 'Acquisition metadata changed'):
            processor.main(['--root', str(self.root), '--repair-failed'])

    def test_customer_inventory_provenance_clears_only_obsolete_inventory_flags(self):
        references = ['customer-link-007', 'customer-link-019']
        record = self.record('customer.txt', b'A customer-listed source paragraph.',
            source_origin='customer_pdf_link', customer_reference_ids=references,
            source_role='regulatory_text_secondary_host',
            review_flags=['original_inventory_missing', 'unresolved_target_requires_review', 'alternate_version_requires_review'],
            metadata={'customer_inventory_status': 'explicitly_listed',
                      'source_list_reconciliation': 'pending_original_linked_inventory',
                      'uncertainty_flags': ['original_resource_inventory_not_reconciled', 'version_confirmation_pending'],
                      'edition_year': '2024', 'edition_status': 'historical_edition_not_currentness_verified'})
        document, sections, chunks = processor.process_record(record, self.root, 400, 60)
        self.assertEqual(document['customer_reference_ids'], references)
        self.assertEqual(document['acquisition_metadata']['customer_reference_ids'], references)
        self.assertEqual(chunks[0]['customer_reference_ids'], references)
        self.assertEqual(chunks[0]['customer_inventory_status'], 'explicitly_listed')
        self.assertEqual(document['inventory_reconciliation']['status'], 'explicitly_listed_in_customer_pdf')
        self.assertNotIn('original_inventory_missing', document['uncertainty_flags'])
        self.assertNotIn('original_resource_inventory_not_reconciled', document['uncertainty_flags'])
        self.assertNotIn('original_resource_inventory_not_reconciled', document['acquisition_metadata']['uncertainty_flags'])
        self.assertIn('version_confirmation_pending', document['acquisition_metadata']['uncertainty_flags'])
        for flag in ['unresolved_target_requires_review', 'alternate_version_requires_review',
                     'secondary_host_requires_primary_source_verification', 'historical_edition_2024_currentness_not_verified']:
            self.assertIn(flag, chunks[0]['uncertainty_flags'])
        self.assertEqual(document['legal_status'], 'not_determined')
        self.assertEqual(document['acquisition_record'], record)

    def test_alternate_sources_do_not_inherit_customer_approval(self):
        record = self.record('alternate.txt', b'An alternate source paragraph.',
            source_origin='alternate_public_source', customer_reference_ids=['customer-link-007'],
            review_flags=['alternate_target_requires_review'],
            metadata={'customer_inventory_status': 'alternate_not_verified',
                      'source_list_reconciliation': 'pending_original_linked_inventory'})
        document, sections, chunks = processor.process_record(record, self.root, 400, 60)
        self.assertIn('alternate_target_requires_review', chunks[0]['uncertainty_flags'])
        self.assertIn('original_resource_inventory_not_reconciled', chunks[0]['uncertainty_flags'])
        self.assertEqual(document['inventory_reconciliation']['status'], 'alternate_not_verified')
        self.assertEqual(document['legal_status'], 'not_determined')

    def test_cornell_uscode_keeps_rule_notes_and_block_boundaries(self):
        body = '''<html><body><div id="content"><small id="titleHelp">Quick search by citation:</small>
        <h1>42 U.S. Code § 1396a</h1><ul class="nav nav-tabs"><li>U.S. Code</li><li>Notes</li></ul>
        <div id="prevnext">prev | next</div><div id="tab_default_1"><text><div class="text"><div class="subsection">
        <span class="num">(a)</span><span class="heading"> State plans</span><span class="chapeau">A State plan must satisfy:</span>
        <div class="paragraph"><span>(1)</span><div class="content">The first condition.</div></div>
        <div class="paragraph"><span>(2)</span><div class="content">The second condition.</div></div></div></div></text></div>
        <div id="tab_default_2"><notes><div class="notes"><p>Effective Date of Amendment</p><p>Source amendment note.</p></div></notes></div>
        <div id="tab_default_3"><table><tr><td>Related authority navigation</td></tr></table></div></div></body></html>'''
        document, sections, chunks = processor.process_record(self.record('usc.html', body.encode(), 'text/html'), self.root, 400, 60)
        text = document['text']
        self.assertIn('(a) State plans\n\nA State plan must satisfy:', text)
        self.assertIn('The first condition.', text)
        self.assertIn('The second condition.', text)
        self.assertIn('Source amendment note.', text)
        self.assertNotIn('Quick search', text)
        self.assertNotIn('prev | next', text)
        self.assertNotIn('Related authority navigation', text)
        self.assertEqual(document['extraction']['metadata']['content_profile'], 'cornell_uscode')
        processor.validate_record(document, sections, chunks)

    def test_findlaw_keeps_version_disclaimer_and_excludes_marketing(self):
        body = '''<html><body><main class="codes-page"><div class="sidebar-with-breadcrumbs-layout__content">
        <h1>New York Code section</h1><p>Current as of January 01, 2026 | Updated by Findlaw Staff</p>
        <div class="codes-content section"><div class="ed-note-kitn">An explicit expiration note.</div><div class="subsection">The legal rule.</div>
        <div class="footnote">Source footnote.</div><div class="codes-controls">Previous Part of Code</div><div class="cite-this-article">Copy citation interface</div></div>
        <p>FindLaw Codes may not reflect the most recent version of the law in your jurisdiction.</p>
        <div class="wasThisHelpful section">Was this helpful?</div></div><section class="fl-resource-section">Unrelated news and attorney advertising.</section></main></body></html>'''
        document, sections, chunks = processor.process_record(self.record('findlaw.html', body.encode(), 'text/html'), self.root, 400, 60)
        for value in ['The legal rule.', 'Source footnote.', 'An explicit expiration note.', 'Current as of January 01, 2026', 'may not reflect the most recent version']:
            self.assertIn(value, document['text'])
        for value in ['Previous Part of Code', 'Copy citation interface', 'Was this helpful?', 'Unrelated news']:
            self.assertNotIn(value, document['text'])
        self.assertEqual(len(document['extraction']['metadata']['publisher_version_notices']), 2)
        self.assertIn('publisher_version_notice_requires_review', chunks[0]['uncertainty_flags'])
        self.assertEqual(document['legal_status'], 'not_determined')
        processor.validate_record(document, sections, chunks)

    def test_multiple_ecfr_parts_are_not_silently_truncated(self):
        body = b'<html><body><nav>Site navigation</nav><div id="part-438" class="part"><h1>Part 438</h1><p>Managed care text.</p></div><div id="part-440" class="part"><h1>Part 440</h1><p>Service text.</p></div></body></html>'
        document, sections, chunks = processor.process_record(self.record('parts.html', body, 'text/html'), self.root, 400, 60)
        self.assertIn('Managed care text.', document['text'])
        self.assertIn('Service text.', document['text'])
        self.assertNotIn('Site navigation', document['text'])
        self.assertIn('multiple_regulatory_parts_require_scope_review', chunks[0]['uncertainty_flags'])

    def test_ecfr_exact_sections_and_subparts_exclude_site_chrome(self):
        section = '<div id="438.54" class="section"><h4>§ 438.54 Managed care enrollment.</h4><p>Actual section text.</p></div>'
        body = '<html><body><p>Site search and title navigation.</p>{content}<p>Publisher account controls.</p></body></html>'
        document, sections, chunks = processor.process_record(self.record('section.html', body.format(content=section).encode(), 'text/html'), self.root, 400, 60)
        self.assertEqual(document['extraction']['metadata']['content_profile'], 'ecfr_section')
        self.assertIn('Actual section text.', document['text'])
        self.assertNotIn('Site search', document['text'])
        self.assertNotIn('Publisher account', document['text'])
        subpart = '<div id="subpart-F" class="subpart"><h2>Subpart F—Grievance and Appeal System</h2>' + section + '<div id="438.402" class="section"><p>The second section.</p></div></div>'
        document, sections, chunks = processor.process_record(self.record('subpart.html', body.format(content=subpart).encode(), 'text/html'), self.root, 400, 60)
        self.assertEqual(document['extraction']['metadata']['content_profile'], 'ecfr_subpart')
        self.assertIn('Subpart F—Grievance and Appeal System', document['text'])
        self.assertIn('Actual section text.', document['text'])
        self.assertIn('The second section.', document['text'])
        self.assertNotIn('Site search', document['text'])
        self.assertNotIn('Publisher account', document['text'])
        processor.validate_record(document, sections, chunks)

if __name__ == '__main__':
    unittest.main()
