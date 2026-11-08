"""Run the real AWS adapters with API-shaped local monkeypatches.

The search stub uses the lexical baseline, not Titan. The model stub emits only
an attributed excerpt and an explicit uncertainty; it simulates no legal answer.
No enabled AWS approval file or SDK client is created.
"""
from __future__ import annotations

import json
from unittest.mock import patch

from .aws import BedrockCorpus, BedrockModel
from .context import ExpandedCorpus
from .corpus import ApprovedCorpus, base
from .harness import Harness


class SimulationGate:
    def __init__(self):
        self.calls = []

    def reserve(self, action, region, resource):
        self.calls.append({"action": action, "region": region, "resource": resource, "simulated": True})


class SimulatedBedrock:
    def __init__(self, corpus):
        self.corpus = corpus
        self.retrieve_calls = 0
        self.converse_calls = 0

    def retrieve(self, **request):
        self.retrieve_calls += 1
        found = self.corpus.search(request["retrievalQuery"]["text"], 6)
        snapshot = self.corpus.summary()["snapshot_sha256"]
        return {"retrievalResults": [{"content": {"type": "TEXT", "text": item["text"]},
                    "location": {"type": "S3", "s3Location": {"uri": f"s3://offline-demo/corpus/{item['chunk_id']}.txt"}},
                    "metadata": {"chunk_id": item["chunk_id"], "document_id": item["document_id"],
                                 "text_sha256": item["text_sha256"], "snapshot_sha256": snapshot,
                                 "source_url": item["source_url"]}, "score": 0.5} for item in found],
                "guardrailAction": "NONE"}

    def converse(self, **request):
        self.converse_calls += 1
        text = request["messages"][0]["content"][0]["text"]
        if request['modelId'].startswith('qwen.'):
            text = text.removesuffix('\n/no_think')
        payload = json.loads(text)
        item = payload["evidence"][0]
        span = item["passages"][0]
        answer = {"status": "currency_unverified", "scope": payload["scope"], "findings": [],
                  "quotes": [span['quote_id']],
                  "uncertainties": [{"kind": "offline_simulation", "description": "This is an API-contract demonstration using lexical retrieval and simulated model responses. It does not establish relevance, legal applicability, or currentness.",
                                     "evidence_ids": [item["evidence_id"]], "question_for_user": None}],
                  "clarifying_questions": []}
        if "draft" in payload:
            answer = {"response": answer, "metadata_proxy_evidence_ids": [], "additional_query": None,
                      "checks": {"support": False, "applicability": False, "responsiveness": False,
                                 "dates_and_versions": False, "conflicts_and_exceptions": False, "boundary_safe": True},
                      "issues": ["Offline stub: no substantive model judgment was performed."]}
        return {"output": {"message": {"role": "assistant", "content": [{"text": base.canonical(answer)}]}},
                "stopReason": "end_turn", "usage": {"inputTokens": 0, "outputTokens": 0, "totalTokens": 0},
                "metrics": {"latencyMs": 0}}


def run_api_check(db, registry, graph, question, scope, limit=2, model_id="amazon.nova-lite-v1:0", trace=None):
    """Borrow the caller's graph; own only the two retrieval connections opened here."""
    lexical = ApprovedCorpus(db, registry)
    gate = SimulationGate()
    stub = SimulatedBedrock(lexical)
    corpus, retriever = None, None
    try:
        def client(service, region):
            if service not in {"bedrock-runtime", "bedrock-agent-runtime"}:
                raise AssertionError("Unexpected AWS client requested in offline simulation")
            return stub
        with patch("oracle.aws.aws_client", side_effect=client):
            retriever = BedrockCorpus(db, registry, "DEMO123456", "us-east-1", gate)
            corpus = ExpandedCorpus(retriever, graph)
            model = BedrockModel(model_id, "us-east-1", gate, max_output_tokens=4096)
            result = Harness(corpus, model, limit, trace=trace).run(question, scope)
        result["simulation"] = {"enabled": True, "network_used": False, "actual_embeddings_generated": False,
                                "actual_llm_inference": False, "retrieval_backing": "local_sqlite_bm25",
                                "aws_retrieve_calls": stub.retrieve_calls, "aws_converse_calls": stub.converse_calls,
                                "reserved_mock_calls": gate.calls,
                                "purpose": "API-contract and orchestration demonstration, not a research answer or quality benchmark"}
        return result
    finally:
        if retriever is not None:
            retriever.close()
        lexical.close()
