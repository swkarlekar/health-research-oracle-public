"""Lossless local backup of S3 Vectors data; no embedding model is called."""
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import struct

from .corpus import HarnessError, base


def verify_cache(path):
    con = sqlite3.connect(f'{Path(path).resolve().as_uri()}?mode=ro', uri=True)
    try:
        manifest = json.loads(con.execute('SELECT json FROM manifest').fetchone()[0])
        digest, count = hashlib.sha256(), 0
        for key, data, metadata in con.execute('SELECT key,data,metadata FROM vectors ORDER BY key'):
            if len(data) != manifest['dimensions'] * 4:
                raise HarnessError('Cached vector dimensions changed')
            if not all(math.isfinite(v) for v in struct.unpack('<' + 'f' * manifest['dimensions'], data)):
                raise HarnessError('Cached vector contains non-finite values')
            json.loads(metadata)
            digest.update(base.canonical([key, metadata]).encode())
            digest.update(data)
            count += 1
        if count != manifest['vector_count'] or digest.hexdigest() != manifest['content_sha256']:
            raise HarnessError('Vector cache count or checksum mismatch')
        return manifest
    finally:
        con.close()


def export_vectors(client, index_arn, destination, *, expected_count, snapshot, resources, ledger):
    """A bounded, paginated export, with a reservation before any AWS request.

    Float32 blobs preserve the embedding values. Metadata is retained in full,
    including Bedrock's internal fields; this is not an export of KB sync state.
    """
    destination = Path(destination)
    if destination.exists():
        manifest = verify_cache(destination)
        if (manifest['snapshot_sha256'] != snapshot or manifest['vector_count'] != expected_count
                or manifest['dimensions'] != 1024 or manifest['embedding_model_id'] != 'amazon.titan-embed-text-v2:0'):
            raise HarnessError('Existing vector cache has a different snapshot or size')
        return manifest
    if not 1 <= expected_count <= 5000:
        raise HarnessError('Vector export exceeds this trial scope')
    ledger.reserve('vector-export', '0.10', details={'index_arn': index_arn,
        'max_pages': 100, 'max_vectors': 5000, 'basis': 'Conservative transfer/request allowance, not an invoice'})
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix('.partial.sqlite')
    if temporary.exists():
        raise HarnessError('An incomplete vector cache needs review before another export')
    con = sqlite3.connect(temporary)
    con.executescript('CREATE TABLE vectors(key TEXT PRIMARY KEY,data BLOB NOT NULL,metadata TEXT NOT NULL);'
                      'CREATE TABLE manifest(json TEXT NOT NULL);')
    tokens, token, count = set(), None, 0
    try:
        for page in range(100):
            request = {'indexArn': index_arn, 'maxResults': 100, 'returnData': True, 'returnMetadata': True}
            if token:
                request['nextToken'] = token
            response = client.list_vectors(**request)
            rows = response.get('vectors')
            if not isinstance(rows, list):
                raise HarnessError('Vector export omitted its records')
            for row in rows:
                key, values, metadata = row.get('key'), row.get('data', {}).get('float32'), row.get('metadata')
                if (not isinstance(key, str) or not isinstance(metadata, dict) or not isinstance(values, list)
                        or len(values) != 1024 or any(type(v) not in (int, float) or not math.isfinite(v) for v in values)):
                    raise HarnessError('Vector export returned malformed data')
                con.execute('INSERT INTO vectors VALUES(?,?,?)',
                            (key, struct.pack('<1024f', *values), base.canonical(metadata)))
                count += 1
                if count > expected_count:
                    raise HarnessError('Vector export exceeds the completed ingestion count')
            token = response.get('nextToken')
            if not token:
                break
            if not isinstance(token, str) or token in tokens:
                raise HarnessError('Vector export repeated an invalid pagination token')
            tokens.add(token)
        if token or count != expected_count:
            raise HarnessError('Vector export is incomplete; cache not accepted')
        digest = hashlib.sha256()
        for key, data, metadata in con.execute('SELECT key,data,metadata FROM vectors ORDER BY key'):
            digest.update(base.canonical([key, metadata]).encode())
            digest.update(data)
        manifest = {'format': 'float32-sqlite-v1', 'embedding_model_id': 'amazon.titan-embed-text-v2:0',
            'dimensions': 1024, 'distance_metric': 'cosine', 'vector_count': count,
            'snapshot_sha256': snapshot, 'content_sha256': digest.hexdigest(),
            'non_filterable_metadata_keys': ['AMAZON_BEDROCK_TEXT', 'AMAZON_BEDROCK_METADATA'],
            'original_resources': resources,
            'restore_limit': 'Vectors can be re-uploaded without embedding; Bedrock KB sync state and source bindings need separate validation.'}
        con.execute('INSERT INTO manifest VALUES(?)', (base.canonical(manifest),))
        con.commit()
    finally:
        con.close()
    verify_cache(temporary)
    temporary.replace(destination)
    return verify_cache(destination)


def restore_batches(path, batch_size=100):
    """Prepare paid PutVectors inputs locally; this function makes no AWS calls."""
    if type(batch_size) is not int or not 1 <= batch_size <= 100:
        raise HarnessError('Restore batches must contain 1 through 100 vectors')
    verify_cache(path)
    con = sqlite3.connect(f'{Path(path).resolve().as_uri()}?mode=ro', uri=True)
    try:
        batch = []
        for key, data, metadata in con.execute('SELECT key,data,metadata FROM vectors ORDER BY key'):
            batch.append({'key': key, 'data': {'float32': list(struct.unpack('<1024f', data))}, 'metadata': json.loads(metadata)})
            if len(batch) == batch_size:
                yield batch
                batch = []
        if batch:
            yield batch
    finally:
        con.close()


def remap_bedrock_metadata(vector, original, target):
    """Rebind only managed source references; never change source text/hashes."""
    metadata = vector['metadata']
    if metadata.get('x-amz-bedrock-kb-data-source-id') != original['DataSourceId']:
        raise HarnessError('Cached vector has an unexpected managed data source')
    nested = json.loads(metadata['AMAZON_BEDROCK_METADATA'])
    source = nested.get('source') or {}
    old_prefix = 's3://' + original['DocumentBucket'] + '/'
    location = source.get('sourceLocation')
    if not isinstance(location, str) or not location.startswith(old_prefix):
        raise HarnessError('Cached vector has an unexpected managed source location')
    source['sourceLocation'] = 's3://' + target['DocumentBucket'] + '/' + location[len(old_prefix):]
    metadata['x-amz-bedrock-kb-data-source-id'] = target['DataSourceId']
    metadata['AMAZON_BEDROCK_METADATA'] = base.canonical(nested)
    return vector
