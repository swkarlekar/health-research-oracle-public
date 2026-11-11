"""Offline AWS API contracts; synthetic passages test plumbing, not legal quality.

Standard-library mocks run without boto3. When the optional SDK is installed,
Stubber additionally validates AWS request/response shapes with dummy credentials
and every socket connection blocked. No AWS request is made by these tests.
"""
import json
from datetime import datetime, timezone
from unittest import skipIf
from unittest.mock import Mock, patch

from support import Fixture, SCOPE, answer

from oracle.aws import ApprovalGate, BedrockCorpus, BedrockModel, export_ingestion, start_ingestion_job, upload_ingestion, _prompt_schema, _native_schema
from oracle.corpus import HarnessError, base
from oracle.harness import Harness

try:
    import boto3
    from botocore.config import Config
    from botocore.stub import Stubber
except ImportError:
    boto3 = None


def converse_response(value):
    return {"output": {"message": {"role": "assistant", "content": [{"text": json.dumps(value)}]}},
            "stopReason": "end_turn", "usage": {"inputTokens": 120, "outputTokens": 40, "totalTokens": 160},
            "metrics": {"latencyMs": 10}}


class AwsMockContracts(Fixture):
    def test_native_decoder_preserves_bounded_evidence_identity_allowlist(self):
        schema = {'type': 'string', 'enum': ['E1', 'E7']}
        native = _native_schema(schema)
        self.assertEqual(native['enum'], ['E1', 'E7'])
        with self.assertRaises(HarnessError):
            base.validate_schema('E2', native)

    def test_native_decoder_omits_substring_patterns_but_local_validation_retains_them(self):
        schema = {'type': 'string', 'pattern': r'[.!?]$'}
        self.assertNotIn('pattern', _native_schema(schema))
        self.assertIn('pattern', schema)
        base.validate_schema('A complete finding.', schema)
        with self.assertRaises(HarnessError):
            base.validate_schema('An unfinished finding', schema)

    def test_qwen_native_output_uses_supported_schema_without_weakening_local_checks(self):
        schema = {'type': 'object', 'additionalProperties': False, 'required': ['quotes'],
            'properties': {'quotes': {'type': 'array', 'maxItems': 2, 'uniqueItems': True,
                'items': {'type': 'string', 'minLength': 1, 'enum': ['E1.Q1', 'E1.Q2']}}}}
        native = _native_schema(schema)
        self.assertEqual(native['properties']['quotes']['items']['enum'], ['E1.Q1', 'E1.Q2'])
        self.assertNotIn('minLength', native['properties']['quotes']['items'])
        self.assertEqual(native['properties']['quotes']['maxItems'], 2)
        fake = Mock()
        fake.converse.return_value = converse_response({'quotes': ['invented']})
        resource = {'model_id': 'qwen.qwen3-32b-v1:0', 'max_output_tokens': 2400, 'max_prompt_chars': 110000}
        with patch('oracle.aws.aws_client', return_value=fake), self.assertRaises(HarnessError):
            BedrockModel(resource['model_id'], 'us-east-1', self.grant(resource=resource)).complete('policy', {}, schema)
        output = fake.converse.call_args.kwargs['outputConfig']['textFormat']
        self.assertEqual(output['type'], 'json_schema')
        self.assertEqual(json.loads(output['structure']['jsonSchema']['schema']), native)

    def test_prompt_omits_schema_annotations_but_preserves_property_names_and_validation(self):
        schema = {'$id': 'annotation', '$schema': 'annotation', 'title': 'Annotation', 'type': 'object',
                  'additionalProperties': False, 'properties': {'title': {'type': 'string'},
                  'response': {'$id': 'nested-annotation', 'type': 'object'}}}
        cleaned = _prompt_schema(schema)
        self.assertNotIn('$id', cleaned)
        self.assertNotIn('$id', cleaned['properties']['response'])
        self.assertIn('title', cleaned['properties'])
        self.assertIn('$id', schema)
        fake = Mock()
        fake.converse.return_value = converse_response({'title': 'Answer', '$id': 'annotation'})
        with patch('oracle.aws.aws_client', return_value=fake), self.assertRaises(HarnessError):
            BedrockModel('synthetic-model', 'us-east-1', self.grant()).complete('policy', {}, schema)

    def test_reasoning_content_is_discarded_and_never_used_as_answer_json(self):
        fake = Mock()
        response = converse_response({'ok': True})
        response['output']['message']['content'].append({'reasoningContent': {'reasoningText': {'text': 'PRIVATE_SENTINEL'}}})
        fake.converse.return_value = response
        model = BedrockModel('synthetic-model', 'us-east-1', self.grant())
        with patch('oracle.aws.aws_client', return_value=fake):
            self.assertEqual(model.complete('policy', {}, {'type': 'object'}), {'ok': True})
        self.assertNotIn('PRIVATE_SENTINEL', json.dumps(model.usage))
        response['output']['message']['content'] = [{'reasoningContent': {'reasoningText': {'text': '{"ok":true}'}}}]
        with patch('oracle.aws.aws_client', return_value=fake), self.assertRaises(HarnessError):
            model.complete('policy', {}, {'type': 'object'})

    def test_opt_in_dns_retry_reserves_another_attempt_but_does_not_retry_service_errors(self):
        import socket
        from botocore.exceptions import EndpointConnectionError, ClientError
        directory = self.root / 'dns-export'
        manifest = export_ingestion(self.corpus, directory)
        resource = {'bucket': 'synthetic-corpus', 'prefix': 'corpus/',
                    'manifest_sha256': base.digest(base.canonical(manifest))}
        gate = self.grant('s3:PutObject', resource, max_calls=manifest['file_count'] + 1)
        fake = Mock()
        attempts = []
        def put(**request):
            attempts.append(request['Key'])
            if len(attempts) == 1:
                try:
                    raise socket.gaierror('simulated DNS failure')
                except socket.gaierror as cause:
                    raise EndpointConnectionError(endpoint_url='https://synthetic.invalid') from cause
            return {}
        fake.put_object.side_effect = put
        with patch('oracle.aws.aws_client', return_value=fake):
            upload_ingestion(directory, 'synthetic-corpus', 'corpus/', 'us-east-1', gate, dns_retries=1)
        self.assertEqual(len(attempts), manifest['file_count'] + 1)
        self.assertEqual(json.loads(gate.path.read_text())['used_calls'], len(attempts))
        self.assertEqual(attempts[0], attempts[1])
        gate = self.grant('s3:PutObject', resource, max_calls=manifest['file_count'] * 2)
        fake.reset_mock()
        fake.put_object.side_effect = ClientError({'Error': {'Code': 'AccessDenied'}}, 'PutObject')
        with patch('oracle.aws.aws_client', return_value=fake), self.assertRaises(HarnessError):
            upload_ingestion(directory, 'synthetic-corpus', 'corpus/', 'us-east-1', gate, dns_retries=1)
        fake.put_object.assert_called_once()

    def retrieval_resource(self):
        return {"knowledge_base_id": "KB12345678", "max_results": 30,
                "snapshot_sha256": self.corpus.summary()["snapshot_sha256"]}

    def retrieval_corpus(self, max_pages=2):
        return BedrockCorpus(self.db, self.registry, "KB12345678", "us-east-1",
                             self.grant("bedrock:Retrieve", self.retrieval_resource()), max_pages=max_pages)

    def hit(self, chunk_id="chunk0"):
        item = self.corpus.chunk(chunk_id)
        return {"content": {"type": "TEXT", "text": item["text"]}, "score": 0.95,
                "location": {"type": "S3", "s3Location": {"uri": "s3://synthetic-corpus/corpus/" + chunk_id + ".txt"}},
                "metadata": {"chunk_id": chunk_id, "document_id": item["document_id"],
                             "text_sha256": item["text_sha256"], "source_url": item["source_url"],
                             "snapshot_sha256": self.corpus.summary()["snapshot_sha256"]}}

    def test_converse_malformed_output_and_stop_conditions_fail_closed(self):
        schema = {"type": "object", "additionalProperties": False, "required": ["ok"],
                  "properties": {"ok": {"type": "boolean"}}}
        valid = converse_response({"ok": True})
        cases = [[], {}, dict(valid, stopReason="max_tokens"), dict(valid, stopReason="guardrail_intervened"),
                 dict(valid, output={"message": {"role": "user", "content": [{"text": '{"ok":true}'}]}}),
                 dict(valid, output={"message": {"content": [{"toolUse": {"name": "external"}}]}}),
                 dict(valid, output={"message": {"content": [{"text": 3}]}}),
                 dict(valid, output={"message": {"content": [{"text": '{"ok":true,"ok":false}'}]}}),
                 dict(valid, output={"message": {"content": [{"text": '{"ok":NaN}'}]}}),
                 dict(valid, usage={"inputTokens": -1}), dict(valid, usage={"outputTokens": 9000})]
        for index, response in enumerate(cases):
            with self.subTest(case=index):
                fake = Mock()
                fake.converse.return_value = response
                model = BedrockModel("synthetic-model", "us-east-1", self.grant())
                with patch("oracle.aws.aws_client", return_value=fake), self.assertRaises(HarnessError):
                    model.complete("test policy", {}, schema)

    def test_prompt_cap_blocks_before_approval_reservation_or_sdk(self):
        gate = self.grant()
        model = BedrockModel("synthetic-model", "us-east-1", gate)
        with patch("oracle.aws.aws_client") as client, self.assertRaises(HarnessError):
            model.complete("x" * 110001, {}, {})
        client.assert_not_called()
        self.assertEqual(json.loads(gate.path.read_text())["used_calls"], 0)

    def test_malformed_approval_fails_before_sdk(self):
        path = self.root / "bad-grant.json"
        path.write_text("not JSON")
        with patch("oracle.aws.aws_client") as client, self.assertRaises(HarnessError):
            BedrockModel("synthetic-model", "us-east-1", ApprovalGate(path)).complete("policy", {}, {})
        client.assert_not_called()

    def test_retrieve_paginates_with_one_grant_reservation_per_request(self):
        corpus = self.retrieval_corpus()
        self.addCleanup(corpus.close)
        fake = Mock()
        fake.retrieve.side_effect = [{"retrievalResults": [], "nextToken": "page-two"},
                                     {"retrievalResults": [self.hit()]}]
        with patch("oracle.aws.aws_client", return_value=fake):
            result = corpus.search("synthetic alpha", limit=1)
        self.assertEqual(result[0]["chunk_id"], "chunk0")
        self.assertEqual(fake.retrieve.call_count, 2)
        self.assertNotIn("nextToken", fake.retrieve.call_args_list[0].kwargs)
        self.assertEqual(fake.retrieve.call_args_list[1].kwargs["nextToken"], "page-two")
        self.assertEqual(json.loads(corpus.gate.path.read_text())["used_calls"], 2)
        self.assertEqual(corpus.last_retrieval["pages_requested"], 2)

    def test_retrieve_page_limit_is_explicit_not_unbounded(self):
        corpus = self.retrieval_corpus(max_pages=1)
        self.addCleanup(corpus.close)
        fake = Mock()
        fake.retrieve.return_value = {"retrievalResults": [], "nextToken": "more"}
        with patch("oracle.aws.aws_client", return_value=fake):
            self.assertEqual(corpus.search("synthetic alpha"), [])
        self.assertEqual(fake.retrieve.call_count, 1)
        self.assertTrue(corpus.last_retrieval["pagination_limit_reached"])

    def test_retrieve_repeated_token_fails_instead_of_looping(self):
        corpus = self.retrieval_corpus()
        self.addCleanup(corpus.close)
        fake = Mock()
        fake.retrieve.return_value = {"retrievalResults": [], "nextToken": "same-token"}
        with patch("oracle.aws.aws_client", return_value=fake), self.assertRaises(HarnessError):
            corpus.search("synthetic alpha")
        self.assertEqual(fake.retrieve.call_count, 2)

    def test_retrieve_malformed_identity_or_nontext_is_rejected(self):
        bad_snapshot = self.hit()
        bad_snapshot["metadata"]["snapshot_sha256"] = "unexpected"
        bad_doc = self.hit()
        bad_doc["metadata"]["document_id"] = "invented"
        bad_chunk = self.hit()
        bad_chunk["metadata"]["chunk_id"] = "invented"
        bad_content = self.hit()
        bad_content["content"] = {"type": "IMAGE", "byteContent": "untrusted"}
        bad_score = self.hit()
        bad_score["score"] = float("nan")
        cases = [{}, {"retrievalResults": {}}, {"retrievalResults": [bad_snapshot]},
                 {"retrievalResults": [bad_doc]}, {"retrievalResults": [bad_chunk]},
                 {"retrievalResults": [bad_content]}, {"retrievalResults": [bad_score]},
                 {"retrievalResults": [self.hit()], "guardrailAction": "INTERVENED"}]
        for index, response in enumerate(cases):
            with self.subTest(case=index):
                corpus = self.retrieval_corpus()
                try:
                    fake = Mock()
                    fake.retrieve.return_value = response
                    with patch("oracle.aws.aws_client", return_value=fake), self.assertRaises(HarnessError):
                        corpus.search("synthetic alpha")
                finally:
                    corpus.close()

    def test_managed_packet_identifies_semantic_engine(self):
        corpus = self.retrieval_corpus()
        self.addCleanup(corpus.close)
        fake = Mock()
        fake.retrieve.return_value = {"retrievalResults": [self.hit()]}
        with patch("oracle.aws.aws_client", return_value=fake):
            packet = corpus.packet("synthetic alpha", SCOPE, limit=1)
        self.assertEqual(packet["retrieval"]["engine"], "bedrock_knowledge_bases")
        self.assertTrue(packet["retrieval"]["semantic_retrieval"])
        base.validate_packet(packet, corpus)

    def test_managed_query_plan_traces_both_searches_and_preserves_original_scope(self):
        corpus = self.retrieval_corpus()
        self.addCleanup(corpus.close)
        fake = Mock()
        fake.retrieve.return_value = {'retrievalResults': [self.hit()]}
        with patch('oracle.aws.aws_client', return_value=fake):
            packet = corpus.packet('Original synthetic question', SCOPE, limit=2,
                                   search_queries=['synthetic alpha', 'synthetic addendum'])
        self.assertEqual(fake.retrieve.call_count, 2)
        self.assertEqual(packet['scope']['question'], 'Original synthetic question')
        plans = packet['retrieval']['search_plan']['query_plans']
        self.assertEqual(len(plans), 2)
        self.assertTrue(all(p['semantic_retrieval'] for p in plans))
        base.validate_packet(packet, corpus)

    def test_ingestion_requires_exact_approval_before_sdk(self):
        with patch("oracle.aws.aws_client") as client, self.assertRaises(HarnessError):
            start_ingestion_job("KB12345678", "DS12345678", "a" * 64, "us-east-1",
                                ApprovalGate(self.root / "absent.json"))
        client.assert_not_called()

    def test_ingestion_uses_stable_idempotency_token_and_reports_failure_status(self):
        resource = {"knowledge_base_id": "KB12345678", "data_source_id": "DS12345678", "snapshot_sha256": "a" * 64}
        gate = self.grant("bedrock:StartIngestionJob", resource)
        fake = Mock()
        fake.start_ingestion_job.return_value = {"ingestionJob": {"knowledgeBaseId": "KB12345678",
            "dataSourceId": "DS12345678", "ingestionJobId": "JOB1234567", "status": "FAILED",
            "startedAt": datetime(2026, 1, 1, tzinfo=timezone.utc), "failureReasons": ["synthetic test failure"]}}
        with patch("oracle.aws.aws_client", return_value=fake):
            first = start_ingestion_job("KB12345678", "DS12345678", "a" * 64, "us-east-1", gate)
            start_ingestion_job("KB12345678", "DS12345678", "a" * 64, "us-east-1", gate)
        self.assertEqual(first["status"], "FAILED")
        self.assertIsInstance(first["startedAt"], str)
        self.assertEqual(fake.start_ingestion_job.call_args_list[0].kwargs["clientToken"],
                         fake.start_ingestion_job.call_args_list[1].kwargs["clientToken"])


@skipIf(boto3 is None, "Install the optional boto3 SDK for service-model contract validation")
class AwsSdkContracts(Fixture):
    def test_sdk_qwen_native_structured_output_request_shape(self):
        runtime = self.client('bedrock-runtime')
        schema = {'type': 'object', 'additionalProperties': False, 'required': ['ok'],
                  'properties': {'ok': {'type': 'boolean'}}}
        resource = {'model_id': 'qwen.qwen3-32b-v1:0', 'max_output_tokens': 2400, 'max_prompt_chars': 110000}
        with Stubber(runtime) as stub:
            stub.add_response('converse', converse_response({'ok': True}))
            with patch('oracle.aws.aws_client', return_value=runtime):
                result = BedrockModel(resource['model_id'], 'us-east-1', self.grant(resource=resource)).complete('policy', {}, schema)
            stub.assert_no_pending_responses()
        self.assertEqual(result, {'ok': True})

    retrieval_resource = AwsMockContracts.retrieval_resource
    retrieval_corpus = AwsMockContracts.retrieval_corpus
    hit = AwsMockContracts.hit

    def setUp(self):
        super().setUp()
        blocker = patch("socket.socket.connect", side_effect=AssertionError("Network is forbidden in offline SDK tests"))
        blocker.start()
        self.addCleanup(blocker.stop)
        self.session = boto3.Session(aws_access_key_id="offline-test", aws_secret_access_key="offline-test",
                                     region_name="us-east-1")

    def client(self, service):
        return self.session.client(service, config=Config(retries={"total_max_attempts": 1}))

    def test_sdk_full_retrieve_draft_review_flow(self):
        # The mocked answer is deliberately synthetic: only transport and evidence
        # linkage are being checked, not correctness of a research interpretation.
        corpus = self.retrieval_corpus()
        self.addCleanup(corpus.close)
        packet = self.corpus.packet("synthetic alpha", SCOPE, limit=1)
        response = answer(packet)
        response['quotes'] = ['E1.Q1']
        review = {"response": response, "checks": {key: True for key in
            ("support", "applicability", "responsiveness", "dates_and_versions", "conflicts_and_exceptions", "boundary_safe")},
            "additional_query": None, "metadata_proxy_evidence_ids": [], "issues": []}
        runtime, retriever = self.client("bedrock-runtime"), self.client("bedrock-agent-runtime")
        model_resource = {"model_id": "amazon.nova-lite-v1:0", "max_output_tokens": 2400, "max_prompt_chars": 110000}
        # Keep model and retrieval grants separate, as the production approval flow requires.
        model_gate = self.grant("bedrock:Converse", model_resource)
        model_gate.path.rename(self.root / "model-grant.json")
        model_gate = ApprovalGate(self.root / "model-grant.json")
        corpus.gate = self.grant("bedrock:Retrieve", self.retrieval_resource())
        model = BedrockModel("amazon.nova-lite-v1:0", "us-east-1", model_gate)
        with Stubber(runtime) as model_stub, Stubber(retriever) as retrieval_stub:
            retrieval_stub.add_response("retrieve", {"retrievalResults": [self.hit()]}, {
                "knowledgeBaseId": "KB12345678", "retrievalQuery": {"text": "synthetic alpha"},
                "retrievalConfiguration": {"vectorSearchConfiguration": {"numberOfResults": 30,
                    "overrideSearchType": "SEMANTIC", "filter": {"equals": {
                        "key": "snapshot_sha256", "value": self.corpus.summary()["snapshot_sha256"]}}}}})
            model_stub.add_response("converse", converse_response(response))
            model_stub.add_response("converse", converse_response(review))
            with patch("oracle.aws.aws_client", side_effect=lambda service, region: {
                "bedrock-runtime": runtime, "bedrock-agent-runtime": retriever}[service]):
                result = Harness(corpus, model, limit=1).run("synthetic alpha", SCOPE)
            model_stub.assert_no_pending_responses()
            retrieval_stub.assert_no_pending_responses()
        self.assertEqual(result["harness"]["model_calls"], 2)
        self.assertEqual(result["harness"]["usage"][0]["prompt_tokens"], 120)
        self.assertEqual(result["quotes"][0]["text"], self.corpus.chunk(result["quotes"][0]["chunk_id"])["text"])
        self.assertFalse(result["verification"]["semantic_correctness_verified"])

    def test_sdk_upload_shape_and_exact_manifest_scope(self):
        directory = self.root / "export"
        manifest = export_ingestion(self.corpus, directory)
        resource = {"bucket": "synthetic-corpus", "prefix": "corpus/", "manifest_sha256": base.digest(base.canonical(manifest))}
        gate = self.grant("s3:PutObject", resource, max_calls=manifest["file_count"])
        client = self.client("s3")
        with Stubber(client) as stub:
            for item in manifest["files"]:
                stub.add_response("put_object", {"ETag": '"offline-etag"'}, {
                    "Bucket": "synthetic-corpus", "Key": "corpus/" + item["name"],
                    "Body": (directory / item["name"]).read_bytes(), "ServerSideEncryption": "AES256",
                    "ContentType": "application/json" if item["name"].endswith(".json") else "text/plain; charset=utf-8"})
            with patch("oracle.aws.aws_client", return_value=client):
                result = upload_ingestion(directory, "synthetic-corpus", "corpus", "us-east-1", gate)
            stub.assert_no_pending_responses()
        self.assertEqual(result["uploaded_files"], 4)

    def test_sdk_ingestion_shape(self):
        resource = {"knowledge_base_id": "KB12345678", "data_source_id": "DS12345678", "snapshot_sha256": "b" * 64}
        gate = self.grant("bedrock:StartIngestionJob", resource)
        client = self.client("bedrock-agent")
        with Stubber(client) as stub:
            stub.add_response("start_ingestion_job", {"ingestionJob": {
                "knowledgeBaseId": "KB12345678", "dataSourceId": "DS12345678", "ingestionJobId": "JOB1234567",
                "status": "STARTING", "startedAt": datetime(2026, 1, 1, tzinfo=timezone.utc),
                "updatedAt": datetime(2026, 1, 1, tzinfo=timezone.utc)}}, {
                "knowledgeBaseId": "KB12345678", "dataSourceId": "DS12345678",
                "clientToken": "oracle-" + base.digest(base.canonical(resource)),
                "description": "Approved oracle corpus ingestion " + "b" * 64})
            with patch("oracle.aws.aws_client", return_value=client):
                result = start_ingestion_job("KB12345678", "DS12345678", "b" * 64, "us-east-1", gate)
            stub.assert_no_pending_responses()
        self.assertEqual(result["status"], "STARTING")

    def test_sdk_error_consumes_attempt_without_exposing_vendor_body(self):
        gate = self.grant(max_calls=1)
        client = self.client("bedrock-runtime")
        with Stubber(client) as stub:
            stub.add_client_error("converse", service_error_code="ThrottlingException",
                                  service_message="synthetic request content must not be printed", http_status_code=429)
            with patch("oracle.aws.aws_client", return_value=client), self.assertRaises(HarnessError) as rejected:
                BedrockModel("synthetic-model", "us-east-1", gate).complete("policy", {}, {})
            stub.assert_no_pending_responses()
        self.assertNotIn("request content", str(rejected.exception))
        self.assertEqual(json.loads(gate.path.read_text())["used_calls"], 1)


if __name__ == "__main__":
    import unittest
    unittest.main()
