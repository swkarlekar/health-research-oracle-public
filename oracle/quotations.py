"""Let models select source passages by ID; Python supplies the exact offsets."""
import copy
import re

from .corpus import HarnessError

SELECTOR_POLICY = """
QUOTATION OUTPUT ADAPTER FOR THIS CALL
The supplied generation schema replaces the legacy chunk_id/start/end quote
format: quotes[] must contain only passage quote_id strings, for example E1.Q2.
Use the passages array as evidence text. Python will insert the exact canonical
quotation, offsets, and citations after selection. Never count characters.
Include a supporting selection for every evidence ID cited in a finding. Use
only the passages needed for the answer, preserving conditions and exceptions.
Aim for three to six concise passages; use more only to retain necessary source
conditions. Remove ancillary findings rather than citing unquoted evidence.
"""


def passages(item):
    """Lossless partition at nearby line/sentence boundaries, never a paraphrase."""
    text, start, result = item['text'], 0, []
    while start < len(text):
        end = min(start + 1400, len(text))
        if end < len(text):
            # Prefer a complete paragraph with its heading, rather than a fixed
            # window that cuts off the condition at the end of a legal sentence.
            paragraph = re.search(r'\n[ \t]*\n', text[start + 120:end])
            if paragraph:
                end = start + 120 + paragraph.end()
            else:
                end = min(start + 900, len(text))
                sentence = re.search(r'(?<=[.;])\s+', text[start + 300:end])
                boundaries = list(re.finditer(r'\s+', text[start + 350:end]))
                if sentence:
                    end = start + 300 + sentence.end()
                elif boundaries:
                    end = start + 350 + boundaries[-1].end()
        result.append({'quote_id': f"{item['evidence_id']}.Q{len(result) + 1}",
                       'start': start, 'end': end, 'text': text[start:end]})
        start = end
    return result


def covered_hits(evidence):
    """Raw hits fully present in retained parent context need not be repeated."""
    covered = {}
    for item in evidence:
        if item.get('context_kind'):
            continue
        for parent in evidence:
            if not parent.get('context_kind') or item['document_id'] != parent['document_id']:
                continue
            left = item['document_start_char'] - parent['document_start_char']
            if left >= 0 and parent['text'][left:left + len(item['text'])] == item['text']:
                covered[item['evidence_id']] = parent['evidence_id']
                break
    return covered


def quote_options(packet, visible_only=False):
    covered = covered_hits(packet['evidence']) if visible_only else {}
    return {p['quote_id']: {'chunk_id': item['chunk_id'], 'start': p['start'], 'end': p['end']}
            for item in packet['evidence'] if item['evidence_id'] not in covered
            for p in passages(item) if p['text'].strip()}


def generation_payload(payload):
    """Represent every source character once, alongside selectable passage IDs."""
    value = copy.deepcopy(payload)
    covered = covered_hits(value['evidence'])
    value['covered_retrieval_hits'] = covered
    value['evidence'] = [e for e in value['evidence'] if e['evidence_id'] not in covered]
    for item in value['evidence']:
        item['passages'] = [{'quote_id': p['quote_id'], 'text': p['text']} for p in passages(item)]
        del item['text']
        item.pop('quote_candidates', None)
    value['quotation_policy'] = ('Select quotes using passage quote_id strings, such as E1.Q2. '
        'Passages in order contain the entire unchanged source excerpt. Python resolves IDs to canonical offsets. '
        'covered_retrieval_hits maps raw hits to retained parent context containing their exact text; cite the retained context. '
        'Select the passage(s) that support each finding and retain its conditions and exceptions. '
        'Do not count characters or generate quotation text. Do not ask the user legal research questions you should resolve from sources, '
        'or ask again for facts already supplied in the question.')
    return value


def resolve_quotes(packet, response):
    """Resolve selectors before the existing independent source-span validator.

    Canonical span responses remain supported for existing offline integrations;
    live providers validate the stricter selector schema before returning here.
    """
    response = copy.deepcopy(response)
    options = quote_options(packet)
    resolved = []
    for selection in response['quotes']:
        if isinstance(selection, str):
            if selection not in options:
                raise HarnessError('Model selected an unknown canonical quotation ID')
            resolved.append(options[selection])
        else:
            resolved.append(selection)
    response['quotes'] = resolved
    return response
