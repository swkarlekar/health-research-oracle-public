"""Conservative model-contract provenance filter; not a legal applicability decision."""
import re


PRODUCT_PATTERNS = {
    'MLTC': r'\bMLTC\b|\bmanaged long[ -]term care\b',
    'MAP': r'\bMAP\b|\bMedicaid Advantage Plus\b',
    'PACE': r'\bPACE\b',
    'HARP': r'\bHARP\b|\bHealth and Recovery Plan\b',
    'MMC': r'\bMMC\b|\bmainstream (?:Medicaid )?managed care\b',
    'HIV SNP': r'\bHIV[ /-]*SNP\b'}


def contract_products(text):
    return {key for key, pattern in PRODUCT_PATTERNS.items() if re.search(pattern, text or '', re.I)}


def contract_scope_exclusions(payload):
    """Narrow provenance guard, not a determination of legal applicability."""
    scope = payload['scope']
    requested = contract_products(scope.get('plan_type', ''))
    if not requested:
        return {}
    allowed = requested | contract_products(scope['question'])
    metadata = {d['document_id']: d['source_metadata'] for d in payload.get('document_signals', [])}
    exclusions = {}
    for item in payload['evidence']:
        title = metadata.get(item['document_id'], {}).get('title', '')
        products = contract_products(title)
        body = ''.join(p['text'] for p in item['passages'])
        if (re.search(r'\bmodel contract\b', title, re.I) and products and not products & allowed
                and not contract_products(body) & requested):
            exclusions[item['evidence_id']] = ('This model contract is explicitly labeled for ' + ', '.join(sorted(products))
                + ', outside the requested ' + ', '.join(sorted(requested))
                + ' contract-product scope; this passage contains no explicit reference to the requested product. '
                'It cannot supply a transferred rule. This is a conservative source-selection limit.')
    return exclusions

