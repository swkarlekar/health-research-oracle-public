"""Read-only document lookup over the approved sources. No model or AWS calls."""
import json
import re
from urllib.parse import urlsplit

HIGHLIGHT_OPEN, HIGHLIGHT_CLOSE = '\u0001', '\u0002'
CATEGORIES = [('ecfr', 'Federal regulations'), ('uscode', 'Federal statutes'), ('nylaw', 'New York statutes'),
              ('nyreg', 'New York regulations'), ('nyagency', 'New York State guidance'), ('other', 'Other sources')]


def category(url, title=''):
    if re.search(r'\bNYCRR\b', title or ''):
        return 'nyreg'
    host = urlsplit(url or '').netloc.lower()
    if host.endswith('ecfr.gov'):
        return 'ecfr'
    if host.endswith('law.cornell.edu'):
        return 'uscode'
    if host.endswith('findlaw.com'):
        return 'nylaw'
    if host.endswith('health.ny.gov') or host.endswith('ny.gov'):
        return 'nyagency'
    return 'other'


def _site_suffix(text):
    return re.split(r'\s+[|–—-]\s+(?=Community Service Society|Legal Information|U\.S\. Code)', text)[0]


def display_title(title, extracted):
    """A readable title. Corpus titles carry line-wrap slashes ("Public / Health Law") and bare section numbers."""
    title = ' '.join((title or '').split())
    extracted = ' '.join((extracted or '').split())
    if extracted.startswith('eCFR ::'):
        return extracted[len('eCFR ::'):].strip().replace(' -- ', ' — ')
    parts = [p for p in title.split(' / ') if p]
    if parts:
        merged = parts[0]
        for part in parts[1:]:
            if part[:8] == parts[0][:8]:
                break  # a repeated citation, not a continuation
            joiner = ' ' if merged.endswith(('§', 'N.Y.', 'Public', 'Services', 'Social')) or part[0] in '–—-' \
                or part[0].islower() or part[0].isdigit() or part.startswith(('Health', 'Law', 'Security')) else ' — '
            merged += joiner + part
        title = merged
    if len(title) <= 14 and not re.search(r'[§]|U\.S\.C|C\.F\.R', title) and extracted:
        title = _site_suffix(extracted)
    return title or extracted or 'Untitled source'


def entries(connection, policy):
    """Approved documents only, sorted by category then title."""
    order = {key: n for n, (key, _) in enumerate(CATEGORIES)}
    items = []
    for identity, raw in connection.execute('SELECT document_id,metadata_json FROM documents ORDER BY document_id'):
        if not policy.get(identity, {}).get('retrieval_allowed'):
            continue
        meta = json.loads(raw)
        if meta.get('source_role') == 'collection_index':
            continue
        url = meta.get('source_url') or ''
        kind = 'pdf' if meta.get('media_type') == 'application/pdf' else 'web'
        title_text = display_title(meta.get('title'), meta.get('extracted_title'))
        items.append({'id': identity, 'title': title_text,
                      'url': url, 'category': category(url, title_text), 'kind': kind, 'host': urlsplit(url).netloc.removeprefix('www.'),
                      'characters': meta.get('char_count')})
    items.sort(key=lambda item: (order[item['category']], item['title'].lower()))
    return items


def search_text(connection, allowed, query, limit=12):
    """Keyword search inside documents. Returns one best passage per document with match markers."""
    terms = list(dict.fromkeys(re.findall(r'[a-z0-9]+', query.lower())))[:8]
    if not terms:
        return []
    expression = ' '.join('"' + term + '"' + ('*' if len(term) >= 3 else '') for term in terms)
    rows = connection.execute(
        'SELECT c.document_id,c.metadata_json,snippet(chunks_fts,3,?,?,?,22),bm25(chunks_fts,0,0.1,0.2,1) AS score '
        'FROM chunks_fts f JOIN chunks c ON c.chunk_id=f.chunk_id WHERE chunks_fts MATCH ? '
        'ORDER BY score LIMIT 200', (HIGHLIGHT_OPEN, HIGHLIGHT_CLOSE, '…', expression)).fetchall()
    found = {}
    for identity, meta, snippet, _ in rows:
        if identity in allowed and identity not in found:
            page = json.loads(meta).get('page')
            found[identity] = {'id': identity, 'page': page if type(page) is int else None, 'snippet': ' '.join(snippet.split())}
        if len(found) >= limit:
            break
    return list(found.values())
