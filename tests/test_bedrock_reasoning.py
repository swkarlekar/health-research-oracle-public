"""Reasoning mode preserves authorization, final JSON gates, and private-text exclusion."""
import json
from unittest.mock import Mock
from support import Fixture
from test_aws_contracts import converse_response
from oracle.aws import BedrockModel
from oracle.corpus import HarnessError

class BedrockReasoningTests(Fixture):
    def model(self, model_id='qwen.qwen3-32b-v1:0', *, reasoning=True, response=None):
        fake=Mock();fake.converse.return_value=response or converse_response({'ok':True})
        value=BedrockModel(model_id,'us-east-1',None,max_output_tokens=8192,
                           reasoning=reasoning,client_factory=lambda *args:fake)
        value.gate=self.grant(resource=value.approval_resource())
        return value,fake

    def test_qwen_thinking_switch_keeps_native_schema_and_discards_private_text(self):
        response=converse_response({'ok':True})
        response['output']['message']['content'].insert(0,{'reasoningContent':{'reasoningText':{'text':'PRIVATE_SENTINEL'}}})
        model,fake=self.model(response=response)
        self.assertEqual(model.complete('Policy',{}, {'type':'object'}),{'ok':True})
        request=fake.converse.call_args.kwargs
        self.assertTrue(request['messages'][0]['content'][0]['text'].endswith('/think'))
        self.assertIn('outputConfig',request)
        self.assertEqual(request['additionalModelRequestFields'],{'reasoning_effort':'high'})
        self.assertTrue(model.usage[-1]['reasoning_observed'])
        self.assertNotIn('PRIVATE_SENTINEL',json.dumps(model.usage))

    def test_haiku_thinking_budget_and_sampling_are_provider_specific(self):
        model,fake=self.model('global.anthropic.claude-haiku-4-5-20251001-v1:0')
        model.complete('Policy',{}, {'type':'object','properties':{'clarifying_questions':{'type':'array','items':{'type':'string'}}}})
        request=fake.converse.call_args.kwargs
        self.assertEqual(request['additionalModelRequestFields']['thinking'],{'type':'enabled','budget_tokens':2048})
        self.assertNotIn('temperature',request['inferenceConfig'])
        self.assertIn('outputConfig',request)
        self.assertEqual(model.approval_resource()['thinking_budget_tokens'],2048)

    def test_non_reasoning_grant_cannot_enable_reasoning(self):
        model,fake=self.model()
        resource=model.approval_resource();resource.pop('reasoning_enabled')
        model.gate=self.grant(resource=resource)
        with self.assertRaises(HarnessError):model.complete('Policy',{}, {'type':'object'})
        fake.converse.assert_not_called()

    def test_inline_thinking_is_discarded_but_json_values_are_preserved(self):
        response=converse_response({'literal':'<think>source text</think>'})
        response['output']['message']['content'][0]['text']='<think>PRIVATE_SENTINEL</think>\n'+response['output']['message']['content'][0]['text']
        model,_=self.model(response=response)
        self.assertEqual(model.complete('Policy',{}, {'type':'object'}),{'literal':'<think>source text</think>'})
        self.assertNotIn('PRIVATE_SENTINEL',json.dumps(model.usage))

    def test_unfinished_reasoning_and_output_limit_fail_closed(self):
        for response in (dict(converse_response({'ok':True}),stopReason='max_tokens'),
                         dict(converse_response({'ok':True}),output={'message':{'content':[{'text':'<think>PRIVATE_SENTINEL'}]}})):
            model,_=self.model(response=response)
            with self.assertRaises(HarnessError):model.complete('Policy',{}, {'type':'object'})

    def test_grok_high_effort_and_encrypted_reasoning_are_not_exposed(self):
        response=converse_response({'ok':True})
        response['output']['message']['content'].insert(0,{'reasoningContent':{'redactedContent':b'PRIVATE_ENCRYPTED'}})
        model,fake=self.model('global.xai.grok-4.7',response=response)
        self.assertEqual(model.complete('Policy',{}, {'type':'object'}),{'ok':True})
        self.assertEqual(fake.converse.call_args.kwargs['additionalModelRequestFields'],{'reasoning':{'effort':'high'}})
        self.assertTrue(model.usage[-1]['reasoning_observed'])
        self.assertNotIn('PRIVATE_ENCRYPTED',json.dumps(model.usage))

    def test_llama_keeps_plain_json_contract_without_native_decoder_or_reasoning_switch(self):
        model,fake=self.model('us.meta.llama3-3-70b-instruct-v1:0',reasoning=False)
        model.complete('Policy',{}, {'type':'object','properties':{'clarifying_questions':{'type':'array','items':{'type':'string'}}}})
        self.assertNotIn('additionalModelRequestFields',fake.converse.call_args.kwargs)
        self.assertNotIn('outputConfig',fake.converse.call_args.kwargs)
        self.assertIn('clarifying_questions must always be an array',fake.converse.call_args.kwargs['system'][0]['text'])

    def test_answer_format_reminder_does_not_add_fields_to_routing_contract(self):
        model,fake=self.model('us.meta.llama4-maverick-17b-instruct-v1:0',reasoning=False)
        model.complete('Policy',{}, {'type':'object'})
        self.assertNotIn('clarifying_questions must',fake.converse.call_args.kwargs['system'][0]['text'])

    def test_gpt_oss_uses_its_bedrock_reasoning_field_and_native_json(self):
        model,fake=self.model('openai.gpt-oss-120b-1:0')
        model.complete('Policy',{}, {'type':'object'})
        request=fake.converse.call_args.kwargs
        self.assertEqual(request['additionalModelRequestFields'],{'reasoning_effort':'high'})
        self.assertIn('outputConfig',request)
        self.assertEqual(model.approval_resource()['reasoning_effort'],'high')

    def test_aws_reasoning_tag_is_discarded_before_final_json_validation(self):
        response=converse_response({'ok':True})
        response['output']['message']['content'][0]['text']='<reasoning>PRIVATE_SENTINEL</reasoning>'+response['output']['message']['content'][0]['text']
        model,_=self.model('openai.gpt-oss-120b-1:0',response=response)
        self.assertEqual(model.complete('Policy',{}, {'type':'object'}),{'ok':True})
        self.assertNotIn('PRIVATE_SENTINEL',json.dumps(model.usage))
