"""Offline regression for one approved URL per curl invocation."""

import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / 'poc/review_probes/web_research_boundary_20261108/public_fetch_broker.py'


class TransportTests(unittest.TestCase):
    def test_expansion_is_disabled_for_direct_and_proxy_transports(self):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            curl = home / 'curl-fixture'
            curl.write_bytes(b'fixture bytes')
            source = home / SOURCE.name
            source.write_bytes(SOURCE.read_bytes())
            config = dict(curl_path=str(curl), curl_sha256=hashlib.sha256(curl.read_bytes()).hexdigest(),
                          proxy_url='')
            (home / 'runtime_config.py').write_text(f'def load_config():\n    return {config!r}\n',
                                                    encoding='utf-8')
            spec = importlib.util.spec_from_file_location('broker_for_transport_test', source)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            approved_url = 'https://example.com/'
            completed = subprocess.CompletedProcess([], 0, stdout=b'200', stderr=b'')

            for name, proxy in (('direct', ''), ('proxy', 'http://127.0.0.1:8080')):
                with self.subTest(name=name), patch.object(module, 'PROXY', proxy), \
                     patch.object(module.subprocess, 'run', return_value=completed) as execute:
                    resolver = Mock(return_value=['93.184.216.34'])
                    broker = module.Broker(home / name, resolver=resolver)
                    result = broker.handle(json.dumps({'id': 'approved', 'url': approved_url}))
                    self.assertEqual(result['outcome'], 'FETCHED')
                    self.assertEqual(broker.calls, 1)
                    resolver.assert_called_once_with('example.com')
                    execute.assert_called_once()
                    argv = execute.call_args.args[0]
                    self.assertEqual(argv[1], '-q')
                    self.assertEqual(argv.count('--globoff'), 1)
                    self.assertLess(argv.index('--globoff'), argv.index('--url'))
                    self.assertEqual(argv.count('--url'), 1)
                    self.assertEqual(argv[-2:], ['--url', approved_url])
                    self.assertEqual(argv[argv.index('--proxy') + 1], proxy)
                    self.assertIs(execute.call_args.kwargs['shell'], False)


if __name__ == '__main__':
    unittest.main()
