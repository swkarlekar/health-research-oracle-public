"""Versioned, checksum-verified corpus transport; no credentials or model calls."""
import argparse
from contextlib import ExitStack, closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import stat
import tempfile
import zipfile
from urllib.parse import urlsplit

from .corpus import ApprovedCorpus, HarnessError, ROOT, base
from .context import ExpandedCorpus
from .local_vectors import LocalVectorIndex
from .relationships import GraphStore, _base_payload
from .vector_cache import verify_cache
from .viewer import CitationViewer

MANIFEST = 'oracle-data-manifest.json'
PROCESSED = ('corpus.sqlite', 'documents.jsonl', 'sections.jsonl', 'chunks.jsonl',
             'corpus_summary.json', 'processing_report.json')
FIXED = tuple('text_corpus/processed/' + name for name in PROCESSED) + (
    'artifacts/relationships.sqlite', 'artifacts/aws-eval/vector-cache.sqlite',
    'config/sources.json', 'config/temporal-policy.json')
# Optional retrieval aid for the research pipeline (oracle/paragraphs.py). Bundles made before it existed
# remain valid; when present it is verified against the same canonical snapshot as everything else.
PARAGRAPH_PATH = 'artifacts/aws-eval/paragraph-vectors.sqlite'
OPTIONAL = (PARAGRAPH_PATH,)
MAX_BYTES, MAX_FILES = 2 * 1024**3, 10000
VECTOR_PATH = 'artifacts/aws-eval/vector-cache.sqlite'
PUBLIC_VECTOR_FIELDS = ('AMAZON_BEDROCK_TEXT', 'chunk_id', 'document_id',
                        'snapshot_sha256', 'source_url', 'text_sha256')


def file_digest(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def allowed_path(name):
    path = PurePosixPath(name)
    if (not isinstance(name, str) or '\\' in name or ':' in name or path.is_absolute()
            or any(part in ('', '.', '..') for part in name.split('/'))):
        return False
    return name in FIXED or name in OPTIONAL or (len(path.parts) == 3 and path.parts[:2] == ('text_corpus', 'raw')
                             and not path.name.startswith('.') and path.suffix.lower() in ('.pdf', '.html', '.htm', '.txt'))


def validate_data(root, vector_cache=None):
    """Check snapshot/graph/vector binding and every graph source's original bytes."""
    root = Path(root)
    with ExitStack() as stack:
        corpus = ApprovedCorpus(root / 'text_corpus/processed/corpus.sqlite', root / 'config/sources.json')
        stack.callback(corpus.close)
        snapshot = corpus.summary()['snapshot_sha256']
        graph = GraphStore(root / 'artifacts/relationships.sqlite', snapshot)
        stack.callback(graph.close)
        index = LocalVectorIndex(vector_cache or root / VECTOR_PATH)
        if index.manifest['snapshot_sha256'] != snapshot:
            raise HarnessError('Data bundle vectors and corpus snapshots differ')
        if len(index.rows) != corpus.summary()['chunk_count']:
            raise HarnessError('Data bundle vector count does not match the canonical chunks')
        expanded = ExpandedCorpus(corpus, graph)
        viewer = CitationViewer(expanded, raw_root=root / 'text_corpus/raw')
        originals, first_pdf = 0, None
        for row in graph.connection.execute('SELECT record_json FROM documents'):
            doc = json.loads(row[0])
            if not doc.get('raw_path'):
                continue
            raw = PurePosixPath(doc['raw_path'])
            if raw.is_absolute() or '..' in raw.parts or not raw.parts or raw.parts[0] != 'raw':
                raise HarnessError('Data bundle contains a nonportable original-source path')
            path = root / 'text_corpus' / raw
            if not path.is_file() or path.is_symlink() or file_digest(path) != doc.get('raw_sha256'):
                raise HarnessError('Data bundle original source is missing or differs from its canonical hash')
            originals += 1
            if first_pdf is None and raw.suffix.lower() == '.pdf' and doc.get('processing_status') == 'processed':
                first_pdf = doc
        # Read FTS text once. One lookup per vector would repeatedly scan the
        # unindexed canonical chunk-ID column and needlessly amplify disk reads.
        canonical = {row['chunk_id']: row for row in corpus.connection.execute(
            "SELECT c.chunk_id,c.document_id,json_extract(c.metadata_json,'$.text_sha256') AS text_sha256,"
            "json_extract(d.metadata_json,'$.source_url') AS source_url,"
            "json_extract(d.metadata_json,'$.raw_sha256') AS raw_sha256, f.text "
            'FROM chunks_fts f JOIN chunks c ON c.chunk_id=f.chunk_id '
            'JOIN documents d ON d.document_id=c.document_id')}
        for _, meta, _, _ in index.rows:
            item = canonical.get(meta['chunk_id'])
            if item is None or any(meta[key] != item[key] for key in ('document_id', 'text_sha256', 'source_url')):
                raise HarnessError('Data bundle vector identity differs from its canonical source')
            policy = corpus.policy.get(item['document_id'], {})
            if (policy.get('retrieval_allowed') is not True or item['raw_sha256'] != policy.get('raw_sha256')
                    or item['source_url'] != policy.get('source_url') or base.digest(item['text']) != item['text_sha256']):
                raise HarnessError('Data bundle vector source is outside its pinned canonical registry')
            if base.digest(item['text'].strip()) != meta['managed_text_sha256']:
                raise HarnessError('Data bundle managed vector text differs from its canonical source')
        pdf_check = None
        if first_pdf:
            row = corpus.connection.execute('SELECT chunk_id FROM chunks WHERE document_id=? ORDER BY chunk_id LIMIT 1',
                                            (first_pdf['document_id'],)).fetchone()
            item = corpus.chunk(row[0])
            result = {'quotes': [{'chunk_id': item['chunk_id'], 'start': 0, 'end': min(200, len(item['text'])),
                                  'text': item['text'][:200]}]}
            description = viewer.describe(result, 0)
            if description['kind'] != 'pdf' or not description['original_available']:
                raise HarnessError('Relocated original PDF cannot be resolved by the citation viewer')
            pdf_check = {'document_id': first_pdf['document_id'], 'page_count': description['page_count'],
                         'page': description['page'], 'original_available': True}
        summary = graph.summary()
        result = {'snapshot_sha256': snapshot, 'document_count': corpus.summary()['document_count'],
                'chunk_count': corpus.summary()['chunk_count'], 'original_sources_verified': originals,
                'graph_content_sha256': summary['graph_content_sha256'],
                'relationship_count': summary['relationship_count'],
                'vector_count': len(index.rows), 'vector_content_sha256': index.manifest['content_sha256'],
                'embedding_model_id': index.manifest['embedding_model_id'], 'dimensions': index.manifest['dimensions'],
                'distance_metric': index.manifest['distance_metric'], 'relocated_pdf_check': pdf_check}
        if index.manifest.get('embedding_values_sha256'):
            values_digest = hashlib.sha256()
            with closing(sqlite3.connect(f'{Path(vector_cache or root / VECTOR_PATH).resolve().as_uri()}?mode=ro', uri=True)) as cache:
                for key, data in cache.execute('SELECT key,data FROM vectors ORDER BY key'):
                    values_digest.update(key.encode() + b'\0' + data)
            if values_digest.hexdigest() != index.manifest['embedding_values_sha256']:
                raise HarnessError('Data bundle embedding values digest differs')
            result['embedding_values_sha256'] = index.manifest['embedding_values_sha256']
        if (root / PARAGRAPH_PATH).is_file():
            result['paragraph_vectors'] = _validate_paragraphs(root / PARAGRAPH_PATH, corpus, snapshot)
        return result


def _validate_paragraphs(path, corpus, snapshot):
    """Every stored paragraph must be exactly the splitter's span of a retrievable canonical chunk."""
    from .paragraphs import MODEL, SPLITTER_VERSION, paragraph_rows
    with closing(sqlite3.connect(f'{Path(path).resolve().as_uri()}?mode=ro', uri=True)) as con:
        manifest = json.loads(con.execute('SELECT value FROM manifest').fetchone()[0])
        stored = con.execute('SELECT chunk_id, start, end, length(data) FROM vectors ORDER BY rowid').fetchall()
        digest = hashlib.sha256()
        for (data,) in con.execute('SELECT data FROM vectors ORDER BY rowid'):
            digest.update(data)
    dims = manifest.get('dimensions')
    expected = [(c, a, b) for c, a, b, _, _, _ in paragraph_rows(corpus)]
    if (manifest.get('snapshot_sha256') != snapshot or manifest.get('embedding_model_id') != MODEL
            or manifest.get('splitter') != SPLITTER_VERSION or type(dims) is not int
            or manifest.get('vector_count') != len(stored) or digest.hexdigest() != manifest.get('content_sha256')
            or [(c, a, b) for c, a, b, _ in stored] != expected or any(size != dims * 2 for _, _, _, size in stored)):
        raise HarnessError('Data bundle paragraph vectors do not match the canonical corpus snapshot')
    return {'vector_count': len(stored), 'dimensions': dims, 'content_sha256': manifest['content_sha256'],
            'embedding_model_id': MODEL, 'splitter': SPLITTER_VERSION}


def _distribution_vectors(source, destination, snapshot_sha256=None):
    """Rebuild fresh pages: deleting SQLite fields alone can leave old bytes behind.

    Preserve every float32 blob and canonical source field. Managed ingestion
    identifiers are unnecessary for local retrieval and never enter this copy.
    The original backup remains unchanged for private experiment/restore use.
    """
    original = verify_cache(source)
    manifest = {key: original[key] for key in ('format', 'embedding_model_id', 'dimensions',
        'distance_metric', 'vector_count', 'snapshot_sha256')}
    if snapshot_sha256:
        manifest['snapshot_sha256'] = snapshot_sha256
    # Keep the mixed-cache audit without copying private execution/resource data.
    # New direct Titan vectors do not represent managed Knowledge Base sync state.
    if original.get('provenance_version') == 1:
        if original.get('normalize') is not True:
            raise HarnessError('Incremental Titan provenance must declare normalized embeddings')
        counts = original.get('vector_origin_counts', {})
        if (set(counts) != {'reused_managed_export', 'direct_titan_incremental'}
                or any(type(n) is not int or n < 0 for n in counts.values())
                or sum(counts.values()) != manifest['vector_count']):
            raise HarnessError('Vector origin counts do not match the cache')
        provenance = original.get('embedding_provenance', {})
        hashes = {key: provenance.get(key) for key in
                  ('baseline_snapshot_sha256', 'baseline_embedding_values_sha256')}
        if any(not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{64}', value)
               for value in hashes.values()):
            raise HarnessError('Vector provenance lacks a valid baseline checksum')
        manifest.update(provenance_version=1, normalize=True, vector_origin_counts=counts,
            embedding_provenance={**hashes, 'kind': 'mixed_managed_export_and_direct_titan',
                'old_values_preserved': provenance.get('old_values_preserved') is True,
                'public_subset_derived_without_reembedding':
                    provenance.get('public_subset_derived_without_reembedding') is True,
                'managed_knowledge_base_sync_equivalent': False})
    with closing(sqlite3.connect(f'{Path(source).resolve().as_uri()}?mode=ro', uri=True)) as incoming, \
            closing(sqlite3.connect(destination)) as outgoing:
        outgoing.executescript('CREATE TABLE vectors(key TEXT PRIMARY KEY,data BLOB NOT NULL,metadata TEXT NOT NULL);'
                               'CREATE TABLE manifest(json TEXT NOT NULL);')
        for _, data, metadata in incoming.execute('SELECT key,data,metadata FROM vectors ORDER BY key'):
            source_meta = json.loads(metadata)
            if any(not isinstance(source_meta.get(key), str) for key in PUBLIC_VECTOR_FIELDS):
                raise HarnessError('Vector cache lacks canonical public source metadata')
            public_meta = {key: source_meta[key] for key in PUBLIC_VECTOR_FIELDS}
            public_meta['snapshot_sha256'] = manifest['snapshot_sha256']
            outgoing.execute('INSERT INTO vectors VALUES(?,?,?)',
                             (public_meta['chunk_id'], data, base.canonical(public_meta)))
        digest, values_digest = hashlib.sha256(), hashlib.sha256()
        for key, data, metadata in outgoing.execute('SELECT key,data,metadata FROM vectors ORDER BY key'):
            digest.update(base.canonical([key, metadata]).encode())
            digest.update(data)
            values_digest.update(key.encode() + b'\0' + data)
        manifest.update(content_sha256=digest.hexdigest(),
            embedding_values_sha256=values_digest.hexdigest(),
            non_filterable_metadata_keys=['AMAZON_BEDROCK_TEXT'],
            distribution_metadata='canonical-source-fields-only-v1',
            restore_limit='Local retrieval copy. Managed source bindings were removed and must be reconstructed for cloud ingestion.')
        outgoing.execute('INSERT INTO manifest VALUES(?)', (base.canonical(manifest),))
        outgoing.commit()
    return verify_cache(destination)


def _redact_unavailable_links(root):
    """Derive a public snapshot without changing available source text or decisions.

    Only sharing URLs of empty, unacquired sources can be replaced automatically.
    Historical review decisions are explicitly rebound, never represented as new
    human/model reviews. SQLite VACUUM removes superseded private metadata pages.
    """
    corpus_path = root / 'text_corpus/processed/corpus.sqlite'
    replacements, changed = {}, {}
    with closing(sqlite3.connect(corpus_path)) as corpus:
        corpus.execute('PRAGMA secure_delete=ON')
        for identifier, metadata in corpus.execute('SELECT document_id,metadata_json FROM documents').fetchall():
            doc = json.loads(metadata)
            host = (urlsplit(doc.get('source_url') or '').hostname or '').lower()
            if not (host.endswith('.sharepoint.com') or host in ('sharepoint.com', 'onedrive.live.com', '1drv.ms')):
                continue
            if (doc.get('processing_status') != 'not_acquired' or doc.get('raw_path')
                    or corpus.execute('SELECT COUNT(*) FROM chunks WHERE document_id=?', (identifier,)).fetchone()[0]):
                raise HarnessError('A sharing URL belongs to acquired content; it requires manual publication review')
            old = doc['source_url']
            replacement = 'https://example.invalid/redacted-source/' + identifier
            replacements[old] = replacement
            encoded = metadata.replace(old, replacement)
            doc = json.loads(encoded)
            doc['distribution_redaction'] = {'reason': 'Unavailable sharing URL withheld from public distribution',
                                            'source_content_changed': False}
            changed[identifier] = doc
            corpus.execute('UPDATE documents SET metadata_json=?,source_url=? WHERE document_id=?',
                           (base.canonical(doc), replacement, identifier))
        if not replacements:
            return {'redacted_unavailable_source_ids': [], 'historical_reviews_rebound': 0}
        corpus.commit()
        corpus.execute('VACUUM')
    with closing(base.Corpus(corpus_path)) as corpus:
        snapshot = corpus.summary()['snapshot_sha256']
    documents = root / 'text_corpus/processed/documents.jsonl'
    rows = []
    with documents.open(encoding='utf-8') as stream:
        for line in stream:
            value = json.loads(line)
            identifier = value.get('document_id')
            if identifier in changed:
                # Text is empty for these unavailable sources; preserve that field.
                value = {**changed[identifier], **({'text': value['text']} if 'text' in value else {})}
                line = base.canonical(value) + '\n'
            rows.append(line)
    documents.write_text(''.join(rows), encoding='utf-8')
    registry_path = root / 'config/sources.json'
    registry = base.load_json(registry_path)
    original_snapshot = registry['snapshot_sha256']
    for source in registry['sources']:
        if source['document_id'] in changed:
            source['source_url'] = changed[source['document_id']]['source_url']
    registry['snapshot_sha256'] = snapshot
    registry_path.write_text(json.dumps(registry, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    summary_path = root / 'text_corpus/processed/corpus_summary.json'
    summary = base.load_json(summary_path)
    unavailable_hosts = summary.get('unavailable_by_listed_host', {})
    for host in {urlsplit(value).hostname for value in replacements}:
        if host in unavailable_hosts:
            withheld = unavailable_hosts.pop(host)
            unavailable_hosts['redacted.invalidprovider'] = unavailable_hosts.get('redacted.invalidprovider', 0) + withheld
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    with closing(sqlite3.connect(root / 'artifacts/relationships.sqlite')) as graph:
        graph.execute('PRAGMA secure_delete=ON')
        # This is a derived distribution database, never the original ledger.
        # Restore its exact append-only guard after recording the derivation.
        trigger = graph.execute("SELECT sql FROM sqlite_master WHERE type='trigger' AND name='reviews_no_update'").fetchone()
        if trigger:
            graph.execute('DROP TRIGGER reviews_no_update')
        for identifier, doc in changed.items():
            row = graph.execute('SELECT record_json FROM documents WHERE document_id=?', (identifier,)).fetchone()
            original_doc = json.loads(row[0])
            if original_doc.get('text', ''):
                raise HarnessError('A withheld sharing URL unexpectedly has canonical graph text')
            graph.execute('UPDATE documents SET record_json=? WHERE document_id=?',
                          (base.canonical({**doc, 'text': original_doc.get('text', '')}), identifier))
        values = {table: {row[0]: json.loads(row[1]) for row in graph.execute(f'SELECT {key},record_json FROM {table}')}
                  for table, key in (('documents', 'document_id'), ('sections', 'section_id'), ('relationships', 'relationship_id'))}
        graph_base = base.digest(base.canonical(_base_payload(values['documents'], values['sections'], values['relationships'])))
        for key, value in (('snapshot_sha256', snapshot), ('graph_base_sha256', graph_base)):
            graph.execute('UPDATE metadata SET value=? WHERE key=?', (base.canonical(value), key))
        previous, count = None, 0
        for sequence, encoded in graph.execute('SELECT sequence,record_json FROM reviews ORDER BY sequence').fetchall():
            record = json.loads(encoded)
            original_review = record['review_id']
            record.update(snapshot_sha256=snapshot, previous_review_sha256=previous,
                distribution_derivation={'kind': 'unavailable-source-link-redaction',
                    'original_snapshot_sha256': original_snapshot, 'original_review_id': original_review,
                    'original_review_sha256': base.digest(encoded), 'new_review_performed': False})
            record.pop('review_id')
            record['review_id'] = 'review_' + base.digest(base.canonical(record))
            rebound = base.canonical(record)
            graph.execute('UPDATE reviews SET record_json=? WHERE sequence=?', (rebound, sequence))
            previous, count = base.digest(rebound), count + 1
        if trigger:
            graph.execute(trigger[0])
        graph.commit()
        graph.execute('VACUUM')
    # Refuse automatic distribution if a sharing link also occurs in available
    # source bytes, another document, or evidence we have not explicitly derived.
    needles = [value.encode() for value in replacements]
    needles += [urlsplit(value).hostname.encode() for value in replacements]
    overlap = max(map(len, needles)) - 1
    for path in root.rglob('*'):
        if not path.is_file():
            continue
        previous = b''
        with path.open('rb') as stream:
            for data in iter(lambda: stream.read(1024 * 1024), b''):
                combined = previous + data
                if any(needle in combined for needle in needles):
                    raise HarnessError('A withheld sharing URL remains outside its reviewed metadata locations')
                previous = combined[-overlap:] if overlap else b''
    return {'redacted_unavailable_source_ids': sorted(changed), 'source_snapshot_sha256': original_snapshot,
            'snapshot_sha256': snapshot, 'historical_reviews_rebound': count,
            'unavailable_source_hostnames_withheld': True,
            'source_text_and_quote_offsets_changed': False, 'review_decisions_changed': False,
            'document_embeddings_regenerated': False}


def export_bundle(root, destination):
    root, destination = Path(root).resolve(), Path(destination).resolve()
    if destination.exists():
        raise HarnessError('Bundle already exists; choose a new versioned output path')
    paths = [root / name for name in FIXED] + [root / name for name in OPTIONAL if (root / name).is_file()]
    # Only originals referenced by the pinned graph are distributable. Unrelated
    # files dropped into raw/ (or obsolete downloads) are not silently included.
    with closing(sqlite3.connect(f'{(root / "artifacts/relationships.sqlite").as_uri()}?mode=ro', uri=True)) as graph:
        raw_paths = {json.loads(row[0]).get('raw_path') for row in graph.execute('SELECT record_json FROM documents')}
    paths += [root / 'text_corpus' / name for name in sorted(p for p in raw_paths if p)]
    records = []
    for path in paths:
        name = path.relative_to(root).as_posix()
        if (not allowed_path(name) or path.is_symlink() or not path.is_file()
                or not path.resolve().is_relative_to(root)
                or any(parent.is_symlink() for parent in path.parents if parent.is_relative_to(root))):
            raise HarnessError('Unexpected file in the selected data bundle: ' + name)
        if path.name.endswith('.sqlite') and any(Path(str(path) + suffix).exists() for suffix in ('-wal', '-journal')):
            raise HarnessError('A data database has pending writes; close its writer before bundling')
        records.append({'path': name, 'bytes': path.stat().st_size, 'sha256': file_digest(path)})
    if len(records) > MAX_FILES or sum(r['bytes'] for r in records) > MAX_BYTES:
        raise HarnessError('Data bundle exceeds the reviewed transport limits')
    original_hashes = {record['path']: record['sha256'] for record in records}
    source_bindings = validate_data(root)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + '.partial')
    if temporary.exists():
        raise HarnessError('An incomplete bundle exists; review it before retrying')
    try:
        with tempfile.TemporaryDirectory(prefix='.oracle-public-data-', dir=destination.parent) as directory:
            public_root = Path(directory)
            for record in records:
                if record['path'] == VECTOR_PATH:
                    continue
                path = public_root / record['path']
                path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(root / record['path'], path)
            derivation = _redact_unavailable_links(public_root)
            public_vectors = public_root / VECTOR_PATH
            public_vectors.parent.mkdir(parents=True, exist_ok=True)
            _distribution_vectors(root / VECTOR_PATH, public_vectors, derivation.get('snapshot_sha256'))
            public_paragraphs = public_root / PARAGRAPH_PATH
            if public_paragraphs.is_file() and derivation.get('snapshot_sha256') != derivation.get('source_snapshot_sha256'):
                # Redaction changes only unacquired sources' metadata, never chunk text or offsets; rebind the
                # paragraph index to the public snapshot identity exactly as the chunk vectors are.
                with closing(sqlite3.connect(public_paragraphs)) as con:
                    value = json.loads(con.execute('SELECT value FROM manifest').fetchone()[0])
                    value['snapshot_sha256'] = derivation['snapshot_sha256']
                    con.execute('UPDATE manifest SET value=?', (json.dumps(value),))
                    con.commit()
                    con.execute('VACUUM')
            for record in records:
                path = public_root / record['path']
                record.update(bytes=path.stat().st_size, sha256=file_digest(path))
            bindings = validate_data(public_root)
            manifest = {'schema_version': 1, 'kind': 'oracle-data-only',
                        'created_at_utc': datetime.now(timezone.utc).isoformat(),
                        'bindings': bindings, 'file_count': len(records), 'total_bytes': sum(r['bytes'] for r in records),
                        'files': records, 'distribution_metadata': 'canonical-source-fields-only-v1',
                        'distribution_derivation': {**derivation,
                            'source_graph_content_sha256': source_bindings['graph_content_sha256']},
                        'excluded': ['AWS credentials and profiles', 'AWS account and managed resource identifiers',
                                     'approval grants and budgets', 'chat history', 'query embedding caches',
                                     'evaluation answers', 'LLM weights', 'application code'],
                        'currentness_note': 'Snapshot identity is not a certification of legal applicability or currency.'}
            with zipfile.ZipFile(temporary, 'w', compression=zipfile.ZIP_STORED) as archive:
                for record in records:
                    archive.write(public_root / record['path'], record['path'])
                archive.writestr(MANIFEST, json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
            # Detect source edits without mistaking the sanitized copy for a mutation.
            for name, digest in original_hashes.items():
                if file_digest(root / name) != digest:
                    raise HarnessError('Data changed while bundling; incomplete output was discarded')
            if temporary.stat().st_size >= MAX_BYTES:
                raise HarnessError('The archive exceeds the GitHub release-asset size limit')
            temporary.replace(destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return manifest


def _extract_verified(archive_path, staging):
    try:
        with zipfile.ZipFile(archive_path) as archive:
            infos = archive.infolist()
            names = [info.filename for info in infos]
            if len(infos) > MAX_FILES + 1 or len(names) != len(set(names)) or MANIFEST not in names:
                raise HarnessError('Data archive has missing or duplicate manifest/files')
            info = archive.getinfo(MANIFEST)
            if info.file_size > 2_000_000:
                raise HarnessError('Data manifest exceeds the size limit')
            manifest = json.loads(archive.read(info))
            if manifest.get('schema_version') != 1 or manifest.get('kind') != 'oracle-data-only':
                raise HarnessError('Unsupported data bundle format')
            records = manifest.get('files')
            if not isinstance(records, list) or not 1 <= len(records) <= MAX_FILES:
                raise HarnessError('Data manifest has an invalid file inventory')
            expected = {}
            total = 0
            for record in records:
                name, size, digest = record.get('path'), record.get('bytes'), record.get('sha256')
                if (not isinstance(name, str) or not allowed_path(name) or name in expected
                        or type(size) is not int or size < 0 or not isinstance(digest, str)
                        or not re.fullmatch(r'[0-9a-f]{64}', digest)):
                    raise HarnessError('Data manifest contains an unsafe or malformed file record')
                expected[name] = record
                total += size
            if (set(names) != {*expected, MANIFEST} or not set(FIXED).issubset(expected)
                    or total > MAX_BYTES or manifest.get('total_bytes') != total or manifest.get('file_count') != len(records)):
                raise HarnessError('Data archive inventory or size does not match its manifest')
            for info in infos:
                if info.is_dir() or stat.S_ISLNK(info.external_attr >> 16) or info.flag_bits & 1:
                    raise HarnessError('Data archive contains unsupported links, directories, or encryption')
                if info.filename == MANIFEST:
                    continue
                record = expected[info.filename]
                if info.file_size != record['bytes']:
                    raise HarnessError('Data archive file size differs from its manifest')
                target = staging / info.filename
                target.parent.mkdir(parents=True, exist_ok=True)
                digest, written = hashlib.sha256(), 0
                with archive.open(info) as source, target.open('xb') as output:
                    for chunk in iter(lambda: source.read(1024 * 1024), b''):
                        written += len(chunk)
                        if written > record['bytes']:
                            raise HarnessError('Data archive expands beyond its declared size')
                        digest.update(chunk)
                        output.write(chunk)
                if written != record['bytes'] or digest.hexdigest() != record['sha256']:
                    raise HarnessError('Data archive checksum mismatch')
            if validate_data(staging) != manifest.get('bindings'):
                raise HarnessError('Data bundle snapshot or semantic bindings do not match its manifest')
            return manifest
    except HarnessError:
        raise
    except (zipfile.BadZipFile, KeyError, TypeError, ValueError) as exc:
        raise HarnessError('Data archive or manifest is malformed or corrupted') from exc


def verify_bundle(archive, expected_sha256=None):
    if expected_sha256 and file_digest(archive) != expected_sha256:
        raise HarnessError('Downloaded archive checksum differs from the pinned release')
    with tempfile.TemporaryDirectory(prefix='oracle-data-verify-') as temporary:
        return _extract_verified(archive, Path(temporary))


def install_bundle(archive, destination=ROOT, expected_sha256=None):
    """Verify everything first; preserve existing data and all unrelated user files."""
    if expected_sha256 and file_digest(archive) != expected_sha256:
        raise HarnessError('Downloaded archive checksum differs from the pinned release')
    destination = Path(destination).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.oracle-data-', dir=destination) as temporary:
        staging = Path(temporary)
        manifest = _extract_verified(archive, staging)
        for record in manifest['files']:
            path = destination / record['path']
            if not path.resolve().is_relative_to(destination):
                raise HarnessError('Existing destination path escapes the installation directory')
            if path.exists() and (path.is_symlink() or not path.is_file() or file_digest(path) != record['sha256']):
                raise HarnessError('Existing data differs; install into a fresh checkout to preserve it: ' + record['path'])
        created = []
        try:
            for record in manifest['files']:
                path = destination / record['path']
                if path.exists():
                    continue
                path.parent.mkdir(parents=True, exist_ok=True)
                # Link only a fully verified staged file; exclusive creation avoids
                # replacing a file another process created after the preflight.
                os.link(staging / record['path'], path)
                created.append(path)
            marker = destination / MANIFEST
            if marker.is_symlink():
                raise HarnessError('The installation marker must not be a symbolic link')
            temp_marker = staging / MANIFEST
            temp_marker.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
            temp_marker.replace(marker)
        except Exception:
            for path in reversed(created):
                path.unlink(missing_ok=True)
            raise
        return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    export = commands.add_parser('export', help='Build a data-only archive from an existing corpus')
    export.add_argument('--root', type=Path, default=ROOT)
    export.add_argument('--output', type=Path, required=True)
    for name in ('verify', 'install'):
        command = commands.add_parser(name)
        command.add_argument('archive', type=Path)
        command.add_argument('--sha256')
        if name == 'install':
            command.add_argument('--root', type=Path, default=ROOT)
    args = parser.parse_args(argv)
    try:
        if args.command == 'export':
            result = export_bundle(args.root, args.output)
        elif args.command == 'verify':
            result = verify_bundle(args.archive, args.sha256)
        else:
            result = install_bundle(args.archive, args.root, args.sha256)
        print(json.dumps({'command': args.command, 'file_count': result['file_count'],
                          'total_bytes': result['total_bytes'], 'bindings': result['bindings']}, indent=2))
        return 0
    except (HarnessError, OSError) as exc:
        parser.exit(2, str(exc) + '\n')


if __name__ == '__main__':
    main()
