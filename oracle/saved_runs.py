"""Bundled sample chats: finished Bedrock runs of the client reference questions, shown as earlier chats.

The runs are stored in examples/saved-runs/ (see scripts/export_saved_runs.py). On start-up each one is checked
against this checkout's source registry and relationship map, and every quotation is re-resolved against the
saved source text, exactly as for any saved example. A run that does not match is skipped, never shown.
Seeding adds nothing a second time, makes no model or AWS call, and never touches your own chats.
"""
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
import gzip
import json
from pathlib import Path

from .context import ExpandedCorpus
from .corpus import ApprovedCorpus, HarnessError, ROOT, base
from .policy import check_input, check_response_boundary
from .relationships import GraphStore
from .viewer import CitationViewer

SAVED_DIR = ROOT / 'examples/saved-runs'
SAMPLE_RUN = 3  # runs.saved_example value for bundled sample runs
MAX_JSON_BYTES = 8_000_000


def load_report(path):
    with gzip.open(path, 'rb') as handle:
        data = handle.read(MAX_JSON_BYTES + 1)
    if len(data) > MAX_JSON_BYTES:
        raise HarnessError('Saved sample run is too large')
    return json.loads(data)


def _validate(report, scenario, corpus):
    result = report.get('result')
    if not result or not result.get('model_invoked') or result.get('simulation'):
        raise HarnessError('not an accepted model run')
    if result['scope']['question'] != scenario['question']:
        raise HarnessError('question differs from the scenario')
    if not result['harness']['model'].startswith('qwen.'):
        raise HarnessError('unexpected model')
    scope = {k: result['scope'][k] for k in ('program', 'plan_type', 'research_date')}
    check_input(scenario['question'], scope)
    check_response_boundary(result)
    if result['harness']['registry_sha256'] != base.digest(base.canonical(corpus.registry)):
        raise HarnessError('different source registry')
    # The research pipeline does not use the relationship map; runs that did record its hash, which must match.
    graph_hash = ((result.get('context_expansion') or {}).get('graph_snapshot') or {}).get('graph_content_sha256')
    if graph_hash and graph_hash != corpus.graph.summary()['graph_content_sha256']:
        raise HarnessError('different relationship map')
    viewer = CitationViewer(corpus)
    for index in range(len(result['quotes'])):
        viewer.resolve(result, index)
    return result, scope


def seed(app, directory=SAVED_DIR, log=print, scenarios=None):
    """Add each bundled sample run to the chat history once. Returns the number added."""
    directory = Path(directory)
    files = sorted(directory.glob('scenario-*.debug.json.gz')) if directory.is_dir() else []
    if not files:
        return 0
    with app.connect() as con:
        con.execute('CREATE TABLE IF NOT EXISTS seeded_runs(scenario_id INTEGER PRIMARY KEY, run_id TEXT NOT NULL)')
        done = {row[0] for row in con.execute('SELECT scenario_id FROM seeded_runs')}
    scenarios = scenarios or {s['id']: s for s in base.load_json(ROOT / 'examples/scenarios.json')['scenarios']}
    pending = []
    for path in files:
        number = int(path.name.split('-')[1].split('.')[0])
        if number not in done and number in scenarios:
            pending.append((number, path))
    if not pending:
        return 0
    added = 0
    # Newest-first ordering in the sidebar: insert the last scenario first so the first one lists on top.
    start = datetime.now(timezone.utc)
    with ExitStack() as stack:
        retriever = ApprovedCorpus(app.db, app.registry)
        stack.callback(retriever.close)
        graph = GraphStore(app.graph, retriever.summary()['snapshot_sha256'])
        stack.callback(graph.close)
        corpus = ExpandedCorpus(retriever, graph)
        for position, (number, path) in enumerate(reversed(pending)):
            scenario = scenarios[number]
            try:
                report = load_report(path)
                result, scope = _validate(report, scenario, corpus)
            except (HarnessError, OSError, ValueError, KeyError) as exc:
                log(f'Sample run {number} skipped: {exc}')
                continue
            stamp = (start - timedelta(hours=1) + timedelta(seconds=position)).isoformat()
            identity, thread = base.digest(f'sample-run-{number}')[:32], base.digest(f'sample-thread-{number}')[:32]
            # "Case 1 · simplified · SNF out-of-network coverage": the part that differs comes first, because the
            # sidebar truncates long titles.
            label = (scenario.get('demo_label') or scenario['title']).split(' · ')
            title = (f"Case {scenario['advocate_case']} · {scenario['variant']} · {label[1]}"
                     if scenario.get('advocate_case') and scenario.get('variant') and len(label) > 1 else ' · '.join(label))
            with app.connect() as con:
                con.execute('INSERT OR IGNORE INTO threads VALUES(?,?,?)', (thread, title[:100], stamp))
                con.execute('INSERT OR IGNORE INTO runs(id,thread_id,question,effective_question,scope_json,status,progress,'
                            'result_json,debug_json,error,created_at,completed_at,saved_example) '
                            'VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                            (identity, thread, scenario['question'], scenario['question'], json.dumps(scope), 'done',
                             'finished', json.dumps(result, ensure_ascii=False), json.dumps(report, ensure_ascii=False),
                             None, stamp, stamp, SAMPLE_RUN))
                con.execute('INSERT OR REPLACE INTO seeded_runs VALUES(?,?)', (number, identity))
            added += 1
    return added
