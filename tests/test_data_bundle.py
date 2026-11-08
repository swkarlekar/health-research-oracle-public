"""Portable corpus transport checks; all data synthetic and all sockets blocked."""
import hashlib
from contextlib import closing
import json
from pathlib import Path
import shutil
import sqlite3
import stat
import tempfile
import unittest
from unittest.mock import Mock, patch
import zipfile

from oracle.corpus import ApprovedCorpus, HarnessError, base, build_registry
from oracle.data_bundle import (FIXED, MANIFEST, PUBLIC_VECTOR_FIELDS, export_bundle, file_digest,
                                install_bundle, verify_bundle)
from oracle.relationships import GraphStore, build_graph
from oracle.vector_cache import export_vectors


class BundleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / 'source'
        self.processed = self.root / 'text_corpus/processed'
        self.processed.mkdir(parents=True)
        raw = self.root / 'text_corpus/raw'
        raw.mkdir()
        config = self.root / 'config'
        config.mkdir()
        shutil.copy(Path(__file__).resolve().parents[1] / 'config/temporal-policy.json', config)
        self.db = self.processed / 'corpus.sqlite'
        con = sqlite3.connect(self.db)
        con.executescript('CREATE TABLE documents(document_id TEXT PRIMARY KEY,metadata_json TEXT,source_url TEXT);'
                         'CREATE TABLE chunks(chunk_id TEXT PRIMARY KEY,document_id TEXT,metadata_json TEXT);'
                         'CREATE VIRTUAL TABLE chunks_fts USING fts5(chunk_id UNINDEXED,title,heading_path,text);')
        text = 'A synthetic alpha rule. Café 🏥. Unknown reference: Policy 99.99.\n'
        (raw / 'synthetic.txt').write_bytes(text.encode())
        doc = {'document_id': 'doc0', 'source_url': 'https://example.org/source', 'resolved_url': 'https://example.org/source',
               'title': 'Synthetic rule', 'text': text, 'text_sha256': base.digest(text), 'raw_sha256': base.digest(text),
               'raw_path': 'raw/synthetic.txt', 'char_count': len(text), 'source_origin': 'customer_pdf_link',
               'source_role': 'customer_selected_document', 'processing_status': 'processed', 'section_count': 1,
               'acquisition_status': 'downloaded', 'acquisition_metadata': {'customer_inventory_status': 'explicitly_listed'},
               'date_signals': {}}
        section = {'section_id': 'section0', 'section_index': 1, 'document_id': 'doc0', 'text': text,
                   'text_sha256': base.digest(text), 'raw_sha256': base.digest(text), 'document_start_char': 0,
                   'document_end_char': len(text), 'page': None, 'heading_path': [], 'tables': []}
        chunk = {'chunk_id': 'chunk0', 'document_id': 'doc0', 'section_id': 'section0', 'text_sha256': base.digest(text),
                 'raw_sha256': base.digest(text), 'document_start_char': 0, 'document_end_char': len(text),
                 'section_start_char': 0, 'section_end_char': len(text), 'source_url': doc['source_url']}
        con.execute('INSERT INTO documents VALUES(?,?,?)', ('doc0', base.canonical({k: v for k, v in doc.items() if k != 'text'}), doc['source_url']))
        con.execute('INSERT INTO chunks VALUES(?,?,?)', ('chunk0', 'doc0', base.canonical(chunk)))
        con.execute('INSERT INTO chunks_fts VALUES(?,?,?,?)', ('chunk0', doc['title'], '', text))
        con.commit()
        con.close()
        for name, value in (('documents.jsonl', doc), ('sections.jsonl', section), ('chunks.jsonl', chunk)):
            (self.processed / name).write_text(base.canonical(value) + '\n')
        for name in ('corpus_summary.json', 'processing_report.json'):
            (self.processed / name).write_text('{}\n')
        (config / 'sources.json').write_text(base.canonical(build_registry(self.db)))
        corpus = ApprovedCorpus(self.db, config / 'sources.json')
        try:
            build_graph(corpus, self.processed / 'documents.jsonl', self.processed / 'sections.jsonl',
                        self.root / 'artifacts/relationships.sqlite')
            sdk = Mock()
            sdk.list_vectors.return_value = {'vectors': [{'key': 'vector0', 'data': {'float32': [1.0] + [0.0] * 1023},
                'metadata': {'chunk_id': 'chunk0', 'document_id': 'doc0', 'source_url': doc['source_url'],
                             'text_sha256': base.digest(text), 'snapshot_sha256': corpus.summary()['snapshot_sha256'],
                             'AMAZON_BEDROCK_TEXT': text.strip()}}]}
            export_vectors(sdk, 'synthetic-index', self.root / 'artifacts/aws-eval/vector-cache.sqlite', expected_count=1,
                           snapshot=corpus.summary()['snapshot_sha256'], resources={}, ledger=Mock())
        finally:
            corpus.close()
        self.archive = Path(self.temporary.name) / 'data.zip'
        self.network = patch('socket.socket.connect', side_effect=AssertionError('network forbidden'))
        self.network.start()
        self.addCleanup(self.network.stop)

    def mutate(self, change):
        target = self.archive.with_name('mutated.zip')
        with zipfile.ZipFile(self.archive) as source, zipfile.ZipFile(target, 'w') as destination:
            for info in source.infolist():
                name, data = info.filename, source.read(info)
                name, data = change(name, data)
                destination.writestr(name, data)
        return target

    def test_roundtrip_is_portable_idempotent_and_preserves_private_user_files(self):
        manifest = export_bundle(self.root, self.archive)
        relocated = Path(self.temporary.name) / 'different-laptop'
        (relocated / 'artifacts/ui').mkdir(parents=True)
        history = relocated / 'artifacts/ui/history.sqlite'
        history.write_bytes(b'private history')
        result = install_bundle(self.archive, relocated, file_digest(self.archive))
        self.assertEqual(result, manifest)
        self.assertEqual(verify_bundle(self.archive), manifest)
        self.assertEqual(install_bundle(self.archive, relocated), manifest)
        self.assertEqual(history.read_bytes(), b'private history')
        for record in manifest['files']:
            self.assertEqual(file_digest(relocated / record['path']), record['sha256'])

    def test_bundle_omits_credentials_history_queries_logs_and_unreferenced_raw_files(self):
        for name in ('.aws/credentials', '.env', 'artifacts/query-embeddings.sqlite',
                     'artifacts/ui/history.sqlite', 'artifacts/aws-eval/budget.sqlite',
                     'text_corpus/processed/extraction_progress.log', 'text_corpus/raw/credentials.txt'):
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('PRIVATE_SENTINEL')
        manifest = export_bundle(self.root, self.archive)
        self.assertEqual({r['path'] for r in manifest['files']}, {*FIXED, 'text_corpus/raw/synthetic.txt'})
        with zipfile.ZipFile(self.archive) as archive:
            self.assertTrue(all(b'PRIVATE_SENTINEL' not in archive.read(name) for name in archive.namelist()))

    def test_public_vectors_remove_cloud_bindings_without_changing_original_or_float_blobs(self):
        path = self.root / 'artifacts/aws-eval/vector-cache.sqlite'
        private = 'arn:aws:s3vectors:us-east-1:111122223333:bucket/private-index'
        with sqlite3.connect(path) as con:
            metadata = json.loads(con.execute('SELECT metadata FROM vectors').fetchone()[0])
            metadata.update({'x-amz-bedrock-kb-data-source-id': 'PRIVATE_SOURCE_ID',
                             'AMAZON_BEDROCK_METADATA': json.dumps({'source': {'sourceLocation': private}})})
            con.execute('UPDATE vectors SET metadata=?', (base.canonical(metadata),))
            key, data, encoded = con.execute('SELECT key,data,metadata FROM vectors').fetchone()
            manifest = json.loads(con.execute('SELECT json FROM manifest').fetchone()[0])
            manifest['original_resources'] = {'VectorIndexArn': private, 'DataSourceId': 'PRIVATE_SOURCE_ID'}
            manifest['content_sha256'] = hashlib.sha256(base.canonical([key, encoded]).encode() + data).hexdigest()
            con.execute('UPDATE manifest SET json=?', (base.canonical(manifest),))
        original_hash = file_digest(path)
        export_bundle(self.root, self.archive)
        self.assertEqual(file_digest(path), original_hash)
        relocated = Path(self.temporary.name) / 'public'
        install_bundle(self.archive, relocated)
        public_path = relocated / 'artifacts/aws-eval/vector-cache.sqlite'
        self.assertNotIn(private.encode(), public_path.read_bytes())
        self.assertNotIn(b'PRIVATE_SOURCE_ID', public_path.read_bytes())
        with sqlite3.connect(public_path) as con:
            public_key, public_data, encoded = con.execute('SELECT key,data,metadata FROM vectors').fetchone()
            self.assertEqual(public_key, 'chunk0')
            self.assertEqual(public_data, data)
            self.assertEqual(set(json.loads(encoded)), set(PUBLIC_VECTOR_FIELDS))
            self.assertNotIn('original_resources', json.loads(con.execute('SELECT json FROM manifest').fetchone()[0]))

    def test_unavailable_sharing_url_derives_snapshot_and_explicit_historical_review_provenance(self):
        private = 'https://tenant.sharepoint.com/:b:/s/team/PRIVATE_LINK?e=PRIVATE_ACCESS'
        (self.processed / 'corpus_summary.json').write_text(json.dumps({'unavailable_by_listed_host': {'tenant.sharepoint.com': 1}}))
        missing = {'document_id': 'missing', 'title': 'Unavailable source', 'source_url': private,
                   'resolved_url': private, 'processing_status': 'not_acquired', 'text': '',
                   'text_sha256': base.digest(''), 'section_count': 0, 'char_count': 0,
                   'source_origin': 'customer_pdf_link', 'source_role': 'customer_selected_document',
                   'acquisition_metadata': {'url': private}, 'date_signals': {}}
        with sqlite3.connect(self.db) as con:
            con.execute('INSERT INTO documents VALUES(?,?,?)', ('missing', base.canonical({k:v for k,v in missing.items() if k != 'text'}), private))
        with (self.processed / 'documents.jsonl').open('a') as stream:
            stream.write(base.canonical(missing) + '\n')
        registry = self.root / 'config/sources.json'
        registry.write_text(base.canonical(build_registry(self.db)))
        section_path = self.processed / 'sections.jsonl'
        with closing(ApprovedCorpus(self.db, registry)) as corpus:
            graph_path = self.root / 'artifacts/relationships.sqlite'
            graph_path.unlink()
            build_graph(corpus, self.processed / 'documents.jsonl', section_path, graph_path)
            old_snapshot = corpus.summary()['snapshot_sha256']
        with closing(GraphStore(graph_path, old_snapshot)) as graph:
            candidate = graph.connection.execute('SELECT relationship_id FROM relationships LIMIT 1').fetchone()[0]
            old_review = graph.record_review(candidate, {'decision':'uncertain'}, 'synthetic-reviewer')
        vectors = self.root / 'artifacts/aws-eval/vector-cache.sqlite'
        with sqlite3.connect(vectors) as con:
            meta = json.loads(con.execute('SELECT metadata FROM vectors').fetchone()[0]); meta['snapshot_sha256'] = old_snapshot
            con.execute('UPDATE vectors SET metadata=?', (base.canonical(meta),))
            key,data,encoded=con.execute('SELECT key,data,metadata FROM vectors').fetchone()
            manifest=json.loads(con.execute('SELECT json FROM manifest').fetchone()[0]); manifest['snapshot_sha256']=old_snapshot
            manifest['content_sha256']=hashlib.sha256(base.canonical([key,encoded]).encode()+data).hexdigest()
            con.execute('UPDATE manifest SET json=?',(base.canonical(manifest),))
        original_hashes = {name:file_digest(self.root / name) for name in FIXED}
        public = export_bundle(self.root, self.archive)
        self.assertNotEqual(public['bindings']['snapshot_sha256'], old_snapshot)
        self.assertEqual(public['distribution_derivation']['historical_reviews_rebound'], 1)
        self.assertEqual(original_hashes, {name:file_digest(self.root / name) for name in FIXED})
        relocated=Path(self.temporary.name)/'public'; install_bundle(self.archive, relocated)
        for name in FIXED:
            self.assertNotIn(private.encode(), (relocated/name).read_bytes())
            self.assertNotIn(b'tenant.sharepoint.com', (relocated/name).read_bytes())
        summary = json.loads((relocated / 'text_corpus/processed/corpus_summary.json').read_text())
        self.assertEqual(summary['unavailable_by_listed_host'], {'redacted.invalidprovider': 1})
        with closing(GraphStore(relocated/'artifacts/relationships.sqlite', public['bindings']['snapshot_sha256'])) as graph:
            review=json.loads(graph.connection.execute('SELECT record_json FROM reviews').fetchone()[0])
            for field in ('decision','reviewer','actor_type','created_at','candidate_sha256'):
                self.assertEqual(review[field], old_review[field])
            self.assertEqual(review['distribution_derivation']['original_review_id'], old_review['review_id'])
            self.assertFalse(review['distribution_derivation']['new_review_performed'])

    def test_corrupted_file_is_rejected_before_any_destination_mutation(self):
        export_bundle(self.root, self.archive)
        altered = self.mutate(lambda name, data: (name, b'X' * len(data)) if name.endswith('synthetic.txt') else (name, data))
        destination = Path(self.temporary.name) / 'install'
        with self.assertRaisesRegex(HarnessError, 'checksum'):
            install_bundle(altered, destination)
        self.assertEqual(list(destination.iterdir()), [])

    def test_snapshot_mismatch_and_wrong_release_digest_are_rejected(self):
        export_bundle(self.root, self.archive)
        def change(name, data):
            if name == MANIFEST:
                value = json.loads(data)
                value['bindings']['snapshot_sha256'] = 'not-this-corpus'
                data = json.dumps(value).encode()
            return name, data
        with self.assertRaisesRegex(HarnessError, 'semantic bindings'):
            verify_bundle(self.mutate(change))
        with self.assertRaisesRegex(HarnessError, 'pinned release'):
            verify_bundle(self.archive, '0' * 64)

    def test_internally_checksummed_vector_metadata_still_must_match_canonical_sources(self):
        path = self.root / 'artifacts/aws-eval/vector-cache.sqlite'
        with sqlite3.connect(path) as con:
            meta = json.loads(con.execute('SELECT metadata FROM vectors').fetchone()[0])
            meta['source_url'] = 'https://example.org/wrong-source'
            con.execute('UPDATE vectors SET metadata=?', (base.canonical(meta),))
            digest = hashlib.sha256()
            for key, data, metadata in con.execute('SELECT key,data,metadata FROM vectors ORDER BY key'):
                digest.update(base.canonical([key, metadata]).encode())
                digest.update(data)
            manifest = json.loads(con.execute('SELECT json FROM manifest').fetchone()[0])
            manifest['content_sha256'] = digest.hexdigest()
            con.execute('UPDATE manifest SET json=?', (base.canonical(manifest),))
        with self.assertRaisesRegex(HarnessError, 'canonical source'):
            export_bundle(self.root, self.archive)

    def test_unsafe_extra_path_and_symbolic_link_are_rejected(self):
        export_bundle(self.root, self.archive)
        with zipfile.ZipFile(self.archive, 'a') as archive:
            archive.writestr('../escape', 'bad')
        with self.assertRaises(HarnessError):
            verify_bundle(self.archive)
        self.archive.unlink()
        export_bundle(self.root, self.archive)
        with zipfile.ZipFile(self.archive) as source:
            records = [(info, source.read(info)) for info in source.infolist()]
        self.archive.unlink()
        with zipfile.ZipFile(self.archive, 'w') as archive:
            for info, data in records:
                if info.filename.endswith('synthetic.txt'):
                    info.external_attr = (stat.S_IFLNK | 0o777) << 16
                archive.writestr(info, data)
        with self.assertRaisesRegex(HarnessError, 'links'):
            verify_bundle(self.archive)

    def test_install_never_overwrites_changed_data_or_escaped_destination(self):
        export_bundle(self.root, self.archive)
        destination = Path(self.temporary.name) / 'install'
        target = destination / 'config/sources.json'
        target.parent.mkdir(parents=True)
        target.write_text('existing data')
        with self.assertRaisesRegex(HarnessError, 'Existing data differs'):
            install_bundle(self.archive, destination)
        self.assertEqual(target.read_text(), 'existing data')
        target.unlink()
        external = Path(self.temporary.name) / 'external'
        external.mkdir()
        target.parent.rmdir()
        target.parent.symlink_to(external, target_is_directory=True)
        with self.assertRaisesRegex(HarnessError, 'escapes'):
            install_bundle(self.archive, destination)
        self.assertEqual(list(external.iterdir()), [])

    def test_truncated_archive_and_symlinked_source_are_rejected(self):
        export_bundle(self.root, self.archive)
        self.archive.write_bytes(self.archive.read_bytes()[:100])
        with self.assertRaises(HarnessError):
            verify_bundle(self.archive)
        self.archive.unlink()
        raw = self.root / 'text_corpus/raw/synthetic.txt'
        copy = raw.with_name('copy.txt')
        shutil.copy(raw, copy)
        raw.unlink()
        raw.symlink_to(copy)
        with self.assertRaises(HarnessError):
            export_bundle(self.root, self.archive)
