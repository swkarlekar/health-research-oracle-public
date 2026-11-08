"""Query-only boundary classification and fixed research capability limits.

Two structured calls use the selected, metered model. Substantive source research
and quotation validation remain in Harness. See docs/prompt-search.md for the
frozen experiment and post-validation wording limitations.
"""
from copy import deepcopy

def routing_decision(route, public_check):
    categories={item['category'] for item in route['requests']}
    public=bool(public_check['explicit_public_research'] and 'public_rules' in categories)
    messages=[]
    if 'private_access' in categories:
        messages.append('This system cannot access private Medicaid case or enrollment records or establish the private reason benefits changed. Do not provide names, identifiers, birth dates or addresses.')
    if 'personal_verdict' in categories:
        messages.append('These public sources cannot establish an individual legality or eligibility verdict or certify a specific coverage obligation. Missing authority remains an evidence gap.')
    if 'guarantee' in categories:
        messages.append('This system cannot guarantee a hearing outcome, restored services or a particular coverage result.')
    if 'unverified_allegation' in categories:
        messages.append('Any allegation remains unverified by these public sources. This system cannot establish fraud or identify or accuse responsible employees.')
    if 'unsupported_specific_coverage' in categories:
        messages.append('This system cannot certify individual coverage or invent missing authority; public-rule research requires supporting authorized sources.')
    if 'individual_timing' in categories:
        messages.append('An exact individual deadline or continued-service outcome is not established here; this system researches public requirements and does not calculate a personal filing deadline.')
    question_map={
        'program_or_plan_type':"Which Medicaid program or plan category should the research cover? 'Unknown' is acceptable.",
        'service_or_decision_type':'Which service category and decision type should the public-rule research cover?',
        'notice_or_effective_timing':'Which non-identifying governing period or notice/effective timing should the research cover? Do not provide personal dates.'}
    missing=route['missing_attributes'] if 'individual_timing' in categories else []
    return {'continue_public_research':public,'status_if_no_public_research':'clarification_needed' if missing else 'not_found',
            'boundary_limits':messages,'clarifying_questions':[question_map[key] for key in missing],
            'capability_offer':'Public-rule research remains available using non-identifying program and decision attributes.'}


def attach_to_research_response(response, decision):
    """Only after existing Harness has accepted a public-research response.

    Re-run response schema/canonical/boundary validation after this addition. This
    function cannot authorize an otherwise rejected answer or repair legal errors.
    """
    result=deepcopy(response)
    if decision['boundary_limits']:
        result['uncertainties'].append({'kind':'research_boundary','description':' '.join(decision['boundary_limits']),
                                       'evidence_ids':[],'question_for_user':None})
        if result['status']=='supported':result['status']='partial'
    result['clarifying_questions']=list(dict.fromkeys(result['clarifying_questions']+decision['clarifying_questions']))
    return result


def route_request(complete, question, scope):
    """complete(role, system, payload, schema) must meter and trace each call."""
    from oracle.corpus import base
    from oracle.policy import check_input
    check_input(question, scope)
    classified=complete('request_classification', ROUTER_POLICY, {'question':question}, ROUTER_SCHEMA)
    base.validate_schema(classified, ROUTER_SCHEMA)
    public=complete('public_research_check', PUBLIC_POLICY, {'question':question}, PUBLIC_SCHEMA)
    base.validate_schema(public, PUBLIC_SCHEMA)
    decision=routing_decision(classified, public)
    decision['model_calls']=2
    decision['annotations']={'requested_output_categories':[x['category'] for x in classified['requests']],
                             'explicit_public_research':public['explicit_public_research']}
    return decision


def bounded_response(question, scope, decision):
    """Call only when continue_public_research is False; no corpus law is generated."""
    from oracle.policy import check_response_boundary
    if decision['continue_public_research']:
        raise ValueError('Mixed or ordinary public research must continue through the existing harness')
    response={'status':decision['status_if_no_public_research'],
              'scope':{**scope,'question':question,'assumptions':[]},'findings':[],'quotes':[],
              'uncertainties':[{'kind':'research_boundary','description':' '.join(decision['boundary_limits'])+' '+decision['capability_offer'],
                                'evidence_ids':[],'question_for_user':None}],
              'clarifying_questions':decision['clarifying_questions']}
    check_response_boundary(response)
    return response


# Exact frozen inference prompts; no post-holdout model-prompt tuning.
ROUTER_POLICY = "Route requested OUTPUTS, not the narrative topic. Identify the shortest exact user-request phrase for each output, and assign one category. Do not create a public-rule request merely because a case involves law or insurance. Public-rule research asks what source rules, conditions or procedures establish in general; it does not ask you to certify a case outcome. A personal narrative with an explicit general research question remains allowed. An individual legality/eligibility verdict, guaranteed result, allegation confirmation or blame, coverage certification despite missing sources, private-record access, and a precise personal deadline are distinct outputs. Classify every requested part; preserve explicit public research in mixed requests. A negated or expressly disclaimed request is not requested: 'do not guarantee', 'not asking for a personal deadline' and 'treat the allegation as unverified' do not trigger refusal. For a fraud/blame request choose unverified_allegation, not private_access unless the user separately asks to access records. An offer to submit identifiers for case access is private_access. If an exact individual deadline or service-continuation result depends on expressly missing program, service or notice timing, include the missing research attributes. Never ask for identifying data. Return classifications only, no law, advice or private reasoning."
ROUTER_SCHEMA = {'type': 'object', 'additionalProperties': False, 'required': ['requests', 'missing_attributes'], 'properties': {'requests': {'type': 'array', 'maxItems': 6, 'items': {'type': 'object', 'additionalProperties': False, 'required': ['request_text', 'category'], 'properties': {'request_text': {'type': 'string', 'maxLength': 600}, 'category': {'type': 'string', 'enum': ['public_rules', 'personal_verdict', 'guarantee', 'unverified_allegation', 'unsupported_specific_coverage', 'private_access', 'individual_timing']}}}}, 'missing_attributes': {'type': 'array', 'maxItems': 3, 'items': {'type': 'string', 'enum': ['program_or_plan_type', 'service_or_decision_type', 'notice_or_effective_timing']}}}}
PUBLIC_POLICY = "Determine whether the question EXPLICITLY asks for general public-source rules, criteria, conditions or procedures as a requested output. A statement of circumstances is not such a request. Merely asking whether an act was illegal, asking for a guarantee, confirming fraud, demanding unsupported coverage, accessing personal records, or asking an exact individual deadline is not a general research request. But an explicit research request remains valid alongside those requests. Negation matters: 'research the general rules, not an individual verdict' is general research. Return the shortest exact request phrase supporting yes; otherwise no and an empty phrase. No law or private reasoning."
PUBLIC_SCHEMA = {'type': 'object', 'additionalProperties': False, 'required': ['explicit_public_research', 'request_text'], 'properties': {'explicit_public_research': {'type': 'boolean'}, 'request_text': {'type': 'string', 'maxLength': 600}}}
