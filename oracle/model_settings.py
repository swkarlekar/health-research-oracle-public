"""Model discovery and explicit, bounded per-computer AWS authorization.

Discovery only reads metadata. Models, credentials, histories and grants are
never downloaded, copied to other users, or bundled with the corpus.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
import re
import uuid

from .aws import ApprovalGate, AccountSession, BedrockModel, TitanQueryEmbedder
from .aws_evaluation import converse_reservation, save
from .budget import SpendLedger
from .corpus import ROOT, HarnessError, base
from .local_vectors import CachedTitanQueries
from .providers import LocalModel, discover_local, guard_local_model, server_url

CATALOG = base.load_json(ROOT / 'config/bedrock-models.json')
DEFAULT = {'provider': 'bedrock', 'bedrock_model': 'qwen.qwen3-32b-v1:0', 'local_model': '',
           'local_url': 'http://127.0.0.1:11434', 'aws_profile': 'oracle',
           'context_tokens': 32768, 'local_size_acknowledged': False, 'authorization': None}


def check_aws(profile):
    if not isinstance(profile, str) or not re.fullmatch(r'[\w.@+-]{1,80}', profile):
        raise HarnessError('Enter a local AWS profile name')
    try:
        import boto3
        from botocore.config import Config
        session = boto3.Session(profile_name=profile)
        config = Config(retries={'total_max_attempts': 1}, connect_timeout=5, read_timeout=10)
        identity = session.client('sts', region_name='us-east-1', config=config).get_caller_identity()
        models = session.client('bedrock', region_name='us-east-1', config=config).list_foundation_models(
            byOutputModality='TEXT', byInferenceType='ON_DEMAND')['modelSummaries']
        available = {m['modelId'] for m in models}
        return {'account': identity['Account'], 'region': 'us-east-1',
                'models': [m for m in CATALOG['models'] if m['id'] in available],
                'note': 'Identity/catalog metadata only; no paid model call. Catalog presence does not verify model access.'}
    except Exception as exc:
        if isinstance(exc, HarnessError):
            raise
        raise HarnessError('AWS profile check failed. Sign in locally with aws sso login --profile PROFILE; no credentials are stored in the app.') from exc


class ModelSettings:
    def __init__(self, path, query_cache, ledger_path=None):
        self.path, self.query_cache = Path(path), Path(query_cache)
        self.ledger_path = Path(ledger_path or ROOT / 'artifacts/aws-eval/budget.sqlite')
        self.value = {**DEFAULT, **(base.load_json(self.path) if self.path.is_file() else {})}

    def public(self):
        value = dict(self.value)
        auth = value.get('authorization')
        enabled = bool(auth and datetime.fromisoformat(auth['expires_at']) > datetime.now(timezone.utc))
        value['aws_enabled'] = enabled
        value['bedrock_models'] = CATALOG['models']
        value['aws_region'] = 'us-east-1'
        summary = SpendLedger(self.ledger_path).summary()
        value['budget'] = {k: summary[k] for k in ('cap_usd', 'committed_upper_usd', 'remaining_usd')}
        if auth:
            value['authorization'] = {k: auth[k] for k in ('account', 'expires_at', 'allowance_usd')}
            value['authorization']['generation_model_id'] = auth.get('generation_model_id')
        return value

    def save(self, payload):
        provider = payload.get('provider')
        if provider not in ('bedrock', 'ollama', 'lmstudio', 'openai-local'):
            raise HarnessError('Choose a supported model provider')
        model_id = payload.get('bedrock_model', DEFAULT['bedrock_model'])
        if model_id not in {m['id'] for m in CATALOG['models']}:
            raise HarnessError('Choose a priced Bedrock model from the list')
        profile = payload.get('aws_profile', 'oracle')
        if not isinstance(profile, str) or not re.fullmatch(r'[\w.@+-]{1,80}', profile):
            raise HarnessError('Enter a local AWS profile name')
        context = payload.get('context_tokens', 32768)
        if type(context) is not int or context not in (8192, 16384, 32768):
            raise HarnessError('Choose a supported local context size')
        value = {**DEFAULT, 'provider': provider, 'bedrock_model': model_id, 'aws_profile': profile,
                 'local_model': self.value['local_model'] if provider == 'bedrock' else payload.get('local_model', ''),
                 'local_url': self.value['local_url'] if provider == 'bedrock' else server_url(payload.get('local_url', DEFAULT['local_url'])),
                 'context_tokens': context, 'local_size_acknowledged': payload.get('local_size_acknowledged') is True}
        if provider != 'bedrock':
            self.check_local(value)
        if payload.get('authorize_aws') is True:
            try:
                allowance = Decimal(str(payload.get('allowance_usd', '.25')))
            except InvalidOperation as exc:
                raise HarnessError('AWS allowance must be a number between $0.01 and $10') from exc
            if not allowance.is_finite() or not Decimal('.01') <= allowance <= Decimal('10'):
                raise HarnessError('AWS allowance must be between $0.01 and $10')
            identity = check_aws(profile)
            if identity['account'] != payload.get('confirmed_account'):
                raise HarnessError('Check the AWS account, then explicitly authorize that account')
            if provider == 'bedrock' and model_id not in {m['id'] for m in identity['models']}:
                raise HarnessError('Selected model is not listed in this region; choose an available model')
            ledger = SpendLedger(self.ledger_path)
            if allowance > Decimal(str(ledger.summary()['remaining_usd'])):
                raise HarnessError('This allowance exceeds the remaining strict cumulative budget')
            identifier = uuid.uuid4().hex
            expires = (datetime.now(timezone.utc) + timedelta(hours=4)).isoformat()
            authorization = {'id': identifier, 'account': identity['account'], 'expires_at': expires,
                             'allowance_usd': str(allowance),
                             'generation_model_id': model_id if provider == 'bedrock' else None}
            grants = [('query', 'bedrock:InvokeModel', TitanQueryEmbedder.RESOURCE)]
            if authorization['generation_model_id']:
                grants.append(('model', 'bedrock:Converse', {
                    'model_id': model_id, 'max_output_tokens': 4096, 'max_prompt_chars': 90000}))
            for name, action, resource in grants:
                save(self.grant_path(identifier, name), {'user_approved': True, 'aws_account': identity['account'],
                    'action': action, 'region': 'us-east-1', 'resource': resource, 'max_calls': 100, 'used_calls': 0,
                    'expires_at': expires, 'authorization': 'User explicitly enabled the displayed account/model and session allowance in Settings.'})
            ledger.use_stage('demo-' + identifier, allowance, isolated=True)
            value['authorization'] = authorization
        elif (self.value['aws_profile'] == profile and self.value['bedrock_model'] == model_id):
            value['authorization'] = self.value.get('authorization')
        save(self.path, value)
        self.value = value
        return self.public()

    def grant_path(self, identifier, role):
        return self.path.parent / 'grants' / f'{identifier}-{role}.json'

    def check_local(self, value=None, *, inference=False):
        value = value or self.value
        return guard_local_model(value['provider'], value['local_url'], value['local_model'],
            size_acknowledged=value['local_size_acknowledged'], inference=inference)

    def authorized(self):
        auth = self.value.get('authorization')
        if not auth or datetime.fromisoformat(auth['expires_at']) <= datetime.now(timezone.utc):
            raise HarnessError('Open Settings to check your AWS account and explicitly enable paid calls')
        ledger = SpendLedger(self.ledger_path).use_stage('demo-' + auth['id'], auth['allowance_usd'], isolated=True)
        return auth, ledger

    def disable(self):
        auth = self.value.get('authorization')
        if auth:
            for role in ('query', 'model'):
                path = self.grant_path(auth['id'], role)
                if not path.is_file():
                    continue
                grant = base.load_json(path)
                grant['expires_at'] = datetime.now(timezone.utc).isoformat()
                save(path, grant)
        self.value = {**self.value, 'authorization': None}
        save(self.path, self.value)
        return self.public()

    def queries(self):
        auth = self.value.get('authorization')
        embedder = None
        if auth:
            ledger = SpendLedger(self.ledger_path).use_stage('demo-' + auth['id'], auth['allowance_usd'], isolated=True)
            embedder = TitanQueryEmbedder(ApprovalGate(self.grant_path(auth['id'], 'query')), ledger,
                profile=self.value['aws_profile'], stage_name='demo-' + auth['id'], allowance_usd=auth['allowance_usd'])
        return CachedTitanQueries(self.query_cache, embedder)

    def model(self):
        value = self.value
        if value['provider'] != 'bedrock':
            self.check_local(inference=True)
            path = '/api/chat' if value['provider'] == 'ollama' else '/v1/chat/completions'
            return LocalModel(value['local_model'], value['local_url'] + path, timeout=600,
                max_output_tokens=2048, context_tokens=value['context_tokens'], strict_context=True, keep_alive=0)
        auth, ledger = self.authorized()
        model_id = value['bedrock_model']
        if auth.get('generation_model_id') != model_id:
            raise HarnessError('Open Settings and explicitly authorize the selected Bedrock model before generation; query-embedding consent alone is insufficient')
        price = next(m for m in CATALOG['models'] if m['id'] == model_id)
        session = AccountSession(value['aws_profile'], auth['account'])
        def factory(service, region):
            return BudgetedRuntime(session, ledger, price)
        return BedrockModel(model_id, 'us-east-1', ApprovalGate(self.grant_path(auth['id'], 'model')),
                            max_output_tokens=4096, max_prompt_chars=90000, client_factory=factory)


class BudgetedRuntime:
    def __init__(self, session, ledger, price):
        self.session, self.ledger, self.price = session, ledger, price

    def converse(self, **request):
        if request.get('modelId') != self.price['id'] or request.get('inferenceConfig', {}).get('maxTokens') != 4096:
            raise HarnessError('Inference request does not match the approved priced model/output cap')
        price = {'llm_input_per_million': self.price['input_per_million'],
                 'llm_output_per_million': self.price['output_per_million']}
        reservation = self.ledger.reserve('demo-generation', converse_reservation(request, price),
                                          details={'model_id': self.price['id']})
        from botocore.config import Config
        response = self.session.client('bedrock-runtime', region_name='us-east-1', config=Config(
            retries={'total_max_attempts': 1}, connect_timeout=10, read_timeout=180)).converse(**request)
        usage = response.get('usage', {})
        if any(type(usage.get(k)) is not int or usage[k] < 0 for k in ('inputTokens', 'outputTokens')):
            raise HarnessError('No valid usage receipt; the conservative budget reservation is retained')
        cost = (Decimal(usage['inputTokens']) * Decimal(str(price['llm_input_per_million']))
                + Decimal(usage['outputTokens']) * Decimal(str(price['llm_output_per_million']))) / 1_000_000
        self.ledger.settle(reservation, cost, details={'usage': usage, 'basis': 'Published model-token rate, not an invoice'})
        return response
