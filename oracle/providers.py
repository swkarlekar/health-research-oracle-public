"""Loopback-only model calls, with no download or cloud fallback."""
from __future__ import annotations

import json
import math
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

from .corpus import HarnessError, base


def server_url(value):
    """Validate a loopback base URL without contacting it."""
    if not isinstance(value, str):
        raise HarnessError('Enter a local model server URL')
    try:
        parsed = urllib.parse.urlsplit(value)
    except ValueError as exc:
        raise HarnessError('Enter a valid local model server URL') from exc
    if parsed.path not in ('', '/', '/v1'):
        raise HarnessError('Use the local server base URL, without an API path')
    base.local_endpoint(urllib.parse.urlunsplit(parsed._replace(path='/v1/chat/completions')))
    return urllib.parse.urlunsplit(parsed._replace(path='')).rstrip('/')


def local_json(url):
    """Read bounded local metadata; never follow redirects or use a proxy."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), base.NoRedirect())
    with opener.open(url, timeout=4) as stream:
        raw = stream.read(1_000_001)
    if len(raw) > 1_000_000:
        raise HarnessError('Local model metadata exceeded the size cap')
    return json.loads(raw)


def positive_size(value):
    # bool, strings, negative numbers and non-finite JSON extensions are unknown.
    if type(value) is int and value > 0:
        return value
    if type(value) is float and math.isfinite(value) and value > 0:
        return value
    return None


def physical_memory():
    try:
        memory = positive_size(os.sysconf('SC_PHYS_PAGES') * os.sysconf('SC_PAGE_SIZE'))
        if memory:
            return memory
    except (ValueError, OSError, AttributeError):
        pass
    if sys.platform == 'darwin':
        try:
            result = subprocess.run(['/usr/sbin/sysctl', '-n', 'hw.memsize'], capture_output=True, timeout=2, check=True)
            return positive_size(int(result.stdout))
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
    return None


def discover_local(provider, url):
    """List already installed models using metadata GETs only."""
    if provider not in ('ollama', 'lmstudio', 'openai-local'):
        raise HarnessError('Choose Ollama, LM Studio, or another local OpenAI-compatible server')
    url = server_url(url)
    try:
        if provider == 'ollama':
            rows = [{'id': m['name'], 'name': m['name'], 'size_bytes': m.get('size'),
                     'details': m.get('details', {})} for m in local_json(url + '/api/tags')['models']]
        elif provider == 'lmstudio':
            try:
                rows = [{'id': m['key'], 'name': m.get('display_name') or m['key'],
                         'size_bytes': m.get('size_bytes'), 'details': {'parameters': m.get('params_string')}}
                        for m in local_json(url + '/api/v1/models')['models'] if m.get('type') == 'llm']
            except urllib.error.HTTPError as exc:
                if exc.code != 404:
                    raise
                rows = [{'id': m['id'], 'name': m['id'], 'size_bytes': None, 'details': {}}
                        for m in local_json(url + '/v1/models')['data']]
        else:
            rows = [{'id': m['id'], 'name': m['id'], 'size_bytes': None, 'details': {}}
                    for m in local_json(url + '/v1/models')['data']]
        if any(not isinstance(row['id'], str) or not row['id'].strip() for row in rows):
            raise ValueError('Invalid model identity')
    except (urllib.error.URLError, TimeoutError, OSError, KeyError, TypeError, ValueError, AttributeError) as exc:
        raise HarnessError('Could not list local models. Start the local server and check its URL/authentication settings.') from exc
    memory = physical_memory()
    limit = memory * .35 if memory else 8 * 1024**3
    for row in rows:
        size = row['size_bytes'] = positive_size(row['size_bytes'])
        row['blocked'] = size is not None and size > limit
        row['warning'] = ('Too large for this computer’s conservative memory limit; choose a smaller model.' if row['blocked']
                          else 'Model size is unavailable; verify memory requirements in your local application.' if size is None
                          else 'Disk size is only a lower bound for runtime memory. Larger contexts need more memory.')
    return {'models': rows, 'physical_memory_bytes': memory, 'server_url': url,
            'note': 'Lists existing models only. Discovery never loads or downloads a model.'}


def guard_local_model(provider, url, model, *, size_acknowledged=False, inference=False):
    """Shared UI/CLI guard; explicit acknowledgment never overrides a known large model."""
    if not isinstance(model, str) or not model.strip():
        raise HarnessError('Refresh local models and select an installed model')
    rows = discover_local(provider, url)['models']
    chosen = next((m for m in rows if m['id'] == model), None)
    if chosen is None:
        raise HarnessError('Refresh local models and select an installed model')
    if chosen['blocked']:
        raise HarnessError(chosen['warning'])
    if chosen['size_bytes'] is None and size_acknowledged is not True:
        raise HarnessError('Confirm that the selected local model fits in memory, or choose a model with known size')
    if inference and sys.platform == 'darwin':
        try:
            pressure = subprocess.run(['/usr/bin/memory_pressure', '-Q'], capture_output=True, timeout=2, check=True)
            free = re.search(rb'memory free percentage:\s*(\d+)%', pressure.stdout, re.I)
            if not free or not 0 <= int(free[1]) <= 100:
                raise HarnessError('Could not verify system memory pressure; use Bedrock or check the local runtime before inference.')
            if int(free[1]) < 25:
                raise HarnessError('System memory is under pressure. Use Bedrock or free memory before local inference.')
        except (OSError, subprocess.SubprocessError) as exc:
            raise HarnessError('Could not verify system memory pressure; use Bedrock or check the local runtime before inference.') from exc
    return chosen


def ollama_identity(model='qwen3.6:latest'):
    """Read local runtime/weights metadata only; never loads or downloads a model."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), base.NoRedirect())
    values = {}
    for endpoint in ('tags', 'version'):
        with opener.open('http://127.0.0.1:11434/api/' + endpoint, timeout=10) as stream:
            raw = stream.read(500001)
        if len(raw) > 500000:
            raise HarnessError('Local model metadata exceeded the size cap')
        values[endpoint] = json.loads(raw)
    entry = next((m for m in values['tags']['models'] if m.get('name') == model), None)
    if entry is None or not entry.get('digest'):
        raise HarnessError('The comparison requires the installed local model and its weight digest')
    return {'name': model, 'digest': entry['digest'], 'size': entry.get('size'),
            'details': entry.get('details'), 'ollama_version': values['version']['version']}


class LocalModel:
    def __init__(self, model, endpoint="http://127.0.0.1:11434/api/chat", timeout=180,
                 max_output_tokens=4096, max_prompt_chars=110000, context_tokens=32768,
                 strict_context=False, keep_alive='2m'):
        if not model or not math.isfinite(timeout) or timeout <= 0:
            raise HarnessError("A local model name and positive finite timeout are required")
        if type(max_output_tokens) is not int or not 1 <= max_output_tokens <= 8192 or type(max_prompt_chars) is not int or not 1000 <= max_prompt_chars <= 110000:
            raise HarnessError("Model output or prompt limit is outside the supported range")
        parts = urllib.parse.urlsplit(endpoint)
        # Reuse strict loopback, credentials, and redirect policy from the corpus harness.
        base.local_endpoint(urllib.parse.urlunsplit(parts._replace(path="/v1/chat/completions")))
        if parts.path not in {"/api/chat", "/v1/chat/completions"}:
            raise HarnessError("Use /api/chat or /v1/chat/completions on a local model server")
        self.model, self.endpoint, self.timeout = model, endpoint, timeout
        self.max_output_tokens, self.max_prompt_chars = max_output_tokens, max_prompt_chars
        if type(context_tokens) is not int or context_tokens not in (8192, 16384, 32768):
            raise HarnessError('Local context must be 8,192, 16,384, or 32,768 tokens')
        self.context_tokens, self.strict_context, self.keep_alive = context_tokens, strict_context, keep_alive
        self.calls = 0
        self.usage = []

    def complete(self, system, payload, schema):
        messages = [{"role": "system", "content": system + "\nReturn only JSON matching this schema:\n" + base.canonical(schema)},
                    {"role": "user", "content": base.canonical(payload)}]
        if sum(len(m["content"]) for m in messages) > self.max_prompt_chars:
            raise HarnessError("Prompt exceeds the local character budget; reduce retrieval limit. Evidence was not truncated.")
        if self.strict_context and (sum(len(m['content'].encode('utf-8')) for m in messages)
                                    + self.max_output_tokens + 512 > self.context_tokens):
            raise HarnessError('Evidence exceeds the conservative local context budget. Choose a larger context on a small model or use Bedrock; no inference or truncation was attempted.')
        native = self.endpoint.endswith("/api/chat")
        if native:
            body = {"model": self.model, "messages": messages, "format": schema,
                    "stream": False, "think": False, "keep_alive": self.keep_alive,
                    "options": {"temperature": 0, "num_predict": self.max_output_tokens, "num_ctx": self.context_tokens}}
        else:
            body = {"model": self.model, "messages": messages, "temperature": 0, "stream": False,
                    "max_tokens": self.max_output_tokens,
                    "response_format": {"type": "json_schema", "json_schema": {"name": "research", "strict": True, "schema": schema}}}
        request = urllib.request.Request(self.endpoint, data=base.canonical(body).encode(),
                                         headers={"Content-Type": "application/json"}, method="POST")
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), base.NoRedirect())
        self.calls += 1
        try:
            with opener.open(request, timeout=self.timeout) as stream:
                raw = stream.read(5_000_001)
            if len(raw) > 5_000_000:
                raise HarnessError("Model response exceeded the size cap")
            data = json.loads(raw)
            self.usage.append({"prompt_tokens": data.get("prompt_eval_count") if native else data.get("usage", {}).get("prompt_tokens"),
                               "output_tokens": data.get("eval_count") if native else data.get("usage", {}).get("completion_tokens")})
            if native:
                if data.get("done_reason") == "length":
                    raise HarnessError("Local model output hit the token cap")
                text = data["message"]["content"]
            else:
                if data["choices"][0].get("finish_reason") != "stop":
                    raise HarnessError("Local model output was incomplete")
                text = data["choices"][0]["message"]["content"]
            result = json.loads(text)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise HarnessError("Local model unavailable or timed out; no cloud fallback was attempted") from exc
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise HarnessError("Local model did not return valid structured JSON") from exc
        base.validate_schema(result, schema)
        return result
