"""Offline tests for screenshot routing and initial resource scope."""

import hashlib
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agent_reader as agent
import screenshot_backend as screenshot


class ScreenshotTests(unittest.TestCase):
    def test_only_public_https_and_no_secret_parameters(self):
        self.assertEqual(screenshot.url('https://example.com/page?k=value'), 'https://example.com/page?k=value')
        for value in ('file:///temp/page.html', 'http://example.com/', 'https://127.0.0.1/a',
                      'https://10.0.0.1/', 'https://user:pass@example.com/',
                      'https://example.com:111/', 'https://example.com/#',
                      'https://example.com/?%74oken=abc', 'https://example.com/?secret='):
            with self.subTest(value=value), self.assertRaises(ValueError):
                screenshot.url(value)

    def test_resource_discovery_is_bounded_and_same_origin(self):
        possible = screenshot.Assets('https://example.com/sub/page')
        possible.feed('<script src="/one.js"></script><script src="/one.js"></script>'
                      '<link rel="stylesheet" href="../style.css"><link rel href="unused.css">'
                      '<script src="https://other.example/x.js"></script>'
                      '<img src="/banner.png"><script src="http://example.com/downgrade"></script>')
        self.assertEqual(possible.rows, {'https://example.com/one.js': 'script', 'https://example.com/style.css': 'style'})
        self.assertEqual(possible.excluded, 2)
        with self.assertRaisesRegex(ValueError, 'SCREENSHOT_ASSET_LIMIT'):
            possible.feed(''.join(f'<script src="/{index}.js"></script>' for index in range(7)))

    def test_browser_url_keys_normalize_root_port_and_path(self):
        cases = {
            'https://Example.COM:443': 'https://example.com/',
            'https://example.com/a/%2e%2e//b?': 'https://example.com//b?',
            "https://example.com/a^b?q=it's": 'https://example.com/a%5Eb?q=it%27s',
        }
        for supplied, expected in cases.items():
            with self.subTest(supplied=supplied):
                self.assertEqual(screenshot.url(supplied), expected)
                self.assertEqual(screenshot.url(expected), expected)

    def test_first_supported_document_base_resolves_relative_assets(self):
        possible = screenshot.Assets('https://example.com/sub/page')
        possible.feed('<template><base href="/ignored/"></template>'
                      '<base href="../static/archive/../"><base href="/ignored/">'
                      '<script src="js//one.js?"></script><link rel="stylesheet" href="main.css">')
        self.assertEqual(possible.rows, {
            'https://example.com/static/js//one.js?': 'script',
            'https://example.com/static/main.css': 'style',
        })

    def test_foreign_base_cannot_expand_the_origin(self):
        possible = screenshot.Assets('https://example.com/')
        possible.feed('<base href="https://assets.example/static/">'
                      '<script src="foreign.js"></script><link rel="stylesheet" href="/foreign.css">'
                      '<script src="https://example.com:443/allowed.js"></script>')
        self.assertEqual(possible.rows, {'https://example.com/allowed.js': 'script'})
        self.assertEqual(possible.excluded, 2)

    def test_csp_base_uri_leaves_relative_resources_unresolved(self):
        header = dict(name='content-security-policy', value="default-src 'self'; base-uri 'none'")
        possible = screenshot.Assets('https://example.com/', [header])
        possible.feed('<base href="/different/"><script src="relative.js"></script>'
                      '<script src="https://example.com/absolute.js"></script>')
        self.assertEqual(possible.rows, {'https://example.com/absolute.js': 'script'})
        self.assertEqual(possible.excluded, 1)

    def test_projection_preserves_each_final_csp_and_drops_cookie(self):
        raw = (b'HTTP/1.1 200 Connection established\r\n\r\n'
               b'HTTP/2 103\r\nContent-Type: text/plain\r\n\r\n'
               b'HTTP/2 200\r\nContent-Type: text/html; charset=utf-8\r\n'
               b"Content-Security-Policy: default-src 'self'\r\n"
               b"content-security-policy: script-src 'none'\r\n"
               b'Set-Cookie: synthetic=1\r\nContent-Encoding: identity\r\n\r\n')
        self.assertEqual(screenshot.response_headers(raw, 'html'), [
            dict(name='content-type', value='text/html; charset=utf-8'),
            dict(name='content-security-policy', value="default-src 'self'"),
            dict(name='content-security-policy', value="script-src 'none'"),
        ])

    def test_unsupported_response_objects_stop_before_replay(self):
        for header, code in (
            (b'Content-Disposition: Attachment; filename="page.html"', 'SCREENSHOT_ATTACHMENT'),
            (b'Content-Encoding: gzip', 'SCREENSHOT_ENCODING'),
            (b'Content-Encoding: identity, br', 'SCREENSHOT_ENCODING'),
            (b'Content-Type: text/html', 'SCREENSHOT_MIME'),
            (b' folded-value', 'SCREENSHOT_HEADERS'),
        ):
            with self.subTest(header=header), self.assertRaisesRegex(ValueError, code):
                screenshot.response_headers(b'HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n'
                                            + header + b'\r\n\r\n', 'html')

    def test_capture_uses_one_url_key_and_binds_replayed_headers(self):
        raw = b"HTTP/2 200\r\nContent-Type: text/html\r\nContent-Security-Policy: script-src 'none'\r\n\r\n"
        calls = []

        class Broker:
            def __init__(self, output):
                self.output = output
                self.calls = 0

            def handle(self, request):
                row = json.loads(request)
                calls.append(row['url'])
                self.calls += 1
                self_test.assertEqual(broker.URLS, frozenset([row['url']]))
                folder = self.output / row['id']
                folder.mkdir(parents=True)
                (folder / 'headers.txt').write_bytes(raw)
                (folder / 'body.bin').write_bytes(b'<html><body>fixture</body></html>')
                return dict(outcome='FETCHED')

        self_test = self
        broker = types.SimpleNamespace(Broker=Broker)
        source = 'https://example.com/'
        with tempfile.TemporaryDirectory() as path:
            root = Path(path)

            def render(_backend, cap, _receipt, job, _runtime):
                self.assertEqual(job['url'], source)
                self.assertEqual(list(job['maps']), [source])
                self.assertEqual(job['maps'][source]['response_headers'], screenshot.response_headers(raw, 'html'))
                self.assertEqual(json.loads((cap / 'job.json').read_text(encoding='utf-8')), job)
                with _backend.measure('vm_render'):
                    return dict(blocked_count=0, script_errors=0)

            runtime = {'work_dir': root}
            timings = {'selftests': 2.5, 'preflight': 0.25}
            app = screenshot.Backend((types.SimpleNamespace(render=render), runtime, broker), root, timings=timings)

            def receipt(target, _part):
                return dict(path=str(target), verdict='OK', content_binding='full', evidence='eventlog',
                            **app.binding.snapshot(target))

            with patch.object(app, 'declare'), patch.object(app, 'scan', side_effect=receipt), \
                 patch.object(screenshot.time, 'perf_counter', side_effect=[1, 2, 2, 4, 4, 7, 7]):
                result = app.capture('https://Example.COM:443')
                self.assertEqual(result['source']['url'], source)
                self.assertEqual(calls, [source])
            self.assertEqual(timings, dict(selftests=2.5, preflight=0.25, acquire_and_scan=1,
                                           stage_and_scan=2, vm_render=3))
            self.assertEqual(json.loads((app.evidence / 'receipt.json').read_text(encoding='utf-8'))['timings_seconds'],
                             timings)

            changed = screenshot.Backend((None, runtime, broker), root)
            with patch.object(changed, 'declare'), patch.object(changed, 'scan', side_effect=receipt):
                incoming, maps, _ = changed.acquire(source)
                header = maps[source]['header_path']
                header.write_bytes(raw.replace(b"'none'", b"'self'"))
                new_receipt = receipt(incoming, 'incoming')
                self.assertNotEqual(hashlib.sha256(header.read_bytes()).hexdigest(), maps[source]['headers_sha256'])
                with self.assertRaisesRegex(ValueError, 'SCREENSHOT_INPUT_CHANGED'):
                    changed.stage(source, incoming, maps, new_receipt)

    def test_request_has_no_files_headers_or_code(self):
        inp = dict(op='screenshot', url='https://example.com/')
        self.assertEqual(agent.request(json.dumps(inp)), inp)
        for extra in ('path', 'headers', 'javascript'):
            with self.assertRaisesRegex(ValueError, 'REQUEST_SCHEMA'):
                agent.request(json.dumps({**inp, extra: 'untrusted'}))

    def test_vm_preflight_failure_does_not_load_fetch_broker(self):
        fake_vm = types.SimpleNamespace(preflight=lambda: self.stop('SCREENSHOT_RUNTIME_NOT_CONFIGURED'))
        with patch.object(screenshot, 'load', return_value=fake_vm) as loader:
            with self.assertRaisesRegex(ValueError, 'SCREENSHOT_RUNTIME_NOT_CONFIGURED'):
                screenshot.preflight()
        self.assertEqual(loader.call_count, 1)

    def test_preflight_rejects_reusing_static_vm(self):
        uid = '00000000-0000-0000-0000-000000000001'
        vm = types.SimpleNamespace(preflight=lambda: {'vm_name': 'Separate', 'vm_uuid': uid})
        for alias in ('separate', '{' + uid + '}'):
            broker = types.SimpleNamespace(_runtime={'vm_name': alias})
            with patch.object(screenshot, 'load', side_effect=[vm, broker]):
                with self.assertRaisesRegex(ValueError, 'SCREENSHOT_VM_SCOPE'):
                    screenshot.preflight()

    def stop(self, code):
        raise ValueError(code)

    def test_validated_preflight_runs_before_budget_or_backend(self):
        with tempfile.TemporaryDirectory() as path:
            root = Path(path)
            app = agent.Adapter({}, root=root)
            with patch.object(screenshot, 'preflight', side_effect=ValueError('SCREENSHOT_MEMORY_LOW')), \
                 patch.object(screenshot, 'Backend') as backend, patch.object(agent, 'load_screenshot', return_value=screenshot):
                with self.assertRaisesRegex(ValueError, 'SCREENSHOT_MEMORY_LOW') as failure:
                    app.screenshot('https://example.com/')
                self.assertEqual(failure.exception.request_status, 'NOT_SENT')
                self.assertFalse((root / 'screenshot_budget').exists())
                backend.assert_not_called()

    def test_independent_screenshot_budget_and_fixture_boundary(self):
        with tempfile.TemporaryDirectory() as path:
            root = Path(path)
            ready = types.SimpleNamespace(capture=lambda _: dict(status='SCREENSHOT_READY'))
            with patch.object(screenshot, 'preflight', return_value=object()), \
                 patch.object(screenshot, 'Backend', return_value=ready), \
                 patch.object(agent, 'load_screenshot', return_value=screenshot), \
                 patch.object(agent, 'runtime_key', side_effect=AssertionError('no key')):
                app = agent.Adapter({}, root=root)
                for _ in range(4):
                    self.assertEqual(app.screenshot('https://example.com/')['status'], 'SCREENSHOT_READY')
                with self.assertRaisesRegex(ValueError, 'SCREENSHOT_DAILY_BUDGET_EXHAUSTED'):
                    app.screenshot('https://example.com/')
                self.assertFalse((root / 'budget').exists())
                with self.assertRaisesRegex(ValueError, 'SCREENSHOT_FIXTURE_UNSUPPORTED'):
                    agent.Adapter({}, root=root, replay=True).screenshot('https://example.com/')


if __name__ == '__main__':
    unittest.main()
