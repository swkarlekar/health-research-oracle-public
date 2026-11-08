"""Controlled local-retrieval comparison; no cloud deployment or ingestion.

Default execution is entirely local. Bedrock generation and Titan cache misses
require separate exact grants and persistent ceilings within the original $10.
"""
import argparse
from contextlib import ExitStack
import csv
import json
from pathlib import Path
import time

from .aws import ApprovalGate, BedrockModel, AccountSession
from .aws_evaluation import MeteredClients, MODEL, REGION, MAX_OUTPUT, MAX_PROMPT, save
from .budget import SpendLedger
from .context import ExpandedCorpus
from .corpus import ROOT, HarnessError, base
from .harness import Harness
from .local_vectors import LocalVectorIndex, LocalVectorCorpus, cached_queries, VECTOR_CACHE, QUERY_CACHE
from .output import DebugTrace, demo_markdown
from .providers import LocalModel, ollama_identity
from .relationships import GraphStore

DIRECTORY = ROOT / 'artifacts/evaluation'


def retrieval_parity(index, queries, directory=DIRECTORY):
    """Compare historical managed initial seeds to current local results, free.

    Reuse saved query text/snapshot and actual initial seed order, never a synthetic
    AWS result. Different historic top-K values are preserved in the record.
    """
    rows = []
    source = LocalVectorCorpus(ROOT / 'text_corpus/processed/corpus.sqlite', ROOT / 'config/sources.json', index, queries)
    try:
        for case in [c for c in base.load_json(ROOT / 'examples/scenarios.json')['scenarios'] if c['id'] <= 15]:
            path = ROOT / f"artifacts/aws-eval/scenario-{case['id']:02d}.debug.json"
            report = base.load_json(path)
            initial = next(e for e in report['events'] if e['stage'] == 'initial_retrieval_and_expansion')
            snapshot = initial.get('context_expansion', {}).get('graph_snapshot', {}).get('snapshot_sha256')
            if initial['scope']['question'] != case['question'] or snapshot != index.manifest['snapshot_sha256']:
                raise HarnessError('Historical managed retrieval does not match the query/corpus snapshot')
            if initial['retrieval'].get('engine') != 'bedrock_knowledge_bases':
                raise HarnessError('Historical result is not actual Bedrock managed retrieval')
            original = [e['chunk_id'] for e in initial['evidence_ids'] if not e['chunk_id'].startswith('ctx:')]
            local = [e['chunk_id'] for e in source.search(case['question'], len(original))]
            common = set(original) & set(local)
            old_docs = {source.chunk(c)['document_id'] for c in original}
            local_docs = {source.chunk(c)['document_id'] for c in local}
            rows.append({'scenario': case['id'], 'historical_aws_top_k': original, 'local_top_k': local,
                'identical_order': original == local, 'chunk_overlap_at_k': len(common) / len(original),
                'document_overlap': len(old_docs & local_docs) / len(old_docs),
                'snapshot_sha256': snapshot, 'query_sha256': base.digest(case['question']),
                'local_search_seconds': source.last_search['seconds']})
    finally:
        source.close()
    save(Path(directory) / 'retrieval-parity.json', {'comparison': 'Historical AWS managed initial seeds vs exact local cosine',
        'limits': 'Same query and pinned snapshot; approximate AWS ranking can differ. No new cloud retrieval. '
                  'This measures overlap, not relevance or answer correctness.', 'results': rows})
    return rows


def run_arm(arm, index, queries, *, directory=DIRECTORY, profile='oracle', approval=None, cases=None,
            corpus_db=None, registry=None, graph_path=None, model_factory=None, local_identity=None):
    """Resume skips every completed scenario, including rejected answers."""
    directory = Path(directory) / arm
    directory.mkdir(parents=True, exist_ok=True)
    rows = base.load_json(directory / 'queries.json') if (directory / 'queries.json').is_file() else []
    completed = {r['scenario'] for r in rows}
    cases = cases or [c for c in base.load_json(ROOT / 'examples/scenarios.json')['scenarios'] if c['id'] <= 15]
    local_identity = local_identity or ollama_identity()
    controls = {'arm': arm, 'snapshot_sha256': index.manifest['snapshot_sha256'],
        'vector_cache_sha256': index.manifest['content_sha256'], 'context_chars': 16000, 'limit': 3,
        'output_tokens': MAX_OUTPUT, 'prompt_chars': MAX_PROMPT, 'temperature': 0,
        'source_sha256': {path.name: base.digest(path.read_text()) for path in sorted((ROOT / 'oracle').glob('*.py'))},
        'cases_sha256': base.digest(base.canonical(cases))}
    # Pin the data/policies as well as code; these must agree across both arms.
    controls['registry_sha256'] = base.digest(Path(registry or ROOT / 'config/sources.json').read_text())
    controls['policy_files'] = {str(path.relative_to(ROOT)): base.digest(path.read_text()) for path in
        [ROOT / 'config/temporal-policy.json', ROOT / 'text_corpus/scripts/research.py',
         *sorted(p for p in (ROOT / 'text_corpus/config').iterdir() if p.is_file())]}
    controls['local_model_identity'] = local_identity
    graph = GraphStore(graph_path or ROOT / 'artifacts/relationships.sqlite', index.manifest['snapshot_sha256'])
    try:
        controls['graph_content_sha256'] = graph.summary()['graph_content_sha256']
    finally:
        graph.close()
    other = Path(directory).parent / ('local-local' if arm == 'bedrock-local' else 'bedrock-local') / 'controls.json'
    if other.is_file():
        shared = lambda value: {k: v for k, v in value.items() if k != 'arm'}
        if shared(base.load_json(other)) != shared(controls):
            raise HarnessError('Paired evaluation arms differ in shared data/code/settings')
    control_path = directory / 'controls.json'
    if control_path.is_file() and base.load_json(control_path) != controls:
        raise HarnessError('Evaluation code/data/settings changed; do not mix these results')
    save(control_path, controls)
    clients = None
    if arm == 'bedrock-local':
        if approval is None:
            raise HarnessError('Explicit approval is required for the Bedrock evaluation')
        ledger = SpendLedger(ROOT / 'artifacts/aws-eval/budget.sqlite').use_stage('approved-local-evaluation', '0.50', isolated=True)
        session = AccountSession(profile, base.load_json(approval).get('aws_account'))
        clients = MeteredClients(session, ledger,
            base.load_json(ROOT / 'config/aws-eval-prices.json'), directory=directory)
        if (directory / 'sdk-receipts.json').is_file():
            clients.records = base.load_json(directory / 'sdk-receipts.json')
    for case in cases:
        if case['id'] in completed:
            continue
        # A pre-call marker prevents silent repetition if the process is killed.
        marker = directory / f"scenario-{case['id']:02d}.started.json"
        try:
            with marker.open('x') as stream:
                json.dump({'scenario': case['id'], 'started_at': time.time()}, stream)
        except FileExistsError:
            raise HarnessError('An interrupted scenario requires accounting review before resuming')
        trace, result, error = DebugTrace(), None, None
        started = time.monotonic()
        model = None
        if clients:
            clients.query_id = case['id']
        try:
            with ExitStack() as stack:
                source = LocalVectorCorpus(corpus_db or ROOT / 'text_corpus/processed/corpus.sqlite',
                    registry or ROOT / 'config/sources.json', index, queries)
                stack.callback(source.close)
                graph = GraphStore(graph_path or ROOT / 'artifacts/relationships.sqlite', source.summary()['snapshot_sha256'])
                stack.callback(graph.close)
                corpus = ExpandedCorpus(source, graph, max_total_chars=16000)
                if model_factory:
                    model = model_factory()
                elif clients:
                    model = BedrockModel(MODEL, REGION, ApprovalGate(approval), MAX_OUTPUT, MAX_PROMPT, client_factory=clients)
                else:
                    model = LocalModel('qwen3.6:latest', timeout=600, max_output_tokens=MAX_OUTPUT, max_prompt_chars=MAX_PROMPT)
                result = Harness(corpus, model, limit=3, trace=trace).run(case['question'], case['scope'])
        except Exception as exc:
            error = str(exc) if isinstance(exc, (HarnessError, OSError)) else type(exc).__name__
        elapsed = time.monotonic() - started
        records = [r for r in clients.records if r['query_id'] == case['id']] if clients else []
        charges = []
        if clients:
            reservations = {r['reservation'] for r in records}
            charges = [r for r in clients.ledger.summary()['charges'] if r['id'] in reservations]
        row = {'scenario': case['id'], 'status': result['status'] if result else 'failed',
               'seconds': round(elapsed, 3), 'model_calls': getattr(model, 'calls', 0),
               'input_tokens': sum(r.get('input_tokens', 0) for r in records) if clients else sum(r.get('prompt_tokens') or 0 for r in getattr(model, 'usage', [])),
               'output_tokens': sum(r.get('output_tokens', 0) for r in records) if clients else sum(r.get('output_tokens') or 0 for r in getattr(model, 'usage', [])),
               'calculated_model_cost_usd': sum(r.get('calculated_cost_usd', 0) for r in records),
               'model_cost_upper_usd': sum(r['charged_micro'] if r['charged_micro'] is not None else r['reserved_micro'] for r in charges) / 1e6,
               'unreceipted_model_calls': sum(r['charged_micro'] is None for r in charges),
               'error': error}
        save(directory / f"scenario-{case['id']:02d}.debug.json", trace.report(result, error))
        if result:
            (directory / f"scenario-{case['id']:02d}.md").write_text(demo_markdown(result))
        rows.append(row)
        rows.sort(key=lambda r: r['scenario'])
        save(directory / 'queries.json', rows)
        with (directory / 'queries.csv').open('w', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=list(row))
            writer.writeheader()
            writer.writerows(rows)
        print(f"{arm} scenario {case['id']}: {row['status']}, {row['seconds']}s", flush=True)
        if error and 'BUDGET STOP' in error:
            break
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--arm', choices=['local-local', 'bedrock-local', 'retrieval-parity'], required=True)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--profile', default='oracle')
    parser.add_argument('--model-approval', type=Path)
    parser.add_argument('--query-embedding-approval', type=Path)
    parser.add_argument('--vector-cache', type=Path, default=VECTOR_CACHE)
    parser.add_argument('--query-cache', type=Path, default=QUERY_CACHE)
    args = parser.parse_args()
    if not args.execute:
        print(base.canonical({'arm': args.arm, 'execute': False, 'scenarios': 15,
            'retrieval': 'local exact cosine over exported Titan vectors', 'context_chars': 16000,
            'top_k': 3, 'max_model_calls': 45, 'new_infrastructure': False,
            'bedrock_stage_cap_usd': 0.50, 'query_embedding_stage_cap_usd': 0.01,
            'total_cap_usd': 10, 'historical_aws_outputs_are_controlled_comparison': False}))
        return
    index = LocalVectorIndex(args.vector_cache)
    queries = cached_queries(args.query_cache, approval=args.query_embedding_approval, profile=args.profile)
    if args.arm == 'retrieval-parity':
        retrieval_parity(index, queries)
    else:
        run_arm(args.arm, index, queries, profile=args.profile, approval=args.model_approval)


if __name__ == '__main__':
    main()
