"""Local-first research CLI. Live AWS operations require exact approval grants."""
import argparse
from contextlib import ExitStack
import json
import sqlite3
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from .aws import ApprovalGate, BedrockCorpus, BedrockModel, export_ingestion, start_ingestion_job, upload_ingestion
from .context import ExpandedCorpus
from .corpus import ApprovedCorpus, HarnessError, ROOT, base, build_registry
from .harness import Harness
from .providers import LocalModel, guard_local_model
from .policy import clarification_result, inferred_scope
from .output import DebugTrace, demo_result, demo_markdown, debug_markdown
from .relationships import GraphStore, build_graph
from .relationship_review import prepare_relationship_packet, review_candidates, import_review
from .local_vectors import LocalVectorCorpus, LocalVectorIndex, cached_queries, VECTOR_CACHE, QUERY_CACHE


def _model_args(command):
    command.add_argument('--provider', choices=['local', 'bedrock'], default='local')
    command.add_argument('--model', help='Local default: installed gemma3:4b; Bedrock uses configured model')
    command.add_argument('--review-model')
    command.add_argument('--endpoint', default='http://127.0.0.1:11434/api/chat')
    command.add_argument('--timeout', type=float, default=180)
    command.add_argument('--context-tokens', type=int, choices=[8192, 16384, 32768], default=32768,
                         help='Conservative local request budget; Ollama also receives this context setting')
    command.add_argument('--acknowledge-unknown-model-size', action='store_true',
                         help='Confirm local memory requirements when metadata omits size; never overrides a known-large-model block')
    command.add_argument('--model-approval', type=Path, default=ROOT / 'aws-approval.json')
    command.add_argument('--review-approval', type=Path)


def _vector_args(command):
    command.add_argument('--vector-cache', type=Path, default=VECTOR_CACHE)
    command.add_argument('--query-cache', type=Path, default=QUERY_CACHE)
    command.add_argument('--query-embedding-approval', type=Path,
                         help='Explicit Titan query-embedding grant; omitted means cache-only, no AWS')
    command.add_argument('--aws-profile', default='oracle', help='SSO profile for approved Titan cache misses only')


def _models(args, config, relationship=False):
    if args.provider == 'local':
        parts = urlsplit(args.endpoint)
        if parts.path not in ('/api/chat', '/v1/chat/completions'):
            raise HarnessError('Use /api/chat or /v1/chat/completions on a local model server')
        provider = 'ollama' if parts.path == '/api/chat' else 'openai-local'
        url = urlunsplit(parts._replace(path=''))
        names = [args.model or 'gemma3:4b']
        if args.review_model and args.review_model != names[0]:
            names.append(args.review_model)
        # Check both models before constructing either; metadata GETs never load
        # a model and no implicit download or alternative-model fallback exists.
        for name in names:
            guard_local_model(provider, url, name,
                size_acknowledged=getattr(args, 'acknowledge_unknown_model_size', False), inference=True)
        options = {'context_tokens': getattr(args, 'context_tokens', 32768),
                   'strict_context': True, 'max_output_tokens': 2048, 'keep_alive': 0}
        model = LocalModel(names[0], args.endpoint, args.timeout, **options)
        reviewer = LocalModel(names[1], args.endpoint, args.timeout, **options) if len(names) > 1 else model
        return model, reviewer
    generation = config['generation']
    draft_key = 'relationship_model_id' if relationship else 'draft_model_id'
    review_key = 'relationship_review_model_id' if relationship else 'review_model_id'
    options = {'max_output_tokens': generation['max_output_tokens'], 'max_prompt_chars': generation['max_prompt_chars']}
    model_id = args.model or generation[draft_key]
    review_id = args.review_model or generation[review_key]
    model = BedrockModel(model_id, config['region'], ApprovalGate(args.model_approval), **options)
    reviewer = model if review_id == model_id and not args.review_approval else BedrockModel(
        review_id, config['region'], ApprovalGate(args.review_approval or args.model_approval), **options)
    return model, reviewer


def _scope(args):
    scope = {**inferred_scope(args.question), **getattr(args, 'case_scope', {})}
    return {key: getattr(args, key) or scope.get(key) for key in ('program', 'plan_type', 'research_date')}


def _select_question(args):
    if args.case is not None:
        if args.question:
            raise HarnessError('Choose either a question or --case, not both')
        cases = base.load_json(ROOT / 'examples/scenarios.json')['scenarios']
        case = next((item for item in cases if item['id'] == args.case), None)
        if case is None:
            raise HarnessError('Unknown scenario; choose an ID from examples/scenarios.json')
        args.question, args.case_scope = case['question'], case['scope']
    elif not args.question:
        if args.command != 'demo':
            raise HarnessError('Supply a question or a scenario ID with --case')
        args.case = 1
        _select_question(args)


def _parser():
    parser = argparse.ArgumentParser(description='TRACE research assistant; defaults are local and AWS is approval-gated')
    parser.add_argument('--db', type=Path, default=ROOT / 'text_corpus/processed/corpus.sqlite')
    parser.add_argument('--registry', type=Path, default=ROOT / 'config/sources.json')
    parser.add_argument('--graph', type=Path, default=ROOT / 'artifacts/relationships.sqlite')
    parser.add_argument('--aws-config', type=Path, default=ROOT / 'config/aws-demo.json')
    commands = parser.add_subparsers(dest='command', required=True)
    web = commands.add_parser('serve', help='Open a loopback-only chat interface with local PDF inspection')
    web.add_argument('--port', type=int, default=8765)
    web.add_argument('--history', type=Path, default=ROOT / 'artifacts/ui/history.sqlite')
    web.add_argument('--context-chars', type=int, default=16000, help='Expansion budget; omissions remain explicit in debug output')
    web.add_argument('--vector-cache', type=Path, default=VECTOR_CACHE)
    web.add_argument('--query-cache', type=Path, default=QUERY_CACHE)
    queries = commands.add_parser('cache-query-embeddings', help='Plan or explicitly execute Titan query embeddings for the 15 original scenarios')
    _vector_args(queries)
    queries.add_argument('--execute', action='store_true')
    queries.add_argument('--output', type=Path)
    registry = commands.add_parser('build-registry', help='Pin the user-selected corpus; review changes')
    registry.add_argument('--output', type=Path, default=ROOT / 'config/sources.json')
    build = commands.add_parser('build-graph', help='Scan all canonical sections locally; no model/network')
    build.add_argument('--documents', type=Path, default=ROOT / 'text_corpus/processed/documents.jsonl')
    build.add_argument('--sections', type=Path, default=ROOT / 'text_corpus/processed/sections.jsonl')
    build.add_argument('--output', type=Path, default=ROOT / 'artifacts/relationships.sqlite')
    build.add_argument('--report', type=Path, default=ROOT / 'artifacts/graph-report.json')
    status = commands.add_parser('graph-status', help='Inspect source coverage and relationship review status')
    status.add_argument('--output', type=Path)
    packet = commands.add_parser('relationship-packet', help='Export a pinned candidate packet for offline review')
    packet.add_argument('relationship_id')
    packet.add_argument('--max-context-chars', type=int, default=32000)
    packet.add_argument('--output', type=Path, required=True)
    review = commands.add_parser('review-relationships', help='Opt-in bounded interpretation + independent model review')
    _model_args(review)
    review.add_argument('--limit', type=int, default=5)
    review.add_argument('--max-model-calls', type=int, default=10)
    review.add_argument('--max-context-chars', type=int, default=32000)
    review.add_argument('--retry-unresolved', action='store_true', help='Explicitly revisit previously uncertain model reviews')
    review.add_argument('--output', type=Path, default=ROOT / 'artifacts/relationship-review-report.json')
    imported = commands.add_parser('import-relationship-review', help='Validate and append externally produced model review')
    for name in ('packet', 'interpretation', 'review'):
        imported.add_argument('--' + name, type=Path, required=True)
    imported.add_argument('--reviewer', required=True, help='Honest model/agent provenance label')
    imported.add_argument('--output', type=Path)
    export = commands.add_parser('export-aws', help='Create S3 input files locally; no network')
    export.add_argument('--output-dir', type=Path, default=ROOT / 'artifacts/aws-ingest')
    upload = commands.add_parser('upload-aws', help='Print exact upload plan; --execute additionally requires approval')
    upload.add_argument('--directory', type=Path, default=ROOT / 'artifacts/aws-ingest')
    upload.add_argument('--bucket', required=True)
    upload.add_argument('--prefix', default='corpus/')
    upload.add_argument('--approval', type=Path, default=ROOT / 'aws-approval.json')
    upload.add_argument('--execute', action='store_true')
    upload.add_argument('--output', type=Path)
    ingestion = commands.add_parser('start-ingestion', help='Print managed embedding/ingestion plan; explicit execution is gated')
    ingestion.add_argument('--knowledge-base-id')
    ingestion.add_argument('--data-source-id')
    ingestion.add_argument('--approval', type=Path, default=ROOT / 'aws-approval.json')
    ingestion.add_argument('--execute', action='store_true')
    ingestion.add_argument('--output', type=Path)
    for name in ('ask', 'demo', 'api-check'):
        descriptions = {'ask': 'Query research evidence', 'demo': 'Real local inference; defaults to user scenario 1',
                        'api-check': 'Offline AWS-shaped API simulation; no inference or network'}
        ask = commands.add_parser(name, help=descriptions[name])
        ask.add_argument('question', nargs='?')
        ask.add_argument('--case', type=int, help='User-supplied scenario ID from examples/scenarios.json, including advocate and boundary examples')
        ask.add_argument('--program')
        ask.add_argument('--plan-type')
        ask.add_argument('--research-date')
        ask.add_argument('--limit', type=int, default=4)
        ask.add_argument('--format', choices=['json', 'markdown'], default='markdown' if name == 'demo' else 'json')
        ask.add_argument('--view', choices=['demo', 'debug'], default='demo')
        ask.add_argument('--output', type=Path)
        ask.add_argument('--debug-output', type=Path, help='Separate local diagnostic report (.md for Markdown; otherwise JSON)')
        if name != 'api-check':
            _model_args(ask)
            ask.add_argument('--retriever', choices=['local', 'local-vector', 'bedrock'], default='local')
            _vector_args(ask)
            ask.add_argument('--knowledge-base-id')
            ask.add_argument('--retrieval-approval', type=Path, default=ROOT / 'aws-approval.json')
            ask.add_argument('--evidence-only', action='store_true')
            ask.add_argument('--no-expansion', action='store_true', help='Explicit baseline comparison')
    return parser


def _open_graph(args, stack, snapshot=None):
    graph = GraphStore(args.graph, snapshot)
    stack.callback(graph.close)
    return graph


def _local_command(args, stack):
    corpus = ApprovedCorpus(args.db, args.registry)
    stack.callback(corpus.close)
    if args.command == 'build-graph':
        result = build_graph(corpus, args.documents, args.sections, args.output)
        base.emit(result, args.report)
        return result, None
    if args.command == 'export-aws':
        result = export_ingestion(corpus, args.output_dir)
        return {key: value for key, value in result.items() if key != 'files'}, None
    graph = _open_graph(args, stack, corpus.summary()['snapshot_sha256'])
    if args.command == 'graph-status':
        result = graph.summary()
    elif args.command == 'relationship-packet':
        result = prepare_relationship_packet(graph, args.relationship_id, args.max_context_chars)
    elif args.command == 'review-relationships':
        config = base.load_json(args.aws_config) if args.provider == 'bedrock' else None
        model, reviewer = _models(args, config, relationship=True)
        result = review_candidates(graph, model, limit=args.limit, max_model_calls=args.max_model_calls,
                                  max_context_chars=args.max_context_chars, review_model=reviewer,
                                  retry_unresolved=args.retry_unresolved)
    else:
        result = import_review(graph, base.load_json(args.packet), base.load_json(args.interpretation),
                               base.load_json(args.review), reviewer=args.reviewer)
    return result, args.output


def _aws_command(args, stack):
    config = base.load_json(args.aws_config)
    if args.command == 'upload-aws':
        manifest = base.load_json(args.directory / 'upload-manifest.json')
        if not args.prefix.strip('/'):
            raise HarnessError('Use a nonempty dedicated S3 prefix')
        result = upload_ingestion(args.directory, args.bucket, args.prefix, config['region'], ApprovalGate(args.approval)) if args.execute else {
            'action': 's3:PutObject', 'region': config['region'],
            'resource': {'bucket': args.bucket, 'prefix': args.prefix.strip('/') + '/',
                         'manifest_sha256': base.digest(base.canonical(manifest))},
            'file_count': manifest['file_count'], 'total_bytes': manifest['total_bytes'],
            'executed': False, 'cost_estimate': 'Not yet supplied; requests and ongoing storage may incur charges'}
    else:
        corpus = ApprovedCorpus(args.db, args.registry)
        stack.callback(corpus.close)
        kb_id = args.knowledge_base_id or config['retrieval']['knowledge_base_id']
        ds_id = args.data_source_id or config['retrieval']['data_source_id']
        if not kb_id or not ds_id:
            raise HarnessError('Supply the actual knowledge base and data source IDs after approved deployment')
        snapshot = corpus.summary()['snapshot_sha256']
        result = start_ingestion_job(kb_id, ds_id, snapshot, config['region'], ApprovalGate(args.approval)) if args.execute else {
            'action': 'bedrock:StartIngestionJob', 'region': config['region'],
            'knowledge_base_id': kb_id, 'data_source_id': ds_id, 'snapshot_sha256': snapshot,
            'embedding_model': config['embeddings'], 'executed': False,
            'note': 'Managed ingestion and embeddings require explicit cost approval'}
    return result, args.output


def _query_command(args, stack, trace=None):
    if args.command == 'api-check':
        from .api_check import run_api_check
        config = base.load_json(args.aws_config)
        graph = _open_graph(args, stack)
        return run_api_check(args.db, args.registry, graph, args.question, _scope(args), args.limit,
                        config['generation']['draft_model_id'], trace=trace), args.output
    config = base.load_json(args.aws_config) if args.provider == 'bedrock' or args.retriever == 'bedrock' else None
    if args.retriever == 'bedrock':
        kb_id = args.knowledge_base_id or config['retrieval']['knowledge_base_id']
        if not kb_id:
            raise HarnessError('Supply the actual knowledge base ID after approved deployment')
        corpus = BedrockCorpus(args.db, args.registry, kb_id, config['region'],
                               ApprovalGate(args.retrieval_approval), max_pages=config['retrieval']['max_pages'])
    elif args.retriever == 'local-vector':
        corpus = LocalVectorCorpus(args.db, args.registry, LocalVectorIndex(args.vector_cache),
            cached_queries(args.query_cache, approval=args.query_embedding_approval, profile=args.aws_profile))
    else:
        corpus = ApprovedCorpus(args.db, args.registry)
    stack.callback(corpus.close)
    if not args.no_expansion:
        graph = _open_graph(args, stack, corpus.summary()['snapshot_sha256'])
        corpus = ExpandedCorpus(corpus, graph)
    model, reviewer = (None, None) if args.evidence_only else _models(args, config)
    return Harness(corpus, model, args.limit, reviewer=reviewer, trace=trace,
                   request_guard=args.command != 'api-check').run(
        args.question, _scope(args), args.evidence_only), args.output


def _emit(args, result, output, trace=None):
    if trace is not None:
        report = trace.report(result)
        if args.debug_output:
            base.emit(debug_markdown(report) if args.debug_output.suffix == '.md' else report, args.debug_output)
    if getattr(args, 'view', None) == 'debug':
        base.emit(debug_markdown(report) if args.format == 'markdown' else report, output)
    elif getattr(args, 'view', None) == 'demo':
        base.emit(demo_markdown(result) if args.format == 'markdown' else demo_result(result), output)
    else:
        base.emit(result, output)


def main(argv=None):
    args = _parser().parse_args(argv)
    trace = None
    try:
        if args.command == 'cache-query-embeddings':
            cases = [c for c in base.load_json(ROOT / 'examples/scenarios.json')['scenarios'] if c['id'] <= 15]
            plan = {'executed': False, 'model_id': 'amazon.titan-embed-text-v2:0', 'dimensions': 1024,
                    'normalize': True, 'scenario_count': len(cases), 'document_embedding_calls': 0,
                    'additional_stage_cap_usd': 0.01, 'total_trial_cap_usd': 10}
            if args.execute:
                if args.query_embedding_approval is None:
                    raise HarnessError('Explicit query-embedding approval is required before execution')
                cache = cached_queries(args.query_cache, approval=args.query_embedding_approval, profile=args.aws_profile)
                records = []
                for case in cases:
                    cache.embed(case['question'])
                    records.append({'scenario': case['id'], **cache.last_usage})
                plan.update(executed=True, queries=records)
            base.emit(plan, args.output)
            return 0
        if args.command == 'serve':
            from .web import serve
            if not 0 <= args.port <= 65535:
                raise HarnessError('Local port must be between 0 and 65535')
            serve(args)
            return 0
        if args.command in {'ask', 'demo', 'api-check'}:
            if args.debug_output and args.output and args.debug_output.resolve() == args.output.resolve():
                raise HarnessError('--output and --debug-output must use different paths')
            _select_question(args)
            trace = DebugTrace() if args.debug_output or args.view == 'debug' else None
            clarification = clarification_result(args.question, _scope(args))
            if clarification is not None:
                if trace:
                    trace.event('scope_clarification', questions=clarification['clarifying_questions'])
                _emit(args, clarification, args.output, trace)
                return 0
        with ExitStack() as stack:
            if args.command == 'build-registry':
                result, output = build_registry(args.db), args.output
            elif args.command in {'ask', 'demo', 'api-check'}:
                result, output = _query_command(args, stack, trace)
            elif args.command in {'upload-aws', 'start-ingestion'}:
                result, output = _aws_command(args, stack)
            else:
                result, output = _local_command(args, stack)
            _emit(args, result, output, trace)
        return 0
    except (HarnessError, OSError, sqlite3.Error, json.JSONDecodeError) as exc:
        if trace is not None:
            report = trace.report(error=str(exc))
            try:
                if args.debug_output:
                    base.emit(debug_markdown(report) if args.debug_output.suffix == '.md' else report, args.debug_output)
                if args.view == 'debug':
                    base.emit(debug_markdown(report) if args.format == 'markdown' else report, args.output)
                elif args.output:
                    failed = {'status': 'failed', 'findings': [], 'quotes': [], 'uncertainties': [], 'clarifying_questions': []}
                    base.emit(demo_markdown(failed) if args.format == 'markdown' else demo_result(failed), args.output)
            except OSError as write_error:
                print(f'Could not write diagnostics: {write_error}', file=sys.stderr)
        print(f'TRACE error: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
