"""Read-only citation inspection against the pinned corpus and original files."""
import hashlib
import json
from pathlib import Path

from .corpus import HarnessError, ROOT, base


class CitationViewer:
    def __init__(self, corpus, raw_root=None):
        self.corpus = corpus
        self.raw_root = (raw_root or ROOT / 'text_corpus/raw').resolve()
        self._snapshot = None

    def freeze(self, result, index):
        """Cache verified, immutable source data without sharing SQLite connections."""
        viewer = CitationViewer(None, self.raw_root)
        viewer._snapshot = self.resolve(result, index)
        viewer._quote_key = base.digest(base.canonical(result['quotes'][index]))
        return viewer

    def resolve(self, result, index):
        if type(index) is not int or not 0 <= index < len(result.get('quotes', [])):
            raise HarnessError('Quotation not found')
        quote = result['quotes'][index]
        if self._snapshot is not None:
            if base.digest(base.canonical(quote)) != self._quote_key:
                raise HarnessError('Saved citation changed after source verification')
            return self._snapshot
        item = self.corpus.chunk(quote['chunk_id'])
        if quote['text'] != item['text'][quote['start']:quote['end']]:
            raise HarnessError('Saved quotation differs from canonical source text')
        doc = self.corpus._document(item['document_id'])
        start = item['document_start_char'] + quote['start']
        end = item['document_start_char'] + quote['end']
        sections = [s for s in self.corpus.graph.sections_for_document(doc['document_id'])
                    if s['document_start_char'] < end and s['document_end_char'] > start]
        return quote, doc, start, end, sections

    def raw_file(self, doc):
        raw = doc.get('raw_path')
        if not raw:
            raise HarnessError('No saved original is available for this source')
        # Corpus paths are relative to text_corpus, never user-controlled paths.
        path = (self.raw_root.parent / raw).resolve()
        if not path.is_relative_to(self.raw_root) or not path.is_file():
            raise HarnessError('Saved original is outside the corpus or unavailable')
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != doc.get('raw_sha256'):
            raise HarnessError('Saved original differs from the pinned source hash')
        return path, data

    def describe(self, result, index, page=None):
        quote, doc, start, end, sections = self.resolve(result, index)
        pages = sorted({s['page'] for s in sections if s.get('page') is not None})
        view = {'document_id': doc['document_id'], 'title': doc.get('title'),
                'source_url': doc.get('source_url'), 'quote': quote['text'],
                'document_start_char': start, 'document_end_char': end,
                'citation_pages': pages, 'kind': 'text', 'original_available': False,
                'text': doc['text'], 'boxes': [], 'highlight_status': 'canonical_text',
                'page_count': None, 'page': None}
        try:
            path, data = self.raw_file(doc)
        except HarnessError as exc:
            view['original_note'] = str(exc)
            return view
        view['original_available'] = True
        if data[:5] != b'%PDF-':
            return view  # Never execute saved HTML or load its external assets.
        import fitz
        with fitz.open(stream=data, filetype='pdf') as pdf:
            selected = page if page is not None else (pages[0] if pages else 1)
            if type(selected) is not int or not 1 <= selected <= len(pdf):
                raise HarnessError('PDF page is outside the saved document')
            source_page = pdf[selected - 1]
            if source_page.rect.width > 2000 or source_page.rect.height > 3000:
                raise HarnessError('Page dimensions exceed the local viewer limit')
            fragments = []
            for section in sections:
                if section.get('page') == selected:
                    left, right = max(start, section['document_start_char']), min(end, section['document_end_char'])
                    fragments.append(doc['text'][left:right])
            boxes, methods = match_boxes(source_page, fragments)
            view.update({'kind': 'pdf', 'page': selected, 'page_count': len(pdf),
                         'width': source_page.rect.width, 'height': source_page.rect.height,
                         'boxes': boxes, 'highlight_status': ', '.join(sorted(methods)) if boxes else 'no_reliable_match'})
            view['page_layouts'] = [{'page': n + 1, 'width': p.rect.width, 'height': p.rect.height}
                                    for n, p in enumerate(pdf)]
            view['page_highlights'] = {}
            for number in pages:
                pieces = [doc['text'][max(start, s['document_start_char']):min(end, s['document_end_char'])]
                          for s in sections if s.get('page') == number]
                rects, matches = match_boxes(pdf[number - 1], pieces)
                view['page_highlights'][str(number)] = {'boxes': rects,
                    'status': ', '.join(sorted(matches)) if rects else 'no_reliable_match'}
            view.pop('text')
        return view

    def image(self, result, index, page):
        _, doc, _, _, _ = self.resolve(result, index)
        _, data = self.raw_file(doc)
        import fitz
        with fitz.open(stream=data, filetype='pdf') as pdf:
            if type(page) is not int or not 1 <= page <= len(pdf):
                raise HarnessError('PDF page is outside the saved document')
            source_page = pdf[page - 1]
            if source_page.rect.width > 2000 or source_page.rect.height > 3000:
                raise HarnessError('Page dimensions exceed the local viewer limit')
            return source_page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False).tobytes('png')


def match_boxes(page, fragments):
    """Highlight lexical matches only; never guess a location from an LLM."""
    rectangles, methods = {}, set()
    for fragment in dict.fromkeys(fragments):
        normalized = ' '.join(fragment.split())
        if not normalized:
            continue
        matches = page.search_for(normalized)
        if matches:
            methods.add('exact_passage_match')
        else:
            # Table extraction/layout can differ. Report partial line matching
            # explicitly and avoid short ambiguous headings or clause letters.
            matches = []
            for line in fragment.splitlines():
                line = ' '.join(line.split())
                if len(line) >= 30 and len(line.split()) >= 5:
                    matches.extend(page.search_for(line))
            if matches:
                methods.add('partial_line_matches')
        for rect in matches:
            if getattr(page, 'rotation', 0):
                rect = rect * page.rotation_matrix
            key = tuple(round(v, 3) for v in rect)
            rectangles[key] = list(key)
            if len(rectangles) >= 400:
                return list(rectangles.values()), methods | {'highlight_limit_reached'}
    return list(rectangles.values()), methods
