"""Query-only boundary classification and fixed research capability limits.

Two structured calls use the selected, metered model. Substantive source research
and quotation validation remain in Harness. See docs/prompt-search.md for the
frozen experiment and post-validation wording limitations.
"""
from copy import deepcopy
import re


def retrieval_program_prefix(question, scope):
    """Use caller scope and literal coordination-program names, never model expansions."""
    parts = [scope.get('plan_type'), scope.get('program')]
    parts = [p for p in parts if p and p.lower() not in ('unknown', 'not plan specific')]
    for program in ('Medicare', 'Medicaid'):
        if re.search(r'\b' + program + r'\b', question, re.I) and not any(program.lower() in p.lower() for p in parts):
            parts.append(program)
    prefix = ' '.join(parts)
    return prefix if len(prefix) <= 79 else prefix[:79].rsplit(' ', 1)[0]

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
    classified=complete('request_classification', ROUTER_POLICY, {'question':question, 'scope': scope}, ROUTER_SCHEMA)
    base.validate_schema(classified, ROUTER_SCHEMA)
    prefix = retrieval_program_prefix(question, scope)
    public=complete('public_research_check', PUBLIC_POLICY,
                    {'question':question, 'scope': scope, 'retrieval_program_prefix': prefix}, PUBLIC_SCHEMA)
    base.validate_schema(public, PUBLIC_SCHEMA)
    decision=routing_decision(classified, public)
    planned = public.get('search_queries', [])
    focus = public.get('search_focus')
    if focus:
        # Separate the underlying service/decision from the user's proposed
        # argument. The first search cannot be replaced by an anecdote query.
        # Preserve explicit access/assessment distinctions that a short rewrite
        # can omit. These are query facets from the user's words, not legal rules.
        facets = []
        for pattern, term in ((r'\bout[ -]of[ -]network\b', 'out-of-network'),
                              (r'\breassess(?:ment|ments)\b', 'reassessment')):
            if re.search(pattern, question, re.I) and not re.search(pattern, focus['service_or_decision'], re.I):
                facets.append(term)
        service = re.sub(r'\bskilled rehab(?:ilitation)?\b', 'skilled nursing facility', focus['service_or_decision'], flags=re.I)
        first = ' '.join([prefix, *facets, service]).strip()
        planned = [first if len(first) <= 180 else first[:180].rsplit(' ', 1)[0]]
        if focus['rule_topic']:
            # Cross-cutting rules may be in a statute or regulation whose text
            # never repeats the plan/service. Scope stays fixed in every call.
            general_program = scope.get('program', '')
            if general_program.lower() == 'unknown':
                general_program = ''
            topic = focus['rule_topic']
            if re.search(r'\bcoordination of benefits\b|\bthird[ -]party liability\b', topic, re.I):
                topic = 'third party liability coordination of benefits'
            planned.append(' '.join([general_program, topic]).strip())
        # A concise question already supplies its conditions. Preserve it as the
        # first retrieval query instead of trusting a lossy model rewrite.
        if len(question) <= 180:
            planned[0] = question
    decision['search_queries'] = list(dict.fromkeys(planned)) if decision['continue_public_research'] else []
    decision['research_focus'] = focus if decision['continue_public_research'] else None
    for query in decision['search_queries']:
        check_input(query, {})
        base.reject_generated_urls(query)
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


# Query-only policies; search phrases are retrieval leads, never source evidence.
ROUTER_POLICY = """Classify requested OUTPUTS for a public-source health-insurance research system.
A question about coverage conditions, responsibilities, eligibility criteria, assessment procedures or coordination between programs is public_rules, including when expressed through a real case, ordinary language, 'does this apply', 'can a plan', or 'must a plan'. It need not contain the words general, research, sources, or rules. Requests for advocacy grounds can be researched as source-stated conditions; do not pretend to have personal advocacy experience or decide the case.
Distinguish explaining conditional rules from certifying an individual's outcome. A request to decide that a particular act was illegal or a person definitely qualifies is personal_verdict. A demand to certify coverage despite absent authority is unsupported_specific_coverage. A request for a guaranteed outcome is guarantee. Investigating or confirming allegations or identifying culpable people is unverified_allegation. Accessing private records or offering identifiers for that purpose is private_access. Calculating an exact personal deadline or continued-benefit outcome is individual_timing.
Classify every requested part using its shortest exact request phrase. Preserve public_rules in mixed requests. A case narrative or tentative user opinion is not itself a request for a verdict. Do not turn a bare verdict, guarantee, accusation, private-access request or personal-deadline calculation into public_rules merely because law is involved. 'What is her exact appeal deadline and will her benefits continue?' is individual_timing for BOTH outputs, not public_rules. 'What conditions govern notice and continued benefits?' is public_rules. Negated requests do not trigger refusal. For individual_timing, list only expressly missing program, service/decision or notice/effective-period research attributes; never ask for identifying data. Return classifications only, no law, advice or private reasoning."""
ROUTER_SCHEMA = {'type': 'object', 'additionalProperties': False, 'required': ['requests', 'missing_attributes'], 'properties': {'requests': {'type': 'array', 'maxItems': 6, 'items': {'type': 'object', 'additionalProperties': False, 'required': ['request_text', 'category'], 'properties': {'request_text': {'type': 'string', 'maxLength': 1600}, 'category': {'type': 'string', 'enum': ['public_rules', 'personal_verdict', 'guarantee', 'unverified_allegation', 'unsupported_specific_coverage', 'private_access', 'individual_timing']}}}}, 'missing_attributes': {'type': 'array', 'maxItems': 3, 'items': {'type': 'string', 'enum': ['program_or_plan_type', 'service_or_decision_type', 'notice_or_effective_timing']}}}}
PUBLIC_POLICY = """Independently check whether the user asks a source-researchable question about public insurance rules, conditions, criteria, responsibilities or procedures. Judge the meaning, not magic words: 'when must a plan cover a service?', 'does an exception apply after enrollment stops?', and 'can an assessment occur at a facility?' request rules even without saying general, sources or research. A case narrative, plan name, tentative belief, or request for possible advocacy grounds does not remove that research intent. Explain conditional source rules without deciding an individual's result or claiming personal experience.
Return explicit_public_research=true for that requested research output and quote its shortest exact request phrase. Return false for only a personal legality/eligibility verdict, guaranteed result, allegation/blame, unsupported coverage certification, private-record access or exact personal deadline. 'What is her exact appeal deadline and will her existing benefits continue?' requests two individual outcomes, NOT public research; return false even though general rules could be offered instead. 'What conditions govern continued benefits during appeals?' requests public rules. Do not invent a research request for the prohibited outputs. Preserve research alongside a distinct prohibited request; respect negation.
For an allowed request provide search_focus: service_or_decision (the underlying service and coverage/decision issue, 4–10 words), and rule_topic (the particular criteria or procedural issue, 4–10 words). For a request for advocacy arguments, service_or_decision MUST describe the coverage/service question in the narrative rather than the proposed argument or outcome. Retain the concrete service phrase and its clinical qualifiers from the narrative (for example skilled, emergency, outpatient); do not replace the service with an administrative agreement or appeal. Expand clear service acronyms. Remove insurer brand names, anecdotal success, opinions and proposed legal conclusions. Do not invent citations, policy numbers or another program. Example: a story seeking successful arguments about a denied specialist -> service_or_decision 'out of network specialist coverage', rule_topic 'provider network capacity coverage requirements'. A named exception/status must remain in service_or_decision, rather than generic eligibility. rule_topic should state the cross-cutting legal concept, without repeating the service name, plan name or brands already searched in service_or_decision. For multiple-payer questions, separate the service's covered-benefit issue from the payer-liability issue: use the neutral topic third party liability and coordination of benefits for rule_topic, without the equipment/service name. These terms do not assert a payment rule. The caller supplies retrieval_program_prefix from validated scope; never reinterpret that plan as another program and do not return a program field. These terms are search leads, never evidence. Leave search_queries empty when search_focus is provided. If disallowed, omit search_focus and return empty search_queries. Return JSON only, no law or private reasoning."""
PUBLIC_SCHEMA = {'type': 'object', 'additionalProperties': False, 'required': ['explicit_public_research', 'request_text'], 'properties': {'search_focus': {'type': 'object', 'additionalProperties': False, 'required': ['service_or_decision', 'rule_topic'], 'properties': {'service_or_decision': {'type': 'string', 'minLength': 3, 'maxLength': 100}, 'rule_topic': {'type': 'string', 'maxLength': 100}}}, 'search_queries': {'type': 'array', 'maxItems': 2, 'uniqueItems': True, 'items': {'type': 'string', 'minLength': 3, 'maxLength': 180}}, 'explicit_public_research': {'type': 'boolean'}, 'request_text': {'type': 'string', 'maxLength': 1600}}}
