"""Protect the boundary between a linked page and other hydrated page objects."""
import importlib.util
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("processor_embedded_test", ROOT / "scripts/process_corpus.py")
processor = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = processor
spec.loader.exec_module(processor)


def fixture(auth=False):
    main = {":itemsOrder": ["b", "a", "sidebar"], ":items": {
        "a": {":type": "maximus-base/components/content/text", "text": "<p>Second paragraph.</p>"},
        "b": {":type": "maximus-base/components/content/text", "text": '<h2>First heading</h2><p><a href="/listed.pdf">Plan list</a></p>'},
        "sidebar": {":type": "sidebar", "text": "DO NOT INCLUDE SIDEBAR"}}}
    state = {"aemPages": {"program-materials": {"title": "Program Materials", "authenticationRequired": auth,
        ":items": {"root": {":items": {"main-par": main}}}},
        "unrelated-page": {"text": "DO NOT INCLUDE OTHER PAGE"}}}
    return ('<html><head><title>Loading</title></head><body><script>window.INITIAL_STATE = '
            + json.dumps(state) + ';</script></body></html>').encode()


class EmbeddedPageScopeTests(unittest.TestCase):
    def test_only_requested_page_main_text_in_declared_order(self):
        result = processor.extract_html(fixture(), source_url="https://www.nymedicaidchoice.com/program-materials")
        text = "\n".join(section.text for section in result.sections)
        self.assertIn("First heading", text)
        self.assertIn("Second paragraph.", text)
        self.assertLess(text.index("First heading"), text.index("Second paragraph."))
        self.assertNotIn("DO NOT INCLUDE", text)
        self.assertEqual(result.metadata["observed_child_links"][0]["href_exact"], "/listed.pdf")
        self.assertTrue(all("/aemPages/program-materials/" in section.source_locator for section in result.sections))

    def test_embedded_route_does_not_accept_another_source_or_auth_required_page(self):
        result = processor.extract_html(fixture(), source_url="https://example.com/program-materials")
        self.assertNotEqual(result.method, "embedded_public_page_json_html_text")
        with self.assertRaises(ValueError):
            processor.extract_html(fixture(auth=True), source_url="https://www.nymedicaidchoice.com/program-materials")


if __name__ == "__main__":
    unittest.main()
