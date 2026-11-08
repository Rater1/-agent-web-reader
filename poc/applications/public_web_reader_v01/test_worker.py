"""Guest synthetic tests with the actual generic parser. No outbound transport."""
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import reader_worker as worker


class Tests(unittest.TestCase):
    def test_search_and_private_url(self):
        rows = [dict(title='公开', url='https://example.com/', content='public '*100),
                dict(title='Private', url='https://127.0.0.1/', content='no')]
        result = worker.parse('search', json.dumps(dict(query='公开问题', results=rows, usage=dict(credits=1))).encode())
        self.assertEqual(len(result['results']), 1)
        self.assertEqual(result['omitted_unsafe_urls'], 1)
        self.assertEqual(result['credits_reported'], 1)
        self.assertTrue(result['results'][0]['excerpt_truncated'])

    def test_duplicate_and_large_result(self):
        for raw in [b'{"query":"a","query":"b","results":[]}',
                    json.dumps(dict(query='x', results=[{}]*6)).encode()]:
            with self.assertRaises(ValueError):
                worker.parse('search', raw)

    def test_html_no_scripts_or_comments(self):
        result = worker.parse('html', b'<title>A</title><main><p>A public page with enough text.</p><!--secret--><script>bad()</script><iframe src="https://evil.com/"></iframe></main>')
        self.assertNotIn('secret', result['text'])
        self.assertNotIn('bad()', result['text'])
        self.assertEqual(result['resources_fetched'], 0)

    def test_code_and_scope(self):
        result = worker.parse('html', b'<nav>NO_NAV</nav><article><p>Long enough public introduction.</p><pre><span>if</span> ok:\n    answer = 2 ** 200\n\n    print(answer)</pre></article><p>OUTSIDE</p>')
        self.assertIn('if ok:\n    answer = 2 ** 200\n\n    print(answer)', result['text'])
        self.assertNotIn('OUTSIDE', result['text'])
        self.assertNotIn('NO_NAV', result['text'])

    def test_excerpt_and_empty(self):
        result = worker.parse('html', ('<main>'+('中'*6000)+'</main>').encode())
        self.assertEqual(len(result['text']), 4000)
        self.assertTrue(result['truncated'])
        self.assertLess(len(json.dumps(result, ensure_ascii=True).encode()), 32768)
        with self.assertRaises(ValueError):
            worker.parse('html', b'<main>short</main>')

    def test_unknown_kind(self):
        with self.assertRaises(ValueError):
            worker.parse('pdf', b'xx')


if __name__ == '__main__':
    unittest.main()
