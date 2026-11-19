"""Offline tests for screenshot routing and initial resource scope."""

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
