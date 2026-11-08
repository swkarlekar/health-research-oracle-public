"""One disposable, budgeted AWS trial. Planning is offline; execution is opt-in.

Reuses the production adapters/harness. The ledger is fixed across restarts;
failed calls retain reservations. No SDK retries or provisioned services.
"""
import argparse
from contextlib import ExitStack, closing
import copy
import csv
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path
import shutil
import sqlite3
import time
import uuid

from .aws import ApprovalGate, BedrockCorpus, BedrockModel, export_ingestion, start_ingestion_job, upload_ingestion
from .budget import SpendLedger
from .context import ExpandedCorpus
from .corpus import ApprovedCorpus, HarnessError, ROOT, base
from .harness import Harness
from .output import DebugTrace, demo_markdown
from .relationships import GraphStore

REGION = 'us-east-1'
MODEL = 'qwen.qwen3-32b-v1:0'
EMBED = 'amazon.titan-embed-text-v2:0'
DIRECTORY = ROOT / 'artifacts/aws-eval'
MAX_INPUT, MAX_OUTPUT, MAX_PROMPT, MAX_MODEL_CALLS = 32768, 4096, 90000, 75


def converse_reservation(request, prices):
    # Model-specific byte/BPE bound, including both schema copies and a generous
    # allowance for Qwen's chat template. Never infer tokens from a character ratio.
    inputs = len(base.canonical(request).encode('utf-8')) + 1024
    return ((Decimal(inputs) * Decimal(str(prices['llm_input_per_million']))
             + Decimal(request['inferenceConfig']['maxTokens']) * Decimal(str(prices['llm_output_per_million']))) / Decimal(1_000_000))


def restore_ledger():
    ledger = SpendLedger(DIRECTORY / 'budget.sqlite')
    markers = [r['created_at'] for r in ledger.summary()['charges'] if r['category'] == 'cache-restore-setup']
    return ledger.use_stage('approved-cache-restore', '0.60', baseline_before=min(markers) if markers else None)


def save(path, value):
    """Replace complete local receipts atomically; never store credentials."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    temporary.replace(path)


def plan():
    prices = base.load_json(ROOT / 'config/aws-eval-prices.json')
    if (prices['region'], prices['generation_model_id'], prices['embedding_model_id']) != (REGION, MODEL, EMBED):
        raise HarnessError('Trial prices do not match its fixed model and region')
    with closing(ApprovedCorpus(ROOT / 'text_corpus/processed/corpus.sqlite', ROOT / 'config/sources.json')) as corpus:
        with closing(GraphStore(ROOT / 'artifacts/relationships.sqlite', corpus.summary()['snapshot_sha256'])):
            pass
        manifest = export_ingestion(corpus, ROOT / 'artifacts/aws-ingest')
    files = manifest['files']
    # Bound the managed job to the reviewed small corpus. NONE chunking; no OCR.
    if len(files) > 10000 or manifest['total_bytes'] > 16_000_000:
        raise HarnessError('Corpus exceeds the bounded $10 trial plan')
    for item in files:
        if item['name'].endswith('.txt') and item['bytes'] > 7000:
            raise HarnessError('An ingestion chunk exceeds the conservative Titan input bound')
    return {'mode': 'offline_plan', 'region': REGION, 'model': MODEL, 'embedding': EMBED,
            'snapshot_sha256': manifest['snapshot_sha256'], 'files': len(files),
            'bytes': manifest['total_bytes'], 'query_count': 15, 'max_model_calls': MAX_MODEL_CALLS,
            'max_retrieval_calls': 60, 'max_output_tokens': MAX_OUTPUT,
            'cap_usd': 10, 'setup_reservation_usd': 1, 'safety_reservation_usd': 2,
            'per_model_call_upper_usd': float((Decimal(MAX_INPUT) * Decimal(str(prices['llm_input_per_million']))
                + Decimal(MAX_OUTPUT) * Decimal(str(prices['llm_output_per_million']))) / Decimal(1_000_000)),
            'per_retrieval_call_upper_usd': 0.002,
            'cleanup': 'Delete this trial stack and its uploaded objects after evaluation.',
            'prices': prices, 'manifest': manifest}


class MeteredClients:
    """Record usage before answer parsing, including paid but invalid responses."""
    def __init__(self, session, ledger, prices, directory=None):
        self.session, self.ledger, self.prices = session, ledger, prices
        self.directory = Path(directory) if directory is not None else DIRECTORY
        self.clients, self.records = {}, []
        self.query_id = None

    def __call__(self, service, region):
        if region != REGION:
            raise HarnessError('Cross-region trial calls are blocked')
        if service not in self.clients:
            from botocore.config import Config
            client = self.session.client(service, region_name=region, config=Config(
                retries={'total_max_attempts': 1}, connect_timeout=10, read_timeout=180,
                max_pool_connections=12))
            self.clients[service] = client
        client = self.clients[service]
        if service not in ('bedrock-runtime', 'bedrock-agent-runtime'):
            return client
        owner = self
        class PaidClient:
            def converse(self, **request):
                if request.get('modelId') != MODEL or request.get('inferenceConfig', {}).get('maxTokens') != MAX_OUTPUT:
                    raise HarnessError('Unpriced inference request blocked')
                upper = converse_reservation(request, owner.prices)
                return owner.call(client, 'converse', request, upper)

            def retrieve(self, **request):
                return owner.call(client, 'retrieve', request, Decimal('0.002'))
        return PaidClient()

    def call(self, client, operation, request, upper):
        details = ({'input_token_upper': len(base.canonical(request).encode('utf-8')) + 1024,
                    'basis': 'Full serialized request UTF-8 bytes plus Qwen chat allowance and capped output'}
                   if operation == 'converse' else {'basis': 'Conservative retrieval/query-embedding allowance'})
        reservation = self.ledger.reserve(operation, upper, query_id=self.query_id, details=details)
        started = time.monotonic()
        record = {'query_id': self.query_id, 'operation': operation, 'reservation': reservation,
                  'cost_upper_usd': float(upper), 'completed': False}
        self.records.append(record)
        try:
            response = getattr(client, operation)(**request)
            if operation == 'converse':
                usage = response.get('usage', {})
                inputs, outputs = usage.get('inputTokens'), usage.get('outputTokens')
                if type(inputs) is not int or type(outputs) is not int or min(inputs, outputs) < 0:
                    raise HarnessError('No valid token receipt; reservation retained')
                cost = (Decimal(inputs) * Decimal(str(self.prices['llm_input_per_million']))
                        + Decimal(outputs) * Decimal(str(self.prices['llm_output_per_million']))) / Decimal(1_000_000)
                record.update(input_tokens=inputs, output_tokens=outputs, calculated_cost_usd=float(cost))
                self.ledger.settle(reservation, cost, details={'input_tokens': inputs, 'output_tokens': outputs})
            # Retrieval has no token receipt; retain its conservative upper bound.
            record['completed'] = True
            return response
        except Exception as exc:
            error = getattr(exc, 'response', {}).get('Error', {})
            record['aws_error_code'] = error.get('Code')
            if error.get('Code') == 'ValidationException':
                message = error.get('Message', '')
                record['validation_hints'] = [word for word in ('maxItems', 'minItems', 'pattern', 'enum',
                    'additionalProperties', 'outputConfig', 'textFormat', 'not supported', 'too complex') if word in message]
            raise
        finally:
            record['seconds'] = round(time.monotonic() - started, 3)
            save(self.directory / 'sdk-receipts.json', self.records)
            save(self.directory / 'budget.json', self.ledger.summary())


def grant(name, action, resource, calls):
    path = DIRECTORY / (name + '-approval.json')
    save(path, {'user_approved': True, 'authorization': 'User-authorized single $10 trial of 15 scenarios',
                'expires_at': (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat(),
                'action': action, 'region': REGION, 'resource': resource, 'max_calls': calls, 'used_calls': 0})
    return ApprovalGate(path)


def trial_template():
    template = copy.deepcopy(base.load_json(ROOT / 'infra/stack.json'))
    template['Description'] = 'Disposable oracle trial; delete all trial resources after the 15-query evaluation.'
    for resource in template['Resources'].values():
        for policy in ('DeletionPolicy', 'UpdateReplacePolicy'):
            if policy in resource:
                resource[policy] = 'Delete'
    # The owned index is deleted immediately afterward by CloudFormation.
    # Avoid a slow per-document vector purge before that whole-index deletion.
    template['Resources']['DataSource']['Properties']['DataDeletionPolicy'] = 'RETAIN'
    return template


def wait_stack(client, identity, *, deleting=False, timeout=900):
    from botocore.exceptions import ClientError
    deadline = time.monotonic() + timeout
    previous = None
    while time.monotonic() < deadline:
        try:
            stack = client.describe_stacks(StackName=identity)['Stacks'][0]
        except ClientError as exc:
            if deleting and exc.response['Error']['Code'] == 'ValidationError' and 'does not exist' in exc.response['Error']['Message']:
                return {}
            raise
        status = stack['StackStatus']
        if status != previous:
            print('Trial stack:', status, flush=True)
            previous = status
        if status == ('DELETE_COMPLETE' if deleting else 'CREATE_COMPLETE'):
            return stack
        if status.endswith('FAILED') or (not deleting and 'ROLLBACK' in status):
            raise HarnessError('Trial stack failed: ' + status)
        time.sleep(8)
    raise HarnessError('Trial stack wait timed out; cleanup state retained')


def cleanup(clients, state):
    if not state.get('stack_id'):
        return
    if state.get('paused_for_schema_repair'):
        print('Native schema needs local repair; temporary resources remain recorded for the same trial', flush=True)
        return
    if state.get('archive_required'):
        from .vector_cache import verify_cache
        try:
            manifest = verify_cache(DIRECTORY / 'vector-cache.sqlite')
        except (sqlite3.Error, OSError, HarnessError) as exc:
            raise HarnessError('Local vector backup is missing or invalid; the AWS copy is preserved') from exc
        if (manifest['snapshot_sha256'] != state['manifest']['snapshot_sha256']
                or manifest['vector_count'] != len(state['manifest']['files']) // 2):
            raise HarnessError('Vector backup mismatch; refusing to delete the AWS copy')
    from botocore.exceptions import ClientError
    cloudformation = clients('cloudformation', REGION)
    def finish_cleanup():
        previous = list(state.get('prior_stack_ids', []))
        if state.get('prior_stack_id'):
            previous.append(state['prior_stack_id'])
        for identity in dict.fromkeys(previous):
            wait_stack(cloudformation, identity, deleting=True, timeout=3600)
        if previous:
            state['prior_cleanup_complete'] = True
        state['cleaned_up'] = True
        save(DIRECTORY / 'state.json', state)
    try:
        status = cloudformation.describe_stacks(StackName=state['stack_id'])['Stacks'][0]['StackStatus']
    except ClientError as exc:
        if exc.response['Error']['Code'] != 'ValidationError' or 'does not exist' not in exc.response['Error']['Message']:
            raise
        status = 'DELETE_COMPLETE'
    if status in ('DELETE_IN_PROGRESS', 'DELETE_COMPLETE'):
        if status == 'DELETE_IN_PROGRESS':
            wait_stack(cloudformation, state['stack_id'], deleting=True, timeout=3600)
        finish_cleanup()
        return
    if state.get('ingestion_job_id') and state.get('ingestion_status') != 'COMPLETE':
        agent = clients('bedrock-agent', REGION)
        request = {'knowledgeBaseId': state['outputs']['KnowledgeBaseId'],
                   'dataSourceId': state['outputs']['DataSourceId'], 'ingestionJobId': state['ingestion_job_id']}
        job = agent.get_ingestion_job(**request)['ingestionJob']
        if job['status'] in ('STARTING', 'IN_PROGRESS'):
            agent.stop_ingestion_job(**request)
            deadline = time.monotonic() + 120
            while job['status'] in ('STARTING', 'IN_PROGRESS', 'STOPPING') and time.monotonic() < deadline:
                time.sleep(5)
                job = agent.get_ingestion_job(**request)['ingestionJob']
            if job['status'] in ('STARTING', 'IN_PROGRESS', 'STOPPING'):
                raise HarnessError('Ingestion has not stopped; cleanup state retained')
    bucket = state.get('outputs', {}).get('DocumentBucket')
    if bucket:
        # Only keys from this trial's reviewed manifest, in its own new bucket.
        keys = [{'Key': 'corpus/' + item['name']} for item in state['manifest']['files']]
        for start in range(0, len(keys), 1000):
            result = clients('s3', REGION).delete_objects(Bucket=bucket, Delete={'Objects': keys[start:start+1000], 'Quiet': True})
            if result.get('Errors'):
                raise HarnessError('Trial object cleanup failed; resources remain recorded')
    cloudformation.delete_stack(StackName=state['stack_id'])
    wait_stack(cloudformation, state['stack_id'], deleting=True, timeout=3600)
    finish_cleanup()


def evaluate(clients, outputs, scope_plan, continue_run=False):
    rows = base.load_json(DIRECTORY / 'queries.json') if continue_run and (DIRECTORY / 'queries.json').exists() else []
    for row in rows:
        row.setdefault('attempts', 1)
        row.setdefault('final_attempt_seconds', row['seconds'])
        row.setdefault('unreceipted_model_calls', 0)
    cases = [c for c in base.load_json(ROOT / 'examples/scenarios.json')['scenarios'] if c['id'] <= 15]
    retrieval_gate = ApprovalGate(DIRECTORY / 'retrieval-approval.json') if continue_run else grant(
        'retrieval', 'bedrock:Retrieve', {'knowledge_base_id': outputs['KnowledgeBaseId'],
        'max_results': 30, 'snapshot_sha256': scope_plan['snapshot_sha256']}, 60)
    model_gate = ApprovalGate(DIRECTORY / 'generation-approval.json') if continue_run else grant(
        'generation', 'bedrock:Converse', {'model_id': MODEL,
        'max_output_tokens': MAX_OUTPUT, 'max_prompt_chars': MAX_PROMPT}, MAX_MODEL_CALLS)
    # Finish unattempted cases first; accepted results are never rerun. Existing
    # failed cases may use only the remaining original action/dollar grants.
    prior = {row['scenario']: row for row in rows}
    pending = [case for case in cases if case['id'] not in prior]
    pending += [case for case in cases if prior.get(case['id'], {}).get('status') == 'failed']
    for case in pending:
        old = prior.get(case['id'])
        if old:
            remaining = base.load_json(model_gate.path)['max_calls'] - base.load_json(model_gate.path)['used_calls']
            if remaining < 3:
                print('Remaining original call grant cannot fit another bounded retry', flush=True)
                break
            archive = DIRECTORY / f"scenario-{case['id']:02d}.attempt-{old['attempts']}.debug.json"
            shutil.copy2(DIRECTORY / f"scenario-{case['id']:02d}.debug.json", archive)
        clients.query_id = case['id']
        prior_missing = sum(r['category'] == 'converse' and r['charged_micro'] is None
            for r in clients.ledger.summary()['charges'] if r['query_id'] == case['id'])
        trace, result, error, schema_rejected = DebugTrace(), None, None, False
        started = time.monotonic()
        print(f"Scenario {case['id']}/15: starting", flush=True)
        try:
            with ExitStack() as stack:
                retriever = BedrockCorpus(ROOT / 'text_corpus/processed/corpus.sqlite', ROOT / 'config/sources.json',
                    outputs['KnowledgeBaseId'], REGION, retrieval_gate, client_factory=clients)
                stack.callback(retriever.close)
                graph = GraphStore(ROOT / 'artifacts/relationships.sqlite', scope_plan['snapshot_sha256'])
                stack.callback(graph.close)
                corpus = ExpandedCorpus(retriever, graph, max_total_chars=16000)
                model = BedrockModel(MODEL, REGION, model_gate, MAX_OUTPUT, MAX_PROMPT, client_factory=clients)
                result = Harness(corpus, model, limit=3, trace=trace).run(case['question'], case['scope'])
        except Exception as exc:
            # Public logs contain exception class/code, never vendor request bodies.
            error = str(exc) if isinstance(exc, HarnessError) else type(exc).__name__
            schema_rejected = getattr(exc.__cause__, 'response', {}).get('Error', {}).get('Code') == 'ValidationException'
        seconds = round(time.monotonic() - started, 3)
        records = [r for r in clients.records if r['query_id'] == case['id']]
        charges = [r for r in clients.ledger.summary()['charges'] if r['query_id'] == case['id']]
        missing_usage = sum(r['category'] == 'converse' and r['charged_micro'] is None for r in charges)
        row = {'scenario': case['id'], 'status': result['status'] if result else 'failed',
               'seconds': round(seconds + (old['seconds'] if old else 0), 3),
               'attempts': (old['attempts'] if old else 0) + 1 + max(0, prior_missing - (old.get('unreceipted_model_calls', 0) if old else 0)),
               'final_attempt_seconds': seconds, 'unreceipted_model_calls': missing_usage,
               'model_calls': sum(r['category'] == 'converse' for r in charges),
               'retrieval_calls': sum(r['category'] == 'retrieve' for r in charges),
               'model_seconds': round(sum(r['seconds'] for r in records if r['operation'] == 'converse'), 3),
               'retrieval_seconds': round(sum(r['seconds'] for r in records if r['operation'] == 'retrieve'), 3),
               'input_tokens': sum(r.get('input_tokens', 0) for r in records),
               'output_tokens': sum(r.get('output_tokens', 0) for r in records),
               'calculated_model_cost_usd': sum(r.get('calculated_cost_usd', 0) for r in records),
               'query_cost_upper_usd': sum((r['charged_micro'] if r['charged_micro'] is not None else r['reserved_micro']) / 1e6 for r in charges),
               'error': error}
        rows = [r for r in rows if r['scenario'] != case['id']] + [row]
        rows.sort(key=lambda r: r['scenario'])
        save(DIRECTORY / f"scenario-{case['id']:02d}.debug.json", trace.report(result=result, error=error))
        if result:
            (DIRECTORY / f"scenario-{case['id']:02d}.md").write_text(demo_markdown(result))
        save(DIRECTORY / 'queries.json', rows)
        with (DIRECTORY / 'queries.csv').open('w', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=list(row))
            writer.writeheader()
            writer.writerows(rows)
        print(f"Scenario {case['id']}: {row['status']}, {seconds}s, model ${row['calculated_model_cost_usd']:.6f}", flush=True)
        if schema_rejected:
            raise HarnessError('AWS_SCHEMA_REPAIR_REQUIRED')
        if error and 'BUDGET STOP' in error:
            raise HarnessError(error)
    clients.query_id = None
    return rows


def evaluate_trial(clients, state, scope_plan, continue_run=False):
    try:
        return evaluate(clients, state['outputs'], scope_plan, continue_run)
    except HarnessError as exc:
        if str(exc) == 'AWS_SCHEMA_REPAIR_REQUIRED':
            state['paused_for_schema_repair'] = True
            save(DIRECTORY / 'state.json', state)
        raise


def finish_ingestion(clients, state, scope_plan, archive_vectors=False, continue_run=False):
    """Wait on the existing job, then evaluate once. Never starts/restarts ingestion."""
    outputs = state['outputs']
    request = {'knowledgeBaseId': outputs['KnowledgeBaseId'], 'dataSourceId': outputs['DataSourceId'],
               'ingestionJobId': state['ingestion_job_id']}
    deadline, previous = time.monotonic() + 3600, None
    while True:
        job = clients('bedrock-agent', REGION).get_ingestion_job(**request)['ingestionJob']
        save(DIRECTORY / 'ingestion.json', json.loads(json.dumps(job, default=str)))
        progress = (job['status'], job.get('statistics', {}).get('numberOfNewDocumentsIndexed', 0))
        if progress != previous:
            print('Titan ingestion:', progress[0], 'indexed:', progress[1], flush=True)
            previous = progress
        if job['status'] not in ('STARTING', 'IN_PROGRESS') or time.monotonic() >= deadline:
            break
        time.sleep(12)
    state['ingestion_status'] = job['status']
    save(DIRECTORY / 'state.json', state)
    if job['status'] != 'COMPLETE' or job.get('statistics', {}).get('numberOfDocumentsFailed', 0):
        raise HarnessError('Managed ingestion did not finish successfully; evaluation blocked')
    if archive_vectors:
        from .vector_cache import export_vectors
        state['archive_required'] = True
        save(DIRECTORY / 'state.json', state)
        print('Exporting vectors and all metadata to the local verified cache', flush=True)
        manifest = export_vectors(clients('s3vectors', REGION), outputs['VectorIndexArn'],
            DIRECTORY / 'vector-cache.sqlite', expected_count=scope_plan['files'] // 2,
            snapshot=scope_plan['snapshot_sha256'], resources=outputs, ledger=clients.ledger)
        save(DIRECTORY / 'vector-cache-manifest.json', manifest)
        print('Verified vector backup:', manifest['vector_count'], 'vectors', flush=True)
    return evaluate_trial(clients, state, scope_plan, continue_run)


def resume_ingestion(profile, expected_account, archive_vectors=False, continue_run=False):
    import boto3
    state = base.load_json(DIRECTORY / 'state.json')
    if (state.get('cleaned_up') or not (state.get('ingestion_job_id') or (continue_run and state.get('restored_from_cache')))
            or ((DIRECTORY / 'queries.json').exists() and not continue_run)):
        raise HarnessError('Resume is only for interrupted ingestion before any evaluation query')
    for name in ('generation-approval.json', 'retrieval-approval.json'):
        path = DIRECTORY / name
        if not continue_run and path.exists() and base.load_json(path).get('used_calls', 0):
            raise HarnessError('Evaluation calls already started; ingestion resume cannot repeat them')
    scope_plan = base.load_json(DIRECTORY / 'plan.json')
    if state['manifest']['snapshot_sha256'] != scope_plan['snapshot_sha256']:
        raise HarnessError('Resume snapshot mismatch')
    ledger = restore_ledger() if state.get('restored_from_cache') else SpendLedger(DIRECTORY / 'budget.sqlite')
    clients = MeteredClients(boto3.Session(profile_name=profile, region_name=REGION), ledger, scope_plan['prices'])
    if continue_run and (DIRECTORY / 'sdk-receipts.json').exists():
        clients.records = base.load_json(DIRECTORY / 'sdk-receipts.json')
    if clients('sts', REGION).get_caller_identity()['Account'] != expected_account or state['account'] != expected_account:
        raise HarnessError('Resume account mismatch')
    if archive_vectors:
        state['archive_required'] = True
        save(DIRECTORY / 'state.json', state)
    state.pop('paused_for_schema_repair', None)
    save(DIRECTORY / 'state.json', state)
    try:
        if state.get('restored_from_cache'):
            return evaluate_trial(clients, state, scope_plan, continue_run)
        return finish_ingestion(clients, state, scope_plan, archive_vectors, continue_run)
    finally:
        print('Cleaning up the disposable trial resources', flush=True)
        cleanup(clients, state)
        save(DIRECTORY / 'budget.json', ledger.summary())


def execute(scope_plan, profile, expected_account, retry_setup=False, archive_vectors=False):
    import boto3
    ledger = SpendLedger(DIRECTORY / 'budget.sqlite')
    session = boto3.Session(profile_name=profile, region_name=REGION)
    clients = MeteredClients(session, ledger, scope_plan['prices'])
    account = clients('sts', REGION).get_caller_identity()['Account']
    if account != expected_account:
        raise HarnessError('SSO account differs from the explicitly selected trial account')
    state_path = DIRECTORY / 'state.json'
    if state_path.exists():
        old = base.load_json(state_path)
        setups = sum(row['category'] == 'setup-and-ingestion' for row in ledger.summary()['charges'])
        if not retry_setup or not old.get('cleaned_up') or old.get('ingestion_job_id') or setups != 1:
            raise HarnessError('An earlier trial is recorded; refusing a duplicate paid evaluation')
        archive = DIRECTORY / 'setup-attempt-1'
        archive.mkdir(exist_ok=False)
        for name in ('state.json', 'failure.json', 'budget.json', 'upload-approval.json', 'trial-template.json'):
            if (DIRECTORY / name).exists():
                shutil.copy2(DIRECTORY / name, archive / name)
        (DIRECTORY / 'failure.json').unlink(missing_ok=True)
    if not any(r['category'] == 'safety-and-storage' for r in ledger.summary()['charges']):
        ledger.reserve('safety-and-storage', '2', details={'basis': 'Unallocated safety plus transient storage/cleanup buffer'})
    ledger.reserve('setup-and-ingestion', '1', details={'basis': 'Conservative upper estimate, not measured token usage',
                                                     'bytes': scope_plan['bytes'], 'files': scope_plan['files']})
    identity = 'oracle-trial-' + uuid.uuid4().hex[:12]
    state = {'name': identity, 'account': account, 'profile': profile, 'manifest': scope_plan['manifest'],
             'cleaned_up': False, 'archive_required': archive_vectors}
    cloudformation = clients('cloudformation', REGION)
    save(DIRECTORY / 'trial-template.json', trial_template())
    try:
        state['stack_id'] = identity  # Recover even if creation succeeds but its response is lost.
        save(state_path, state)
        response = cloudformation.create_stack(StackName=identity, TemplateBody=json.dumps(trial_template()),
            Parameters=[{'ParameterKey': 'PaidResourcesApproved', 'ParameterValue': 'yes'}],
            Capabilities=['CAPABILITY_IAM'], OnFailure='DELETE', TimeoutInMinutes=10,
            ClientRequestToken=identity, Tags=[{'Key': 'Project', 'Value': 'health-research-oracle-trial'}])
        state['stack_id'] = response['StackId']
        save(state_path, state)
        stack = wait_stack(cloudformation, state['stack_id'])
        outputs = {item['OutputKey']: item['OutputValue'] for item in stack['Outputs']}
        state['outputs'] = outputs
        save(state_path, state)
        upload_gate = grant('upload', 's3:PutObject', {'bucket': outputs['DocumentBucket'], 'prefix': 'corpus/',
            'manifest_sha256': base.digest(base.canonical(scope_plan['manifest']))}, scope_plan['files'] * 2)
        print('Uploading reviewed canonical chunks and metadata', flush=True)
        upload_ingestion(ROOT / 'artifacts/aws-ingest', outputs['DocumentBucket'], 'corpus/', REGION,
                         upload_gate, client_factory=clients, workers=6, dns_retries=1)
        ingestion_gate = grant('ingestion', 'bedrock:StartIngestionJob', {'knowledge_base_id': outputs['KnowledgeBaseId'],
            'data_source_id': outputs['DataSourceId'], 'snapshot_sha256': scope_plan['snapshot_sha256']}, 1)
        job = start_ingestion_job(outputs['KnowledgeBaseId'], outputs['DataSourceId'], scope_plan['snapshot_sha256'],
                                  REGION, ingestion_gate, client_factory=clients)
        state['ingestion_job_id'] = job['ingestionJobId']
        save(state_path, state)
        finish_ingestion(clients, state, scope_plan, archive_vectors)
    except Exception as exc:
        code = getattr(exc, 'response', {}).get('Error', {}).get('Code')
        save(DIRECTORY / 'failure.json', {'error': str(exc) if isinstance(exc, HarnessError) else type(exc).__name__,
                                        'aws_error_code': code, 'metered_sdk_calls': len(clients.records)})
        raise
    finally:
        print('Cleaning up the disposable trial resources', flush=True)
        cleanup(clients, state)
        save(DIRECTORY / 'budget.json', ledger.summary())


def restore_evaluation(profile, expected_account):
    """Recreate trial resources from the verified cache; never invokes ingestion."""
    import boto3
    from .vector_cache import verify_cache, restore_batches, remap_bedrock_metadata
    state_path = DIRECTORY / 'state.json'
    old = base.load_json(state_path)
    if not old.get('archive_required'):
        raise HarnessError('Restore requires a verified vector backup')
    cache = DIRECTORY / 'vector-cache.sqlite'
    manifest = verify_cache(cache)
    scope_plan = base.load_json(DIRECTORY / 'plan.json')
    if (manifest['snapshot_sha256'] != scope_plan['snapshot_sha256'] or manifest['dimensions'] != 1024
            or manifest['embedding_model_id'] != EMBED):
        raise HarnessError('Restore cache does not match the original trial')
    for batch in restore_batches(cache):
        for vector in batch:
            remap_bedrock_metadata(vector, manifest['original_resources'], manifest['original_resources'])
    ledger = restore_ledger()
    clients = MeteredClients(boto3.Session(profile_name=profile, region_name=REGION), ledger, scope_plan['prices'])
    clients.records = base.load_json(DIRECTORY / 'sdk-receipts.json')
    if clients('sts', REGION).get_caller_identity()['Account'] != expected_account or old['account'] != expected_account:
        raise HarnessError('Restore account mismatch')
    cf = clients('cloudformation', REGION)
    if not old.get('cleaned_up'):
        status = cf.describe_stacks(StackName=old['stack_id'])['Stacks'][0]['StackStatus']
        if status not in ('DELETE_IN_PROGRESS', 'DELETE_COMPLETE'):
            raise HarnessError('Prior trial must already be deleting before cache restore')
    restore_count = sum(r['category'] == 'cache-restore-setup' for r in ledger.summary()['charges'])
    if restore_count >= 2:
        raise HarnessError('The approved restore scope allows no further resource recreation')
    archive = DIRECTORY / ('before-cache-restore' if not restore_count else 'before-cache-restore-2')
    archive.mkdir(exist_ok=False)
    ledger.reserve('cache-restore-setup', '0.25' if not restore_count else '0.12', details={
        'basis': 'Bounded source PUTs and cached-vector writes, no document embedding', 'restore_attempt': restore_count + 1})
    shutil.copy2(state_path, archive / 'state.json')
    name = 'oracle-restore-' + uuid.uuid4().hex[:12]
    state = {'name': name, 'account': expected_account, 'profile': profile, 'manifest': scope_plan['manifest'],
             'archive_required': True, 'cleaned_up': False, 'restored_from_cache': True}
    if not old.get('cleaned_up'):
        state['prior_stack_ids'] = list(dict.fromkeys([
            *old.get('prior_stack_ids', []), *([old['prior_stack_id']] if old.get('prior_stack_id') else []), old['stack_id']]))
    try:
        state['stack_id'] = name
        save(state_path, state)
        state['stack_id'] = cf.create_stack(StackName=name, TemplateBody=json.dumps(trial_template()),
            Parameters=[{'ParameterKey': 'PaidResourcesApproved', 'ParameterValue': 'yes'}],
            Capabilities=['CAPABILITY_IAM'], OnFailure='DELETE', TimeoutInMinutes=10, ClientRequestToken=name)['StackId']
        save(state_path, state)
        stack = wait_stack(cf, state['stack_id'])
        outputs = {item['OutputKey']: item['OutputValue'] for item in stack['Outputs']}
        state['outputs'] = outputs
        save(state_path, state)
        upload_gate = grant('restore-upload', 's3:PutObject', {'bucket': outputs['DocumentBucket'], 'prefix': 'corpus/',
            'manifest_sha256': base.digest(base.canonical(scope_plan['manifest']))}, scope_plan['files'] * 2)
        upload_ingestion(ROOT / 'artifacts/aws-ingest', outputs['DocumentBucket'], 'corpus/', REGION,
                         upload_gate, client_factory=clients, workers=6, dns_retries=1)
        vector_gate = grant('restore-vectors', 's3vectors:PutVectors',
            {'index_arn': outputs['VectorIndexArn'], 'cache_sha256': manifest['content_sha256']}, 50)
        count = 0
        for batch in restore_batches(cache):
            vector_gate.reserve('s3vectors:PutVectors', REGION,
                {'index_arn': outputs['VectorIndexArn'], 'cache_sha256': manifest['content_sha256']})
            clients('s3vectors', REGION).put_vectors(indexArn=outputs['VectorIndexArn'],
                vectors=[remap_bedrock_metadata(v, manifest['original_resources'], outputs) for v in batch])
            count += len(batch)
        save(DIRECTORY / 'restore-proof.json', {'vectors_restored': count, 'document_embedding_calls': 0,
            'ingestion_jobs_started': 0, 'cache_content_sha256': manifest['content_sha256']})
        # Preserve the original grant counters; only the KB resource ID changes.
        retrieval = base.load_json(DIRECTORY / 'retrieval-approval.json')
        retrieval['resource']['knowledge_base_id'] = outputs['KnowledgeBaseId']
        save(DIRECTORY / 'retrieval-approval.json', retrieval)
        print('Restored cached vectors without a corpus embedding job:', count, flush=True)
        evaluate_trial(clients, state, scope_plan, continue_run=True)
    finally:
        cleanup(clients, state)
        save(DIRECTORY / 'budget.json', ledger.summary())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument('--execute', action='store_true', help='Requires explicit user approval for this single paid $10 trial')
    actions.add_argument('--cleanup', action='store_true', help='Resume deletion of only resources recorded in this trial state')
    actions.add_argument('--resume-ingestion', action='store_true', help='Continue the existing ingestion job and original evaluation; no reupload or re-embedding')
    actions.add_argument('--resume-evaluation', action='store_true', help='Continue original cases and bounded failed-case retries using the existing grants')
    actions.add_argument('--restore-evaluation', action='store_true', help='Recreate the existing trial from cached vectors and retry only failed original cases')
    parser.add_argument('--retry-setup', action='store_true', help='One deliberate retry after verified cleanup, only if ingestion never started')
    parser.add_argument('--archive-vectors', action='store_true', help='Requires separate approval; export vectors within the same cap before cleanup')
    parser.add_argument('--profile', default='oracle')
    parser.add_argument('--expected-account')
    args = parser.parse_args()
    if args.restore_evaluation:
        if not args.expected_account:
            parser.error('--restore-evaluation requires --expected-account')
        restore_evaluation(args.profile, args.expected_account)
        return
    if args.resume_ingestion or args.resume_evaluation:
        if not args.expected_account:
            parser.error('--resume-ingestion requires --expected-account')
        resume_ingestion(args.profile, args.expected_account, args.archive_vectors, args.resume_evaluation)
        return
    if args.cleanup:
        if not args.expected_account:
            parser.error('--cleanup requires --expected-account')
        import boto3
        state = base.load_json(DIRECTORY / 'state.json')
        state.pop('paused_for_schema_repair', None)
        clients = MeteredClients(boto3.Session(profile_name=args.profile, region_name=REGION),
                                SpendLedger(DIRECTORY / 'budget.sqlite'), base.load_json(ROOT / 'config/aws-eval-prices.json'))
        if clients('sts', REGION).get_caller_identity()['Account'] != args.expected_account or state['account'] != args.expected_account:
            raise HarnessError('Cleanup account mismatch')
        cleanup(clients, state)
        return
    scope_plan = plan()
    save(DIRECTORY / 'plan.json', scope_plan)
    print(json.dumps({k: v for k, v in scope_plan.items() if k not in ('manifest', 'prices')}, indent=2), flush=True)
    if args.execute:
        if not args.expected_account:
            parser.error('--execute requires --expected-account')
        execute(scope_plan, args.profile, args.expected_account, args.retry_setup, args.archive_vectors)


if __name__ == '__main__':
    main()
