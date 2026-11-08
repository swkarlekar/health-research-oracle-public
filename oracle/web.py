"""Loopback research UI. Optional Titan query embeddings require an exact grant."""
from contextlib import contextmanager, ExitStack
from collections import OrderedDict
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import json
from pathlib import Path
import re
import secrets
import sqlite3
import threading
from urllib.parse import parse_qs, urlsplit
import uuid

from .context import ExpandedCorpus
from .corpus import ApprovedCorpus, HarnessError, ROOT, base
from .harness import Harness
from .output import DebugTrace, demo_result, debug_markdown
from .policy import check_input, check_response_boundary, clarification_result, inferred_scope
from .relationships import GraphStore
from .viewer import CitationViewer
from .local_vectors import LocalVectorIndex, LocalVectorCorpus, cached_queries, VECTOR_CACHE, QUERY_CACHE
from .model_settings import ModelSettings, discover_local, check_aws
from .runtime_identity import runtime_identity

ASSETS = Path(__file__).with_name('web_assets')


def now():
    return datetime.now(timezone.utc).isoformat()


def aws_quality_hold(result):
    """A schema-valid trial answer is not automatically a reviewed demo example."""
    path = ROOT / 'artifacts/aws-eval/content-audit.json'
    fingerprint = base.digest(base.canonical(result))
    entries = base.load_json(path).get('results', []) if path.is_file() else []
    entry = next((item for item in entries if item.get('result_sha256') == fingerprint), None)
    if not entry or entry.get('status') != 'reviewed':
        return 'Saved AWS answer is withheld pending content review; its original diagnostic report is preserved'
    return None


class ProgressTrace(DebugTrace):
    def __init__(self, callback):
        super().__init__()
        self.callback = callback

    def event(self, stage, **details):
        super().event(stage, **details)
        self.callback(stage, details.get('role'))


class LocalApp:
    def __init__(self, db, registry, graph, history, *, context_chars=16000, model_factory=None,
                 retriever='local', vector_cache=VECTOR_CACHE, query_cache=QUERY_CACHE, budget_path=None):
        self.db, self.registry, self.graph = Path(db), Path(registry), Path(graph)
        self.history = Path(history)
        self.history_namespace = base.digest(str(self.history.resolve()))
        self.instance = runtime_identity(ROOT)
        if not 16000 <= context_chars <= 80000:
            raise HarnessError('UI context budget must be between 16000 and 80000 characters')
        self.context_chars = context_chars
        self.models = ModelSettings(self.history.parent / 'settings.json', query_cache, budget_path)
        self.custom_model_factory = model_factory is not None
        self.model_factory = model_factory or self.models.model
        self.model_name = self.selected_model()
        if retriever not in ('local', 'local-vector'):
            raise HarnessError('The page supports local keyword or local Titan vector retrieval')
        self.retriever, self.vector_cache = retriever, vector_cache
        self.vector_index, self.vector_lock = None, threading.Lock()
        self.queries = cached_queries(query_cache) if self.custom_model_factory and retriever == 'local-vector' else None
        self.lock = threading.Lock()
        self.settings_lock = threading.Lock()
        self.viewer_lock = threading.Lock()
        self.viewers = OrderedDict()
        self.token = secrets.token_urlsafe(32)
        self.history.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as con:
            con.executescript('''
                CREATE TABLE IF NOT EXISTS threads(id TEXT PRIMARY KEY,title TEXT NOT NULL,updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY,thread_id TEXT NOT NULL REFERENCES threads(id),
                  question TEXT NOT NULL,effective_question TEXT NOT NULL,scope_json TEXT NOT NULL,
                  status TEXT NOT NULL,progress TEXT NOT NULL,result_json TEXT,debug_json TEXT,error TEXT,
                  created_at TEXT NOT NULL,completed_at TEXT,saved_example INTEGER NOT NULL DEFAULT 0);
                CREATE INDEX IF NOT EXISTS runs_thread ON runs(thread_id,created_at);
            ''')
            con.execute("UPDATE runs SET status='failed',error='Local server restarted before this run finished',progress='interrupted' WHERE status='running'")

    @contextmanager
    def connect(self):
        con = sqlite3.connect(self.history, timeout=30)
        con.row_factory = sqlite3.Row
        con.execute('PRAGMA foreign_keys=ON')
        try:
            with con:
                yield con
        finally:
            con.close()

    @contextmanager
    def corpus(self):
        with ExitStack() as stack:
            if self.retriever == 'local-vector':
                with self.vector_lock:
                    if self.vector_index is None:
                        self.vector_index = LocalVectorIndex(self.vector_cache)
                queries = self.queries if self.custom_model_factory else self.models.queries()
                retriever = LocalVectorCorpus(self.db, self.registry, self.vector_index, queries)
            else:
                retriever = ApprovedCorpus(self.db, self.registry)
            stack.callback(retriever.close)
            graph = GraphStore(self.graph, retriever.summary()['snapshot_sha256'])
            stack.callback(graph.close)
            if not self.custom_model_factory and self.models.value['provider'] != 'bedrock':
                yield ExpandedCorpus(retriever, graph, short_document_chars=4000, max_unit_chars=8000, max_total_chars=8000)
            else:
                yield ExpandedCorpus(retriever, graph, max_total_chars=self.context_chars)

    def selected_model(self):
        value = self.models.value
        return value['bedrock_model'] if value['provider'] == 'bedrock' else value['local_model']

    def save_settings(self, payload):
        if not self.lock.acquire(blocking=False):
            raise BusyError('Wait for the active research run before changing its model')
        try:
            with self.settings_lock:
                result = self.models.save(payload)
            self.model_name = self.selected_model()
            return result
        finally:
            self.lock.release()

    def disable_aws(self):
        with self.settings_lock:
            return self.models.disable()

    def threads(self):
        with self.connect() as con:
            return [dict(r) for r in con.execute('SELECT t.*,COUNT(r.id) AS turns FROM threads t LEFT JOIN runs r ON r.thread_id=t.id GROUP BY t.id ORDER BY t.updated_at DESC LIMIT 200')]

    @staticmethod
    def public(row):
        item = {k: row[k] for k in ('id', 'thread_id', 'question', 'status', 'progress', 'error', 'created_at', 'saved_example')}
        result = json.loads(row['result_json']) if row['result_json'] else None
        if result and row['saved_example'] == 2 and (hold := aws_quality_hold(result)):
            item.update(status='failed', progress='content_review', error=hold)
            result = None
        item['answer'] = demo_result(result) if result else None
        item['model'] = result.get('harness', {}).get('model') if result else None
        item['scope'] = json.loads(row['scope_json'])
        item['uses_history'] = row['question'] != row['effective_question']
        return item

    def thread(self, identity):
        with self.connect() as con:
            if not con.execute('SELECT 1 FROM threads WHERE id=?', (identity,)).fetchone():
                raise HarnessError('Research conversation not found')
            return [self.public(row) for row in con.execute('SELECT * FROM runs WHERE thread_id=? ORDER BY created_at,id', (identity,))]

    def row(self, identity):
        with self.connect() as con:
            row = con.execute('SELECT * FROM runs WHERE id=?', (identity,)).fetchone()
        if row is None:
            raise HarnessError('Research run not found')
        return dict(row)

    def report(self, identity):
        row = self.row(identity)
        if not row['debug_json']:
            raise HarnessError('Debug report is available when this run finishes')
        return json.loads(row['debug_json'])

    def result(self, identity):
        row = self.row(identity)
        if not row['result_json']:
            raise HarnessError('No accepted response is available for this run')
        return json.loads(row['result_json'])

    def viewer(self, identity, result, index):
        if not 0 <= index < len(result.get('quotes', [])):
            raise HarnessError('Quotation not found')
        key = (identity, index, base.digest(base.canonical(result['quotes'][index])))
        with self.viewer_lock:
            if key not in self.viewers:
                with self.corpus() as corpus:
                    self.viewers[key] = CitationViewer(corpus).freeze(result, index)
                while len(self.viewers) > 24:
                    self.viewers.popitem(last=False)
            self.viewers.move_to_end(key)
            return self.viewers[key]

    def add_run(self, question, effective, scope, thread_id=None, saved=False):
        identity = uuid.uuid4().hex
        thread_id = thread_id or uuid.uuid4().hex
        stamp = now()
        with self.connect() as con:
            con.execute('INSERT OR IGNORE INTO threads VALUES(?,?,?)', (thread_id, ' '.join(question.split())[:80], stamp))
            con.execute('UPDATE threads SET updated_at=? WHERE id=?', (stamp, thread_id))
            con.execute('INSERT INTO runs(id,thread_id,question,effective_question,scope_json,status,progress,created_at,saved_example) VALUES(?,?,?,?,?,?,?,?,?)',
                        (identity, thread_id, question, effective, json.dumps(scope), 'running', 'queued', stamp, int(saved)))
        return identity

    def finish(self, identity, result, report, error=None):
        with self.connect() as con:
            con.execute('UPDATE runs SET status=?,progress=?,result_json=?,debug_json=?,error=?,completed_at=? WHERE id=?',
                        ('failed' if error else 'done', 'finished', json.dumps(result, ensure_ascii=False) if result else None,
                         json.dumps(report, ensure_ascii=False), error, now(), identity))

    def submit(self, payload):
        question = payload.get('question')
        supplied_scope = payload.get('scope') or {}
        if not isinstance(supplied_scope, dict) or set(supplied_scope) - {'program', 'plan_type', 'research_date'}:
            raise HarnessError('Invalid research scope')
        check_input(question, supplied_scope)  # Reject identifiers before saving any input.
        thread_id = payload.get('thread_id')
        effective = question.strip()
        previous_scope = {}
        if thread_id:
            self.thread(thread_id)
            if payload.get('use_history', True):
                with self.connect() as con:
                    previous = con.execute('SELECT effective_question,scope_json FROM runs WHERE thread_id=? ORDER BY created_at DESC,id DESC LIMIT 1', (thread_id,)).fetchone()
                if previous:
                    # Earlier generated answers are never promoted into evidence.
                    effective = (previous['effective_question'] + '\n\nLatest user message (answer this follow-up or use this clarification to complete the earlier question): ' + effective)
                    previous_scope = json.loads(previous['scope_json'])
        if len(effective) > 1600:
            raise HarnessError('This conversation exceeds the research context limit. Start a new research question or turn off previous-question context.')
        if type(payload.get('clear_research_date', False)) is not bool:
            raise HarnessError('Invalid research date reset')
        if (previous_scope.get('research_date') not in (None, 'unknown')
                and supplied_scope.get('research_date') == 'unknown' and not payload.get('clear_research_date')):
            supplied_scope = {k: v for k, v in supplied_scope.items() if k != 'research_date'}
        inferred = inferred_scope(effective)
        scope = {**inferred, **previous_scope,
                 **{k: v for k, v in inferred.items() if v is not None and k != 'research_date'},
                 **{k: v for k, v in supplied_scope.items() if v}}
        # An unspecified plan does not block general public-rule research. The
        # model can ask a focused question when that distinction is material.
        scope['plan_type'] = scope.get('plan_type') or 'unknown'
        check_input(effective, scope)
        clarification = clarification_result(effective, scope)
        if clarification is not None:
            identity = self.add_run(question, effective, scope, thread_id)
            trace = DebugTrace()
            trace.event('scope_clarification', questions=clarification['clarifying_questions'])
            self.finish(identity, clarification, trace.report(clarification))
            return self.public(self.row(identity))
        if not self.lock.acquire(blocking=False):
            raise BusyError('A local model run is already in progress. Wait for it to finish before sending another question.')
        try:
            identity = self.add_run(question, effective, scope, thread_id)
            worker = threading.Thread(target=self.work, args=(identity, effective, scope), daemon=True)
            worker.start()
        except Exception:
            self.lock.release()
            raise
        return self.public(self.row(identity))

    def work(self, identity, question, scope):
        def progress(stage, role):
            with self.connect() as con:
                con.execute('UPDATE runs SET progress=? WHERE id=?', (stage + (':' + role if role else ''), identity))
        trace = ProgressTrace(progress)
        try:
            with self.corpus() as corpus:
                result = Harness(corpus, self.model_factory(), limit=3, trace=trace,
                                 request_guard=not self.custom_model_factory).run(question, scope)
            self.finish(identity, result, trace.report(result))
        except Exception as exc:
            error = str(exc) if isinstance(exc, (HarnessError, OSError)) else 'Research run failed; no answer was accepted'
            trace.event('run_failed', reason=error)
            self.finish(identity, None, trace.report(error=error), error)
        finally:
            self.lock.release()

    def import_example(self, case=None):
        if case is not None and (type(case) is not int or not 1 <= case <= 15):
            raise HarnessError('Choose a saved AWS scenario from 1 through 15')
        path = ROOT / (f'artifacts/aws-eval/scenario-{case:02d}.debug.json' if case else 'artifacts/scenario-01.debug.json')
        if not path.is_file() or path.stat().st_size > 5_000_000:
            raise HarnessError('No saved accepted run is available for this example')
        report = base.load_json(path)
        result = report.get('result')
        if not result or not result.get('model_invoked') or result.get('simulation'):
            raise HarnessError('Saved example must be an actual accepted model run')
        question = result['scope']['question']
        if case is not None:
            if hold := aws_quality_hold(result):
                raise HarnessError(hold)
            sample = next(item for item in base.load_json(ROOT / 'examples/scenarios.json')['scenarios'] if item['id'] == case)
            if question != sample['question'] or not result['harness']['model'].startswith('qwen.'):
                raise HarnessError('Saved AWS run does not match the selected scenario or provider')
        scope = {k: result['scope'][k] for k in ('program', 'plan_type', 'research_date')}
        check_input(question, scope)
        check_response_boundary(result)
        with self.corpus() as corpus:
            if result['harness']['registry_sha256'] != base.digest(base.canonical(corpus.registry)):
                raise HarnessError('Saved example uses a different source registry')
            if result.get('context_expansion', {}).get('graph_snapshot', {}).get('graph_content_sha256') != corpus.graph.summary()['graph_content_sha256']:
                raise HarnessError('Saved example uses a different relationship map')
            viewer = CitationViewer(corpus)
            for index in range(len(result['quotes'])):
                viewer.resolve(result, index)
        identity = self.add_run(question, question, scope, saved=2 if case else 1)
        if case:
            with self.connect() as con:
                con.execute('UPDATE threads SET title=? WHERE id=(SELECT thread_id FROM runs WHERE id=?)',
                            (f"AWS {case}: {sample.get('title', question)}"[:100], identity))
        self.finish(identity, result, report)
        return self.public(self.row(identity))


class BusyError(HarnessError):
    pass


class LocalHandler(BaseHTTPRequestHandler):
    server_version = 'ResearchOracle/0.1'

    def log_message(self, *args):
        pass  # Queries and document identifiers do not enter access logs.

    def send(self, value, content_type='application/json; charset=utf-8', status=200, attachment=None):
        data = json.dumps(value, ensure_ascii=False).encode() if content_type.startswith('application/json') else value
        if isinstance(data, str):
            data = data.encode()
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store' if not content_type.startswith('image/') else 'private, max-age=300')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' blob:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        if attachment:
            self.send_header('Content-Disposition', f'attachment; filename="{attachment}"')
        try:
            self.end_headers()
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass  # Normal when a reader scrolls past a lazily rendered page.

    def allowed(self, write=False):
        port = self.server.server_address[1]
        hosts = {f'127.0.0.1:{port}', f'localhost:{port}'}
        if self.headers.get('Host') not in hosts:
            return False
        if write:
            origin = self.headers.get('Origin')
            if origin and origin not in {'http://' + host for host in hosts}:
                return False
            return hmac.compare_digest(self.headers.get('X-Oracle-Token', ''), self.server.app.token)
        return True

    def do_GET(self):
        self.route(False)

    def do_POST(self):
        self.route(True)

    def route(self, write):
        if not self.allowed(write):
            self.send({'error': 'Only this local research page may access this operation'}, status=403)
            return
        try:
            parts = urlsplit(self.path)
            path, query = parts.path, parse_qs(parts.query)
            app = self.server.app
            if write:
                if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                    raise HarnessError('Use a JSON request')
                length = int(self.headers.get('Content-Length', 0))
                if not 0 < length <= 16000:
                    raise HarnessError('Request body is too large or missing')
                payload = json.loads(self.rfile.read(length))
                if not isinstance(payload, dict):
                    raise HarnessError('Expected a JSON object')
                if path == '/api/ask':
                    self.send(app.submit(payload), status=202)
                elif path == '/api/example':
                    self.send(app.import_example(payload.get('case')), status=201)
                elif path == '/api/settings':
                    self.send(app.save_settings(payload))
                elif path == '/api/settings/discover':
                    self.send(discover_local(payload.get('provider'), payload.get('url', '')))
                elif path == '/api/settings/aws':
                    self.send(check_aws(payload.get('profile')))
                elif path == '/api/settings/disable':
                    self.send(app.disable_aws())
                else:
                    self.send({'error': 'Route not found'}, status=404)
                return
            assets = {'/': ('index.html', 'text/html; charset=utf-8'), '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
                      '/style.css': ('style.css', 'text/css; charset=utf-8'),
                      '/cssny-logo.svg': ('cssny-logo.svg', 'image/svg+xml')}
            if path in assets:
                name, kind = assets[path]
                self.send((ASSETS / name).read_bytes(), kind)
            elif path == '/api/config':
                trial_rows = ROOT / 'artifacts/aws-eval/queries.json'
                saved_aws = []
                for row in base.load_json(trial_rows) if trial_rows.is_file() else []:
                    report = ROOT / f"artifacts/aws-eval/scenario-{row['scenario']:02d}.debug.json"
                    if row['status'] != 'failed' and row.get('model_calls') and report.is_file():
                        result = base.load_json(report).get('result')
                        if result and not aws_quality_hold(result):
                            saved_aws.append(row['scenario'])
                self.send({'csrf_token': app.token, 'model': app.model_name,
                           'instance': app.instance,
                           'history_namespace': app.history_namespace,
                           'retriever': app.retriever,
                           'model_settings': app.models.public(),
                           'saved_example_available': (ROOT / 'artifacts/scenario-01.debug.json').is_file(),
                           'saved_aws_cases': saved_aws,
                           'scenarios': base.load_json(ROOT / 'examples/scenarios.json')['scenarios']})
            elif path == '/api/threads':
                self.send(app.threads())
            elif path == '/api/settings':
                self.send(app.models.public())
            elif match := re.fullmatch(r'/api/threads/([a-f0-9]{32})', path):
                self.send(app.thread(match[1]))
            elif match := re.fullmatch(r'/api/runs/([a-f0-9]{32})', path):
                self.send(app.public(app.row(match[1])))
            elif match := re.fullmatch(r'/api/runs/([a-f0-9]{32})/debug', path):
                report = app.report(match[1])
                if query.get('format') == ['markdown']:
                    self.send(debug_markdown(report), 'text/markdown; charset=utf-8', attachment='research-debug.md')
                else:
                    self.send(report)
            elif match := re.fullmatch(r'/api/runs/([a-f0-9]{32})/quotes/(\d+)(/page/(\d+)\.png|/original)?', path):
                result, index = app.result(match[1]), int(match[2])
                viewer = app.viewer(match[1], result, index)
                if match[3] == '/original':
                    _, doc, _, _, _ = viewer.resolve(result, index)
                    raw, data = viewer.raw_file(doc)
                    if data[:5] == b'%PDF-':
                        self.send(data, 'application/pdf')
                    else:
                        self.send(data, 'application/octet-stream', attachment='saved-source' + raw.suffix)
                elif match[4]:
                    self.send(viewer.image(result, index, int(match[4])), 'image/png')
                else:
                    page = int(query['page'][0]) if 'page' in query else None
                    self.send(viewer.describe(result, index, page))
            else:
                self.send({'error': 'Route not found'}, status=404)
        except BusyError as exc:
            self.send({'error': str(exc)}, status=409)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except (HarnessError, ValueError, OSError, sqlite3.Error) as exc:
            self.send({'error': str(exc)}, status=400)
        except Exception:
            self.send({'error': 'The local request could not be completed'}, status=500)


def serve(args):
    server = ThreadingHTTPServer(('127.0.0.1', args.port), LocalHandler)
    server.daemon_threads = True
    try:
        server.app = LocalApp(args.db, args.registry, args.graph, args.history,
                              context_chars=args.context_chars, retriever='local-vector',
                              vector_cache=args.vector_cache, query_cache=args.query_cache)
        print(f'Research oracle: http://127.0.0.1:{server.server_address[1]} (local only)', flush=True)
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
