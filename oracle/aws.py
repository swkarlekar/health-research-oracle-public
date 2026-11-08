"""Optional paid adapters. Every network operation requires an unexpired exact grant.

This module imports no AWS SDK until a grant is checked. No environment variable
enables AWS. A grant is a local record of explicit user approval, not a dollar cap.
"""
from __future__ import annotations

import fcntl
import json
import math
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from .corpus import ApprovedCorpus, HarnessError, base


def _strict_json(text):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise HarnessError("Bedrock returned duplicate JSON fields")
            result[key] = value
        return result

    def constant(value):
        raise HarnessError("Bedrock returned a non-finite JSON number")

    return json.loads(text, object_pairs_hook=pairs, parse_constant=constant)


def _prompt_schema(value):
    """Omit schema annotations that models can mistakenly copy into the answer.

    Keep property names intact and validate against the original schema afterward.
    """
    if isinstance(value, list):
        return [_prompt_schema(item) for item in value]
    if not isinstance(value, dict):
        return value
    return {key: ({name: _prompt_schema(child) for name, child in item.items()}
                  if key in ('properties', '$defs', 'definitions') else _prompt_schema(item))
            for key, item in value.items() if key not in ('$schema', '$id', 'title')}


def _native_schema(value):
    """Stable Bedrock decoding schema; the full contract is still checked locally.

    Omit unsupported length/numeric constraints and regular expressions. Bedrock's
    constrained decoder can treat a JSON Schema substring pattern as a full match
    (a sentence-ending pattern produced punctuation-only findings). The complete
    original schema, including its patterns, is still validated locally.
    """
    value = _prompt_schema(value)
    def clean(node):
        if isinstance(node, list):
            return [clean(child) for child in node]
        if not isinstance(node, dict):
            return node
        result = {key: ({name: clean(child) for name, child in item.items()}
                        if key in ('properties', '$defs', 'definitions') else clean(item))
                  for key, item in node.items() if key not in (
                      'minLength', 'maxLength', 'minimum', 'maximum', 'multipleOf', 'uniqueItems', 'pattern')}
        enums = result.get('enum', [])
        if enums and all(isinstance(v, str) and re.fullmatch(r'E[1-9][0-9]*(\.Q[1-9][0-9]*)?', v) for v in enums):
            result.pop('enum', None)
        return result
    return clean(value)


def _sdk_call(client, operation, **kwargs):
    """Do not print vendor exception bodies, which can contain request content."""
    try:
        return getattr(client, operation)(**kwargs)
    except Exception as exc:
        if type(exc).__module__.startswith("botocore"):
            raise HarnessError(f"AWS {operation} failed; no response accepted and no retry attempted") from exc
        raise


class ApprovalGate:
    def __init__(self, path: Path):
        self.path = path

    def reserve(self, action, region, resource):
        if not self.path.is_file():
            raise HarnessError("AWS blocked: explicit user approval file is missing")
        with self.path.open("r+", encoding="utf-8") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            try:
                grant = json.load(stream)
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise HarnessError("AWS blocked: approval file is not valid JSON") from exc
            try:
                expiry = datetime.fromisoformat(grant["expires_at"])
                valid = (grant["user_approved"] is True and expiry.tzinfo is not None
                         and expiry > datetime.now(timezone.utc)
                         and grant["action"] == action and grant["region"] == region
                         and grant["resource"] == resource
                         and type(grant["max_calls"]) is int and 0 < grant["max_calls"] <= 20000
                         and type(grant.get("used_calls", 0)) is int
                         and 0 <= grant.get("used_calls", 0) < grant["max_calls"])
            except (KeyError, TypeError, ValueError):
                valid = False
            if not valid:
                raise HarnessError("AWS blocked: approval scope, expiry, or call budget does not match")
            # Reserve before sending; failures consume an attempt. SDK retries are disabled.
            grant["used_calls"] = grant.get("used_calls", 0) + 1
            stream.seek(0)
            stream.write(json.dumps(grant, indent=2) + "\n")
            stream.truncate()
            stream.flush()
            os.fsync(stream.fileno())


def aws_client(service, region):
    try:
        import boto3
        from botocore.config import Config
    except ImportError as exc:
        raise HarnessError("Install the optional AWS SDK only when preparing an approved AWS trial") from exc
    return boto3.client(service, region_name=region, config=Config(retries={"total_max_attempts": 1}, connect_timeout=10, read_timeout=120))


class AccountSession:
    """Lazy named-profile session, bound to the account in the explicit grant."""
    def __init__(self, profile, expected_account):
        if not isinstance(expected_account, str) or not re.fullmatch(r'\d{12}', expected_account):
            raise HarnessError('The paid approval must name the verified AWS account')
        self.profile, self.expected_account, self.session = profile, expected_account, None

    def client(self, service, **kwargs):
        if self.session is None:
            import boto3
            from botocore.config import Config
            session = boto3.Session(profile_name=self.profile)
            identity = session.client('sts', region_name='us-east-1',
                config=Config(retries={'total_max_attempts': 1})).get_caller_identity()
            if identity['Account'] != self.expected_account:
                raise HarnessError('AWS account differs from the explicitly approved trial account')
            self.session = session
        return self.session.client(service, **kwargs)


class TitanQueryEmbedder:
    """Only approved query embeddings, with a separate $0.01 stage inside $10.

    No corpus upload, ingestion, or managed retrieval. Cache hits never reach here.
    """
    RESOURCE = {'model_id': 'amazon.titan-embed-text-v2:0', 'dimensions': 1024,
                'normalize': True, 'max_input_chars': 1600}

    def __init__(self, gate, ledger, *, profile='oracle', client_factory=None,
                 stage_name='approved-local-query-embeddings', allowance_usd='0.01'):
        self.gate, self.ledger = gate, ledger
        self.profile, self.client_factory = profile, client_factory
        self.session = None
        self.stage_name, self.allowance_usd = stage_name, allowance_usd

    def embed(self, query):
        from decimal import Decimal
        from .local_vectors import vector_values
        from .policy import check_input
        check_input(query, {})
        self.gate.reserve('bedrock:InvokeModel', 'us-east-1', self.RESOURCE)
        # Use the checked regional price snapshot, not a guessed token ratio.
        from .corpus import ROOT
        prices = base.load_json(ROOT / 'config/aws-eval-prices.json')
        rate = Decimal(str(prices['embedding_per_million'])) / 1_000_000
        ledger = self.ledger.use_stage(self.stage_name, self.allowance_usd, isolated=True)
        reservation = ledger.reserve('titan-query-embedding', rate * 8192,
            details={'model_id': self.RESOURCE['model_id'], 'query_sha256': base.digest(query),
                     'basis': 'Full Titan V2 8192-token input ceiling; no output-token charge'})
        if self.client_factory:
            client = self.client_factory('bedrock-runtime', 'us-east-1')
        else:
            from botocore.config import Config
            if self.session is None:
                self.session = AccountSession(self.profile, base.load_json(self.gate.path).get('aws_account'))
            client = self.session.client('bedrock-runtime', region_name='us-east-1',
                config=Config(retries={'total_max_attempts': 1}, connect_timeout=10, read_timeout=60))
        response = _sdk_call(client, 'invoke_model', modelId=self.RESOURCE['model_id'],
            body=base.canonical({'inputText': query, 'dimensions': 1024, 'normalize': True, 'embeddingTypes': ['float']}),
            contentType='application/json', accept='application/json')
        stream = response['body']
        try:
            raw = stream.read(200001)
        finally:
            stream.close()
        if len(raw) > 200000:
            raise HarnessError('Titan embedding response exceeds the size cap; reservation retained')
        data = _strict_json(raw)
        tokens = data.get('inputTextTokenCount')
        if type(tokens) is not int or tokens < 0:
            raise HarnessError('Titan embedding response lacks a valid usage receipt; reservation retained')
        ledger.settle(reservation, rate * tokens, details={'input_tokens': tokens,
            'request_id': response.get('ResponseMetadata', {}).get('RequestId'),
            'basis': 'Calculated model-token price, not an invoice'})
        return vector_values(data.get('embedding'))


class BedrockModel:
    def __init__(self, model_id, region, gate: ApprovalGate, max_output_tokens=2400, max_prompt_chars=110000, client_factory=None):
        if not isinstance(model_id, str) or not model_id.strip() or not isinstance(region, str) or not region.strip():
            raise HarnessError("A specific Bedrock model ID and region are required")
        if type(max_output_tokens) is not int or not 1 <= max_output_tokens <= 8192:
            raise HarnessError("Bedrock output cap must be between 1 and 8192 tokens")
        if type(max_prompt_chars) is not int or not 1000 <= max_prompt_chars <= 110000:
            raise HarnessError("Bedrock prompt cap must be between 1000 and 110000 characters")
        self.model_id, self.region, self.gate = model_id, region, gate
        self.max_output_tokens, self.max_prompt_chars = max_output_tokens, max_prompt_chars
        self.calls = 0
        self.usage = []
        self.client_factory = client_factory

    def complete(self, system, payload, schema):
        if not isinstance(system, str) or not isinstance(schema, dict):
            raise HarnessError("Bedrock requires a text system policy and response schema")
        allowed = ', '.join(schema.get('properties', {}))
        system += ("\nReturn only the JSON object itself, without Markdown fences, commentary, reasoning, or duplicate keys. "
                   "Do not copy schema annotations or input metadata into the answer. "
                   + ("The ONLY permitted top-level keys are: " + allowed + '. ' if allowed else '')
                   + "Match this schema:\n" + base.canonical(_prompt_schema(schema)))
        text = base.canonical(payload)
        output_config = None
        if self.model_id.startswith('qwen.'):
            text += '\n/no_think'
        if self.model_id == 'qwen.qwen3-32b-v1:0':
            output_config = {'textFormat': {'type': 'json_schema', 'structure': {'jsonSchema': {
                'name': 'oracle_response', 'schema': base.canonical(_native_schema(schema))}}}}
        native_chars = len(base.canonical(output_config)) if output_config else 0
        if len(system) + len(text) + native_chars > self.max_prompt_chars:
            raise HarnessError("Prompt exceeds the approved per-call character cap")
        # Cap values are part of the exact approved resource, not caller-controlled afterward.
        resource = {"model_id": self.model_id, "max_output_tokens": self.max_output_tokens,
                    "max_prompt_chars": self.max_prompt_chars}
        self.gate.reserve("bedrock:Converse", self.region, resource)
        self.calls += 1
        result = _sdk_call((self.client_factory or aws_client)("bedrock-runtime", self.region), "converse",
            modelId=self.model_id, system=[{"text": system}],
            messages=[{"role": "user", "content": [{"text": text}]}],
            inferenceConfig={"maxTokens": self.max_output_tokens, "temperature": 0},
            **({'outputConfig': output_config} if output_config else {}))
        if not isinstance(result, dict) or result.get("stopReason") != "end_turn":
            raise HarnessError("Bedrock did not finish a complete answer; no answer accepted")
        try:
            message = result["output"]["message"]
            content = message["content"]
            if message.get("role", "assistant") != "assistant" or not isinstance(content, list) or not content:
                raise HarnessError("Bedrock returned an unexpected message shape")
            fragments = []
            for part in content:
                if isinstance(part, dict) and set(part) == {'reasoningContent'} and isinstance(part['reasoningContent'], dict):
                    continue  # Qwen can emit an empty block with /no_think. Never collect private reasoning.
                if not isinstance(part, dict) or set(part) != {'text'} or not isinstance(part['text'], str):
                    raise HarnessError("Bedrock returned unsupported non-text output")
                fragments.append(part['text'])
            if not fragments:
                raise HarnessError('Bedrock returned no answer text')
            text = "".join(fragments)
            if len(text) > 100000:
                raise HarnessError("Bedrock response exceeded the local size cap")
            response = _strict_json(text)
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise HarnessError("Bedrock returned invalid structured JSON") from exc
        usage = result.get("usage", {})
        metrics = result.get("metrics", {})
        if not isinstance(usage, dict) or not isinstance(metrics, dict):
            raise HarnessError("Bedrock returned malformed usage metadata")
        for field in ("inputTokens", "outputTokens", "totalTokens"):
            if field in usage and (type(usage[field]) is not int or usage[field] < 0):
                raise HarnessError("Bedrock returned malformed token usage")
        if usage.get("outputTokens", 0) > self.max_output_tokens:
            raise HarnessError("Bedrock output usage exceeded the approved token cap")
        self.usage.append({"prompt_tokens": usage.get("inputTokens"), "output_tokens": usage.get("outputTokens"),
                           "total_tokens": usage.get("totalTokens"), "latency_ms": metrics.get("latencyMs")})
        base.validate_schema(response, schema)
        return response


class BedrockCorpus(ApprovedCorpus):
    """Retrieve IDs from Bedrock; resolve quotes against the pinned local canonical copy."""
    def __init__(self, db, registry, knowledge_base_id, region, gate, max_pages=2, client_factory=None):
        if not isinstance(knowledge_base_id, str) or not knowledge_base_id.strip() or not isinstance(region, str) or not region.strip():
            raise HarnessError("A specific knowledge base ID and region are required")
        if type(max_pages) is not int or not 1 <= max_pages <= 5:
            raise HarnessError("Managed retrieval page cap must be between 1 and 5")
        super().__init__(db, registry)
        self.knowledge_base_id, self.region, self.gate = knowledge_base_id, region, gate
        self.max_pages = max_pages
        self.client_factory = client_factory
        self.last_retrieval = {}

    def packet(self, question, scope, limit=4):
        packet = super().packet(question, scope, limit)
        packet["retrieval"].update({"engine": "bedrock_knowledge_bases", "mode": "semantic",
                                    "semantic_retrieval": True, "managed_retrieval": self.last_retrieval})
        packet["packet_id"] = base.packet_hash(packet)
        return packet

    def expand(self, packet, query, limit=4):
        packet = super().expand(packet, query, limit)
        packet['retrieval']['additional_managed_retrieval'] = self.last_retrieval
        packet['packet_id'] = base.packet_hash(packet)
        return packet

    def search(self, query, limit=4, include_indexes=False):
        if type(limit) is not int or not 1 <= limit <= 12:
            raise HarnessError("Retrieval limit must be between 1 and 12")
        from .policy import check_input
        check_input(query, {})
        snapshot = self.summary()["snapshot_sha256"]
        request = {"knowledgeBaseId": self.knowledge_base_id, "retrievalQuery": {"text": query},
                   "retrievalConfiguration": {"vectorSearchConfiguration": {
                       "numberOfResults": 30, "overrideSearchType": "SEMANTIC",
                       "filter": {"equals": {"key": "snapshot_sha256", "value": snapshot}}}}}
        found, seen, tokens = [], set(), set()
        next_token, pages = None, 0
        client = None
        while pages < self.max_pages:
            self.gate.reserve("bedrock:Retrieve", self.region,
                              {"knowledge_base_id": self.knowledge_base_id, "max_results": 30,
                               "snapshot_sha256": snapshot})
            if client is None:
                client = (self.client_factory or aws_client)("bedrock-agent-runtime", self.region)
            response = _sdk_call(client, "retrieve", **request)
            pages += 1
            if not isinstance(response, dict) or not isinstance(response.get("retrievalResults"), list):
                raise HarnessError("Managed retrieval returned a malformed result list")
            if response.get("guardrailAction", "NONE") != "NONE":
                raise HarnessError("Managed retrieval was blocked by a guardrail; no evidence accepted")
            for hit in response["retrievalResults"]:
                if not isinstance(hit, dict) or not isinstance(hit.get("metadata"), dict) or not isinstance(hit.get("content"), dict):
                    raise HarnessError("Managed retrieval returned a malformed passage")
                meta, content = hit["metadata"], hit["content"]
                if content.get("type", "TEXT") != "TEXT" or not isinstance(content.get("text"), str):
                    raise HarnessError("Managed retrieval returned unsupported non-text evidence")
                if meta.get("snapshot_sha256") != snapshot:
                    raise HarnessError("Managed retrieval returned an unexpected corpus snapshot")
                if not isinstance(meta.get("chunk_id"), str) or not meta["chunk_id"]:
                    raise HarnessError("Managed retrieval omitted its canonical chunk identity")
                item = self.chunk(meta["chunk_id"])
                if meta.get("text_sha256") != item["text_sha256"]:
                    raise HarnessError("Managed retrieval metadata does not match canonical text")
                for field in ("document_id", "source_url"):
                    if field in meta and meta[field] != item[field]:
                        raise HarnessError("Managed retrieval returned mismatched source identity")
                score = hit.get("score")
                if score is not None and (type(score) not in (int, float) or not math.isfinite(score) or score < 0):
                    raise HarnessError("Managed retrieval returned a malformed relevance score")
                if item["chunk_id"] not in seen:
                    found.append(item)
                    seen.add(item["chunk_id"])
            next_token = response.get("nextToken")
            if next_token is not None and (not isinstance(next_token, str) or not next_token):
                raise HarnessError("Managed retrieval returned an invalid pagination token")
            if not next_token or len(found) >= limit:
                break
            if next_token in tokens:
                raise HarnessError("Managed retrieval repeated a pagination token")
            tokens.add(next_token)
            request["nextToken"] = next_token
        self.last_retrieval = {"pages_requested": pages, "page_cap": self.max_pages,
                               "pagination_limit_reached": bool(next_token and pages == self.max_pages and len(found) < limit),
                               "canonical_hits": len(found), "returned_hits": min(len(found), limit),
                               "query_sha256": base.digest(query)}
        return found[:limit]


def start_ingestion_job(knowledge_base_id, data_source_id, snapshot_sha256, region, gate, client_factory=None):
    """Explicitly approved managed ingestion; never called by export or query commands.

    The snapshot scopes local approval/idempotency, not the remote S3 prefix. The
    user must review the actual data-source location before approving ingestion.
    """
    if not re.fullmatch(r"[0-9A-Za-z]{10}", knowledge_base_id or "") or not re.fullmatch(r"[0-9A-Za-z]{10}", data_source_id or ""):
        raise HarnessError("Knowledge base and data source IDs must each contain 10 alphanumeric characters")
    if not isinstance(snapshot_sha256, str) or not re.fullmatch(r"[a-f0-9]{64}", snapshot_sha256):
        raise HarnessError("A specific canonical corpus snapshot hash is required")
    resource = {"knowledge_base_id": knowledge_base_id, "data_source_id": data_source_id,
                "snapshot_sha256": snapshot_sha256}
    gate.reserve("bedrock:StartIngestionJob", region, resource)
    token = "oracle-" + base.digest(base.canonical(resource))
    result = _sdk_call((client_factory or aws_client)("bedrock-agent", region), "start_ingestion_job",
                       knowledgeBaseId=knowledge_base_id, dataSourceId=data_source_id, clientToken=token,
                       description="Approved oracle corpus ingestion " + snapshot_sha256)
    job = result.get("ingestionJob") if isinstance(result, dict) else None
    if not isinstance(job, dict) or job.get("knowledgeBaseId") != knowledge_base_id or job.get("dataSourceId") != data_source_id:
        raise HarnessError("Ingestion response did not match the approved data source")
    if not isinstance(job.get("ingestionJobId"), str) or not job["ingestionJobId"] or job.get("status") not in {"STARTING", "IN_PROGRESS", "COMPLETE", "FAILED", "STOPPING", "STOPPED"}:
        raise HarnessError("Ingestion response omitted a valid job identity or status")
    # Datetimes are converted so the caller can save a local audit record as JSON.
    return {key: value.isoformat() if isinstance(value, datetime) else value for key, value in job.items()}


def export_ingestion(corpus, output: Path):
    """One existing canonical chunk per file. Configure KB chunkingStrategy=NONE."""
    output.mkdir(parents=True, exist_ok=True)
    files = []
    snapshot = corpus.summary()["snapshot_sha256"]
    for row in corpus.connection.execute("SELECT chunk_id FROM chunks ORDER BY chunk_id"):
        item = base.Corpus.chunk(corpus, row[0])
        if not corpus.allowed(item):
            continue
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", item["chunk_id"]):
            raise HarnessError("Canonical chunk ID cannot be used as an ingestion filename")
        name = item["chunk_id"] + ".txt"
        metadata = {"metadataAttributes": {key: item[key] for key in
                                           ("chunk_id", "document_id", "text_sha256", "source_url")}}
        metadata["metadataAttributes"]["snapshot_sha256"] = snapshot
        if len(base.canonical(metadata["metadataAttributes"]).encode("utf-8")) > 1024:
            raise HarnessError("Chunk metadata exceeds the S3 Vectors Knowledge Bases custom-metadata limit")
        for filename, content in [(name, item["text"]), (name + ".metadata.json", base.canonical(metadata))]:
            data = content.encode("utf-8")
            (output / filename).write_bytes(data)
            files.append({"name": filename, "bytes": len(data), "sha256": base.hashlib.sha256(data).hexdigest()})
    manifest = {"snapshot_sha256": snapshot, "file_count": len(files), "total_bytes": sum(f["bytes"] for f in files),
                "chunking_strategy": "NONE", "files": files}
    base.emit(manifest, output / "upload-manifest.json")
    return manifest


def upload_ingestion(directory: Path, bucket, prefix, region, gate, client_factory=None, workers=1, dns_retries=0):
    if type(workers) is not int or not 1 <= workers <= 16:
        raise HarnessError('Upload workers must be between 1 and 16')
    if type(dns_retries) is not int or not 0 <= dns_retries <= 1:
        raise HarnessError('At most one explicit DNS upload retry is allowed')
    manifest = base.load_json(directory / "upload-manifest.json")
    if not isinstance(manifest, dict) or not isinstance(manifest.get("files"), list) or manifest.get("chunking_strategy") != "NONE":
        raise HarnessError("Upload manifest is malformed")
    if not bucket or not prefix.strip("/"):
        raise HarnessError("Use a specific bucket and nonempty dedicated prefix")
    prefix = prefix.strip("/") + "/"
    resource = {"bucket": bucket, "prefix": prefix,
                "manifest_sha256": base.digest(base.canonical(manifest))}
    # Validate EVERY local file before the first paid operation.
    prepared, names = [], set()
    for item in manifest["files"]:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str) or item["name"] in names:
            raise HarnessError("Upload manifest contains an invalid or duplicate filename")
        names.add(item["name"])
        path = (directory / item["name"]).resolve()
        if path.parent != directory.resolve():
            raise HarnessError("Upload file escaped the export directory")
        data = path.read_bytes()
        if base.hashlib.sha256(data).hexdigest() != item.get("sha256") or len(data) != item.get("bytes"):
            raise HarnessError("Upload file escaped the export directory or changed after review")
        prepared.append((item, data))
    if manifest.get("file_count") != len(prepared) or manifest.get("total_bytes") != sum(len(data) for _, data in prepared):
        raise HarnessError("Upload manifest file or byte count is inconsistent")
    from threading import Lock
    client, client_lock = None, Lock()
    def upload(entry):
        nonlocal client
        item, data = entry
        for attempt in range(dns_retries + 1):
            gate.reserve("s3:PutObject", region, resource)
            with client_lock:
                if client is None:
                    client = (client_factory or aws_client)("s3", region)
            try:
                _sdk_call(client, "put_object", Bucket=bucket, Key=prefix + item["name"], Body=data,
                          ServerSideEncryption="AES256",
                          ContentType="application/json" if item["name"].endswith(".json") else "text/plain; charset=utf-8")
                return
            except HarnessError as exc:
                # DNS failed before a socket was opened: no request reached S3.
                # Other failures are never retried. Every attempt uses a grant.
                import socket
                seen, pending, dns_failure = set(), [exc], False
                while pending:
                    cause = pending.pop()
                    if id(cause) in seen:
                        continue
                    seen.add(id(cause))
                    dns_failure |= isinstance(cause, socket.gaierror)
                    pending.extend(c for c in (cause.__cause__, cause.__context__) if c is not None)
                if not dns_failure or attempt == dns_retries:
                    raise
    if workers == 1:
        for entry in prepared:
            upload(entry)
    else:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=workers) as executor:
            for _ in executor.map(upload, prepared):
                pass
    return {"uploaded_files": len(manifest["files"]), "bucket": bucket, "prefix": prefix}
