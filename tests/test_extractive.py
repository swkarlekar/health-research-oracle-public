"""Extractive source selection retains the canonical, scope and independent-review gates."""
import copy
from support import Fixture, SCOPE, RoutedModel
from oracle.corpus import HarnessError, base
from oracle.harness import Harness, constrained_schema
from oracle.output import DebugTrace
from oracle.quotations import generation_payload

class ExtractiveSourceTests(Fixture):
    def payload(self):
        packet = self.corpus.packet('Synthetic alpha', SCOPE, 2)
        return packet, generation_payload(self.corpus.model_payload(packet))

    def test_no_responsive_source_still_receives_independent_review_without_findings(self):
        class EmptyDraftModel(RoutedModel):
            def complete(model, system, payload, schema):
                if 'checks' in schema.get('properties', {}):
                    model.calls += 1
                    return {'response': copy.deepcopy(payload['draft']),
                            'checks': {key: True for key in schema['properties']['checks']['properties']},
                            'additional_query': None, 'issues': [], 'metadata_proxy_evidence_ids': []}
                if 'status' in schema.get('properties', {}):
                    from oracle.harness import empty_response
                    model.calls += 1
                    return {'status': 'not_found', 'scope': payload['scope'], 'findings': [], 'quotes': [],
                            'uncertainties': [{'kind':'gap','description':'The requested exception is not established.',
                                              'evidence_ids':[], 'question_for_user':None}], 'clarifying_questions':[]}
                return super(EmptyDraftModel, model).complete(system, payload, schema)
        model, trace = EmptyDraftModel(), DebugTrace()
        result = Harness(self.corpus, model, request_guard=True, trace=trace).run('Synthetic alpha', SCOPE)
        self.assertEqual(result['findings'], [])
        self.assertEqual(result['quotes'], [])
        self.assertEqual(result['status'], 'not_found')
        self.assertEqual(result['harness']['model_calls'], 4)
        roles = [e['role'] for e in trace.events if e['stage'] == 'model_started']
        self.assertEqual(roles, ['request_classification', 'public_research_check', 'draft', 'review'])

    def test_empty_allowed_source_native_schema_has_no_contradictory_cardinality(self):
        from oracle.aws import _native_schema
        packet, _ = self.payload()
        schema = _native_schema(constrained_schema(packet, []))
        self.assertEqual(schema['properties']['findings']['maxItems'], 0)
        ids = schema['properties']['findings']['items']['properties']['evidence_ids']
        self.assertEqual(ids['maxItems'], 0)
        self.assertEqual(ids['minItems'], 0)

    def test_duplicate_reference_normalization_does_not_rewrite_unknown_ids_or_claims(self):
        from oracle.aws import deduplicate_reference_sets
        value = {'findings': [{'text': 'Keep the entire conditional claim.', 'evidence_ids': ['E1', 'E1', 'E999']}],
                 'quotes': ['E1.Q2', 'E1.Q2', 'E999.Q3'], 'scope': {'question': 'Unchanged'}}
        changes = deduplicate_reference_sets(value)
        self.assertEqual(len(changes), 2)
        self.assertEqual(value['findings'][0]['evidence_ids'], ['E1', 'E999'])
        self.assertEqual(value['quotes'], ['E1.Q2', 'E999.Q3'])
        self.assertEqual(value['findings'][0]['text'], 'Keep the entire conditional claim.')
        packet, _ = self.payload()
        with self.assertRaises(HarnessError):
            base.validate_schema(value['findings'][0], constrained_schema(packet)['properties']['findings']['items'])

    def test_contract_product_guard_respects_joint_unknown_mixed_and_explicit_bridge_scope(self):
        from oracle.source_scope import contract_scope_exclusions
        payload={'scope': {'program':'New York Medicaid','plan_type':'MLTC','question':'MLTC reassessment rules'},
                 'document_signals':[{'document_id':'d','source_metadata':{'title':'Mainstream MMC / HIV SNP / HARP model contract'}}],
                 'evidence':[{'evidence_id':'E1','document_id':'d','passages':[{'text':'The Contractor must assess its members.'}]}]}
        self.assertIn('E1',contract_scope_exclusions(payload))
        payload['scope']['plan_type']='HARP'
        self.assertEqual(contract_scope_exclusions(payload),{})
        payload['scope']['plan_type']='unknown'
        self.assertEqual(contract_scope_exclusions(payload),{})
        payload['scope']['plan_type']='MLTC';payload['scope']['question']='Compare MLTC and HARP assessment rules'
        self.assertEqual(contract_scope_exclusions(payload),{})
        payload['scope']['question']='MLTC assessment rules'
        payload['evidence'][0]['passages'][0]['text']='A reference to MLTC is present; applicability still requires review.'
        self.assertEqual(contract_scope_exclusions(payload),{})
        payload['document_signals'][0]['source_metadata']['title']='General regulation'
        payload['evidence'][0]['passages'][0]['text']='A generally worded rule.'
        self.assertEqual(contract_scope_exclusions(payload),{})

    def test_proxy_schema_allows_only_actual_available_ids(self):
        from oracle.harness import review_schema
        packet,_=self.payload()
        schema=review_schema(constrained_schema(packet), ['E1'])['properties']['metadata_proxy_evidence_ids']
        base.validate_schema(['E1'],schema)
        with self.assertRaises(HarnessError):base.validate_schema(['E2'],schema)
        with self.assertRaises(HarnessError):base.validate_schema(['E1'],review_schema(constrained_schema(packet), [])['properties']['metadata_proxy_evidence_ids'])

    def test_extractive_quotes_are_substantive_and_cannot_bypass_review_or_status_gates(self):
        from oracle.quotations import resolve_quotes
        packet,payload=self.payload()
        response={'status':'supported','scope':packet['scope'],'findings':[], 'quotes':[payload['evidence'][0]['passages'][0]['quote_id']],
                  'uncertainties':[],'clarifying_questions':[]}
        response=resolve_quotes(packet,response)
        result=base.render_response(packet,response,self.corpus,answer_style='source_quotations')
        self.assertEqual(result['answer_style'],'source_quotations')
        self.assertEqual(result['quotes'][0]['text'],self.corpus.chunk(result['quotes'][0]['chunk_id'])['text'])
        with self.assertRaises(HarnessError):base.render_response(packet,response,self.corpus)
        response['status']='not_found'
        with self.assertRaises(HarnessError):base.render_response(packet,response,self.corpus,answer_style='source_quotations')
        response['status']='supported';response['scope']=copy.deepcopy(response['scope']);response['scope']['assumptions']=['An invented legal conclusion.']
        with self.assertRaises(HarnessError):base.render_response(packet,response,self.corpus,answer_style='source_quotations')

    def test_selected_raw_ids_map_to_exact_expanded_parent_and_ids_remain_monotonic(self):
        from oracle.context import ExpandedCorpus
        from support import FixtureGraph
        expanded=ExpandedCorpus(self.corpus,FixtureGraph(self.corpus),short_document_chars=60,max_unit_chars=100,max_total_chars=200)
        packet=expanded.packet('Synthetic alpha',SCOPE,2)
        previous=generation_payload(expanded.model_payload(packet))
        chosen=previous['evidence'][0]['evidence_id']
        highest=max(int(e['evidence_id'][1:]) for e in packet['evidence'])
        before={e['evidence_id']:e['chunk_id'] for e in packet['evidence']}
        raw_before = {e['chunk_id'] for e in packet['evidence'] if not e.get('context_kind')}
        expanded.focus(packet,[chosen])
        current=generation_payload(expanded.model_payload(packet))
        mapped = current.get('covered_retrieval_hits', {}).get(chosen, chosen)
        self.assertIn(mapped, current['allowed_evidence_ids'])
        self.assertTrue(raw_before <= {e['chunk_id'] for e in packet['evidence']})
        self.assertGreaterEqual(packet['retrieval']['max_assigned_evidence_id'],highest)
        for e in packet['evidence']:
            if e['evidence_id'] in before:self.assertEqual(e['chunk_id'],before[e['evidence_id']])
        expanded.validate_context(packet)
        self.assertLessEqual(sum(len(e['text']) for e in packet['evidence']),expanded.max_total_chars)

    def test_empty_extractive_status_normalization_retains_gap_and_records_original(self):
        trace=DebugTrace();h=Harness(self.corpus,RoutedModel(),request_guard=True,trace=trace)
        response={'status':'partial','scope':SCOPE,'findings':[],'quotes':[],
                  'uncertainties':[{'kind':'gap','description':'The location requirement is not established by the supplied excerpts.',
                                    'evidence_ids':[],'question_for_user':None}], 'clarifying_questions':[]}
        original=copy.deepcopy(response['uncertainties'])
        result=h._empty_extractive_status(response)
        self.assertEqual(result['status'],'not_found')
        self.assertEqual(result['uncertainties'],original)
        self.assertEqual(trace.events[0]['original_status'],'partial')

    def test_extractive_clarifications_are_limited_to_unknown_scope_attributes(self):
        packet,_=self.payload()
        schema=constrained_schema(packet, extractive=True)
        questions=schema['properties']['clarifying_questions']
        with self.assertRaises(HarnessError):base.validate_schema(['Please define the missing legal status.'],questions)
        from oracle.policy import SCOPE_QUESTIONS
        packet['scope']['research_date']='unknown'
        schema=constrained_schema(packet, extractive=True)
        base.validate_schema([SCOPE_QUESTIONS['research_date']],schema['properties']['clarifying_questions'])
        with self.assertRaises(HarnessError):base.validate_schema('Provide the policy definition.',schema['properties']['uncertainties']['items']['properties']['question_for_user'])

    def test_long_tabular_paragraph_retains_header_and_row_in_one_quote(self):
        from oracle.quotations import passages
        text='Covered services                 Excluded services\n' + ('Service A                 Service B\n' * 60) + '\n\nFootnote with a condition.'
        options=passages({'evidence_id':'E1','text':text})
        self.assertEqual(''.join(x['text'] for x in options),text)
        self.assertIn('Covered services',options[0]['text'])
        self.assertEqual(options[0]['text'].count('Service A'),60)

    def test_all_approved_scope_questions_pass_the_privacy_boundary(self):
        from oracle.policy import SCOPE_QUESTIONS, check_response_boundary
        check_response_boundary({'scope':{**SCOPE,'assumptions':[]},'findings':[], 'uncertainties':[],
                                 'clarifying_questions':list(SCOPE_QUESTIONS.values())})
