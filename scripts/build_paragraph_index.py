"""Build the optional paragraph-vector index (one-time, about $0.10 of Titan V2 embeddings).

    .venv/bin/python scripts/build_paragraph_index.py --profile oracle --approve-spend 0.25
    .venv/bin/python scripts/build_paragraph_index.py --from-cache artifacts/harness-lab/para-vectors.sqlite

Paragraphs are embedded at 1,024 dimensions with their document title and heading,
then stored truncated to 512 dimensions as float16 (Titan V2 vectors are nested).
The work file is resumable. The explicit --approve-spend value is a hard stop
computed from returned token counts; this script does not use the app's grants.
"""
import argparse, hashlib, json, sqlite3, struct, sys, threading, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from oracle.corpus import ApprovedCorpus                            # noqa: E402
from oracle.paragraphs import MODEL, PARAGRAPH_VECTORS, SPLITTER_VERSION, paragraph_rows  # noqa: E402

PRICE_PER_TOKEN = 0.02 / 1_000_000


def embed_text(title, heading, text):
    head = ' | '.join(x for x in (' '.join((title or '').split()), ' '.join((heading or '').split())[:200]) if x)
    return (head + '\n' + text.strip())[:6000]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--profile', default='oracle')
    parser.add_argument('--approve-spend', type=float, default=0.0, help='explicit dollar ceiling for new embeddings')
    parser.add_argument('--from-cache', type=Path, help='existing work file of 1,024-d paragraph vectors')
    parser.add_argument('--work', type=Path, default=ROOT / 'artifacts/aws-eval/paragraph-vectors.work.sqlite')
    parser.add_argument('--output', type=Path, default=PARAGRAPH_VECTORS)
    parser.add_argument('--dimensions', type=int, default=512)
    args = parser.parse_args()
    import numpy as np
    corpus = ApprovedCorpus(ROOT / 'text_corpus/processed/corpus.sqlite', ROOT / 'config/sources.json')
    rows = list(paragraph_rows(corpus))
    work = sqlite3.connect(args.from_cache or args.work, check_same_thread=False)
    work.execute('CREATE TABLE IF NOT EXISTS v(rowid INTEGER PRIMARY KEY, chunk_id TEXT, start INT, end INT, data BLOB)')
    have = {(c, s, e): d for c, s, e, d in work.execute('SELECT chunk_id, start, end, data FROM v')}
    todo = [r for r in rows if (r[0], r[1], r[2]) not in have]
    print(f'{len(rows)} paragraphs; {len(todo)} need embeddings')
    if todo:
        if args.approve_spend <= 0:
            raise SystemExit('New embeddings are needed. Re-run with an explicit --approve-spend ceiling (about 0.15).')
        import boto3
        client = boto3.Session(profile_name=args.profile).client('bedrock-runtime', region_name='us-east-1')
        lock, spent = threading.Lock(), [0.0]

        def one(row):
            chunk_id, start, end, title, heading, text = row
            if spent[0] >= args.approve_spend:
                raise SystemExit('Approved spend reached; re-run to resume.')
            body = json.dumps({'inputText': embed_text(title, heading, text), 'dimensions': 1024, 'normalize': True})
            for attempt in range(6):
                try:
                    data = json.loads(client.invoke_model(modelId=MODEL, body=body, contentType='application/json',
                                                          accept='application/json')['body'].read())
                    break
                except Exception:
                    if attempt == 5:
                        raise
                    time.sleep(2 * (attempt + 1))
            blob = struct.pack('<1024f', *data['embedding'])
            with lock:
                work.execute('INSERT INTO v(chunk_id,start,end,data) VALUES(?,?,?,?)', (chunk_id, start, end, blob))
                have[(chunk_id, start, end)] = blob
                spent[0] += data['inputTextTokenCount'] * PRICE_PER_TOKEN
                if len(have) % 500 == 0:
                    work.commit()
                    print(len(have), f'${spent[0]:.4f}', flush=True)
        with ThreadPoolExecutor(12) as pool:
            list(pool.map(one, todo))
        work.commit()
        print(f'embedded {len(todo)} paragraphs for ${spent[0]:.4f}')
    matrix = np.zeros((len(rows), args.dimensions), dtype='<f2')
    for index, row in enumerate(rows):
        vector = np.frombuffer(have[(row[0], row[1], row[2])], dtype='<f4')[:args.dimensions]
        matrix[index] = vector / np.linalg.norm(vector)
    blobs = [matrix[i].tobytes() for i in range(len(rows))]
    manifest = {'embedding_model_id': MODEL, 'dimensions': args.dimensions, 'dtype': 'float16', 'splitter': SPLITTER_VERSION,
                'vector_count': len(rows), 'snapshot_sha256': corpus.summary()['snapshot_sha256'],
                'content_sha256': hashlib.sha256(b''.join(blobs)).hexdigest(),
                'input': 'document title | heading path, newline, paragraph text; 1,024-d Titan V2 truncated and renormalized'}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.output.exists():
        args.output.unlink()
    out = sqlite3.connect(args.output)
    out.execute('CREATE TABLE manifest(value TEXT NOT NULL)')
    out.execute('CREATE TABLE vectors(chunk_id TEXT NOT NULL, start INTEGER NOT NULL, end INTEGER NOT NULL, data BLOB NOT NULL)')
    out.execute('INSERT INTO manifest VALUES(?)', (json.dumps(manifest),))
    out.executemany('INSERT INTO vectors VALUES(?,?,?,?)', [(r[0], r[1], r[2], b) for r, b in zip(rows, blobs)])
    out.commit(); out.close()
    print('wrote', args.output, f'{args.output.stat().st_size / 1e6:.1f} MB', json.dumps(manifest)[:200])


if __name__ == '__main__':
    main()
