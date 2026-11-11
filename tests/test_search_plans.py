"""Bounded retrieval plans are query leads, not source facts or scope changes."""
from unittest.mock import patch
from support import Fixture, SCOPE, FixtureGraph
from oracle.corpus import HarnessError, base
from oracle.context import ExpandedCorpus
from oracle.request_routing import route_request, PUBLIC_SCHEMA

class SearchPlanTests(Fixture):
    def test_query_plan_preserves_original_scope_and_canonical_evidence(self):
        original = 'Original synthetic question with narrative'
        packet = self.corpus.packet(original, SCOPE, 2,
                                    search_queries=['Synthetic alpha', 'Synthetic addendum'])
        self.assertEqual(packet['scope']['question'], original)
        self.assertEqual(packet['retrieval']['search_plan']['queries'], ['Synthetic alpha', 'Synthetic addendum'])
        self.assertLessEqual(len(packet['evidence']), 2)
        base.validate_packet(packet, self.corpus)

    def test_routing_plan_is_never_used_for_disallowed_request(self):
        values = iter([{'requests':[{'category':'private_access','request_text':'open records'}], 'missing_attributes':[]},
                       {'explicit_public_research':False,'request_text':'','search_queries':['synthetic alpha']}])
        result = route_request(lambda *args: next(values), 'Open private records', SCOPE)
        self.assertFalse(result['continue_public_research'])
        self.assertEqual(result['search_queries'], [])

    def test_generated_query_urls_rejected_before_retrieval(self):
        with patch.object(self.corpus, 'search') as search, self.assertRaises(HarnessError):
            self.corpus.search_planned('synthetic', ['https://example.org/collect'], 2)
        search.assert_not_called()

    def test_plan_size_and_query_length_are_bounded(self):
        for queries in ([], ['one', 'two', 'three'], ['x' * 181]):
            with self.subTest(queries=queries), self.assertRaises(HarnessError):
                self.corpus.search_planned('synthetic', queries, 2)
        for queries in (['x' * 181], ['one', 'two', 'three']):
            with self.assertRaises(HarnessError):
                base.validate_schema({'explicit_public_research':True,'request_text':'question','search_queries':queries}, PUBLIC_SCHEMA)

    def test_second_search_respects_source_budget_without_truncating_or_reusing_ids(self):
        expanded = ExpandedCorpus(self.corpus, FixtureGraph(self.corpus),
                                  short_document_chars=60, max_unit_chars=60, max_total_chars=100)
        packet = expanded.packet('Synthetic alpha', SCOPE, 1)
        before = {e['evidence_id']: e['text'] for e in packet['evidence']}
        expanded.expand(packet, 'Synthetic addendum', 2)
        expanded.validate_context(packet)
        self.assertLessEqual(sum(len(e['text']) for e in packet['evidence']), 100)
        self.assertTrue(packet['retrieval']['budget_omissions'])
        self.assertEqual(len({e['evidence_id'] for e in packet['evidence']}), len(packet['evidence']))
        for evidence in packet['evidence']:
            if evidence['evidence_id'] in before:
                self.assertEqual(evidence['text'], before[evidence['evidence_id']])

    def test_parent_expansion_preserves_sparse_evidence_identifiers(self):
        expanded = ExpandedCorpus(self.corpus, FixtureGraph(self.corpus))
        packet = self.corpus.packet('Synthetic alpha', SCOPE, 2)
        packet['evidence'][1]['evidence_id'] = 'E4'
        packet['packet_id'] = base.packet_hash(packet)
        expanded._expand_context(packet)
        expanded.validate_context(packet)
        ids = [e['evidence_id'] for e in packet['evidence']]
        self.assertEqual(ids[:2], ['E1', 'E4'])
        self.assertEqual(len(ids), len(set(ids)))

    def test_planner_cannot_reinterpret_scoped_plan_and_keeps_literal_coordination_program(self):
        from oracle.request_routing import retrieval_program_prefix
        scoped = {'program': 'New York Medicaid', 'plan_type': 'MLTC', 'research_date': 'unknown'}
        prefix = retrieval_program_prefix('Does my MLTC plan cover this service?', scoped)
        self.assertIn('MLTC', prefix)
        self.assertNotIn('Medicare', prefix)
        prefix = retrieval_program_prefix('How does MLTC coordinate with Medicare?', scoped)
        self.assertIn('Medicare', prefix)
        self.assertNotIn('Advantage', prefix)
        calls=[]
        outputs=iter([{'requests':[{'category':'public_rules','request_text':'coverage'}],'missing_attributes':[]},
                      {'explicit_public_research':True,'request_text':'coverage','search_focus':{
                          'service_or_decision':'equipment coverage','rule_topic':'payment coordination'},'search_queries':[]}])
        def complete(role,system,payload,schema):
            calls.append(payload);return next(outputs)
        decision=route_request(complete,'How does MLTC coordinate with Medicare?',scoped)
        self.assertEqual(decision['search_queries'][0], 'How does MLTC coordinate with Medicare?')
        self.assertEqual(decision['search_queries'][1], 'New York Medicaid payment coordination')
        self.assertTrue(all('Advantage' not in q for q in decision['search_queries']))
        self.assertTrue(all(p['scope']==scoped for p in calls))

    def test_long_query_rewrite_preserves_explicit_access_and_assessment_facets(self):
        outputs=iter([{'requests':[{'category':'public_rules','request_text':'rules'}],'missing_attributes':[]},
                      {'explicit_public_research':True,'request_text':'rules','search_focus':{
                          'service_or_decision':'facility placement agreement','rule_topic':'provider network adequacy'}}])
        question='A long non-identifying narrative about out-of-network placement and a reassessment. ' + 'Background. '*15
        decision=route_request(lambda *args:next(outputs),question,SCOPE)
        self.assertIn('out-of-network',decision['search_queries'][0])
        self.assertIn('reassessment',decision['search_queries'][0])
        self.assertLessEqual(len(decision['search_queries'][0]),180)

    def test_controlled_synonyms_change_only_search_leads_not_scope_or_question(self):
        outputs=iter([{'requests':[{'category':'public_rules','request_text':'rules'}],'missing_attributes':[]},
                      {'explicit_public_research':True,'request_text':'rules','search_focus':{
                          'service_or_decision':'skilled rehabilitation coverage','rule_topic':'coordination of benefits for a service'}}])
        question='A non-identifying research narrative. '*6
        decision=route_request(lambda *args:next(outputs),question,SCOPE)
        self.assertIn('skilled nursing facility',decision['search_queries'][0])
        self.assertTrue(decision['search_queries'][1].endswith('third party liability coordination of benefits'))
        self.assertEqual(decision['research_focus']['service_or_decision'],'skilled rehabilitation coverage')

    def test_spelled_out_plan_scope_is_recognized_without_an_individual_verdict(self):
        from oracle.policy import inferred_scope
        self.assertEqual(inferred_scope('What public rules govern managed long-term care coverage?')['plan_type'],'MLTC')
        self.assertIsNone(inferred_scope('What public rules govern a care program?')['plan_type'])
