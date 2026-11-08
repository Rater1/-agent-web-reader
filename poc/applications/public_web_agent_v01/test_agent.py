"""Synthetic adapter tests; real keys, network, scanner and VM are never used."""
import concurrent.futures
import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
import agent_reader as a


class Tests(unittest.TestCase):
    def setUp(self):
        (a.REPO/'.local').mkdir(exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(dir=a.REPO/'.local',prefix='agent-reader-test-')
        self.root=Path(self.temp.name)
        self.runtime_patch=patch.object(a.Adapter,'runtime_preflight',return_value={})
        self.runtime_patch.start()
        self.frozen=json.loads((HERE/'frozen.json').read_text(encoding='utf-8'))
    def tearDown(self):
        self.runtime_patch.stop()
        self.temp.cleanup()
    def adapter(self):
        return a.Adapter(self.frozen,replay=True,root=self.root)
    def test_schema(self):
        for raw in ['{"op":"search","query":"x","headers":{}}','{"op":"search","op":"read"}',
                    '{"op":"read","search_id":"../../x","rank":1}',
                    json.dumps(dict(op='read',search_id='a'*32,rank=True)),
                    json.dumps(dict(op='search',query='tvly-SYNTHETIC')),
                    json.dumps(dict(op='search',query='C:\\private\\file')), 'x'*4097]:
            with self.assertRaises(ValueError):a.request(raw)
        self.assertEqual(a.request('{"op":"search","query":"public query"}')['query'],'public query')
    def test_actual_replay_search_read(self):
        tool=self.adapter(); search=tool.search(a.QUERY_REPLAY)
        self.assertEqual(search['status'],'SEARCH_OK')
        self.assertEqual(search['mode'],'fixture')
        page=tool.read(search['search_id'],1)
        self.assertEqual(page['status'],'READ_OK')
        self.assertIn('answer = 2 ** 200',page['text'])
        self.assertEqual(page['source']['mode'],'synthetic')
    def test_fake_session_and_out_of_range(self):
        tool=self.adapter()
        with self.assertRaisesRegex(a.Stopped,'SEARCH_ID_NOT_FOUND'):tool.read('a'*32,1)
        result=tool.search(a.QUERY_REPLAY)
        with self.assertRaisesRegex(a.Stopped,'RANK_SCOPE'):tool.read(result['search_id'],5)
        self.assertFalse((self.root/result['search_id']/'read_reserved.json').exists())
    def test_expiry(self):
        tool=self.adapter(); result=tool.search(a.QUERY_REPLAY)
        tool.clock=lambda: 10**12
        with self.assertRaisesRegex(a.Stopped,'SESSION_EXPIRED_OR_CHANGED'):tool.read(result['search_id'],1)
    def test_state_tamper(self):
        tool=self.adapter();result=tool.search(a.QUERY_REPLAY);file=self.root/result['search_id']/'state.json'
        state=json.loads(file.read_text());state['result']['results'][0]['url']='https://attacker.example/'
        file.write_text(json.dumps(state))
        with self.assertRaisesRegex(a.Stopped,'SEARCH_RESULT_CHANGED'):tool.read(result['search_id'],1)
    def test_duplicate_and_concurrent_read(self):
        tool=self.adapter();result=tool.search(a.QUERY_REPLAY)
        def call():
            try:return tool.read(result['search_id'],1)['status']
            except a.Stopped as e:return str(e)
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:out=list(pool.map(lambda _:call(),range(2)))
        self.assertEqual(sorted(out),['READ_ALREADY_RESERVED_NO_RETRY','READ_OK'])
    def test_failed_read_consumes_slot(self):
        tool=self.adapter();result=tool.search(a.QUERY_REPLAY)
        with patch.object(tool,'replay_result',side_effect=TimeoutError('SYNTHETIC-SECRET')) as transport:
            with self.assertRaises(TimeoutError):tool.read(result['search_id'],1)
            with self.assertRaisesRegex(a.Stopped,'READ_ALREADY_RESERVED_NO_RETRY'):tool.read(result['search_id'],1)
            self.assertEqual(transport.call_count,1)
    def test_budget_atomic(self):
        tool=self.adapter()
        def reserve():
            try:return tool.reserve_search()
            except a.Stopped:return None
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:out=list(pool.map(lambda _:reserve(),range(a.MAX_DAILY_SEARCHES+4)))
        self.assertEqual(len([x for x in out if x is not None]),a.MAX_DAILY_SEARCHES)
        self.assertEqual(len(list((self.root/'budget').iterdir())),a.MAX_DAILY_SEARCHES)
    def test_budget_failure_no_backend(self):
        tool=a.Adapter(self.frozen,root=self.root,backend_factory=lambda: self.fail('must not create backend'))
        with patch.object(a,'runtime_key',return_value='tvly-SYNTHETIC'),patch.object(tool,'reserve_search',side_effect=a.Stopped('LOCAL_DAILY_BUDGET_EXHAUSTED')):
            with self.assertRaisesRegex(a.Stopped,'LOCAL_DAILY_BUDGET_EXHAUSTED'):tool.search('public query')
    def test_no_key_no_backend_or_budget(self):
        tool=a.Adapter(self.frozen,root=self.root,backend_factory=lambda: self.fail('must not create backend'))
        with patch.object(a,'runtime_key',side_effect=a.Stopped('AUTH_NOT_CONFIGURED')),patch.object(tool,'reserve_search') as reserve:
            with self.assertRaisesRegex(a.Stopped,'AUTH_NOT_CONFIGURED'):tool.search('public query')
            reserve.assert_not_called()
    def test_guard_failures_no_protected(self):
        calls=[];bad=copy.deepcopy(self.frozen);name=next(iter(bad['sources']));bad['sources'][name]='0'*64
        with self.assertRaisesRegex(a.Stopped,'SOURCE_CHANGED'):a.guard(bad,lambda:0,lambda:calls.append('network'))
        with self.assertRaisesRegex(a.Stopped,'SELFTEST_FAILED'):a.guard(self.frozen,lambda:1,lambda:calls.append('network'))
        self.assertEqual(calls,[])
    def test_registry_key_mock_only(self):
        with patch.object(a.os,'environ',{'TAVILY_API_KEY':'tvly-SYNTHETIC'}):
            self.assertEqual(a.runtime_key({}),'tvly-SYNTHETIC')
    def test_replay_state_cannot_become_live(self):
        tool=self.adapter();result=tool.search(a.QUERY_REPLAY);file=self.root/result['search_id']/'state.json'
        state=json.loads(file.read_text());state['replay']=False;file.write_text(json.dumps(state))
        tool.replay=False
        with patch.object(tool,'backend') as backend:
            with self.assertRaisesRegex(a.Stopped,'SESSION_RECEIPT_SCOPE'):tool.read(result['search_id'],1)
            backend.assert_not_called()
        self.assertFalse((file.parent/'read_reserved.json').exists())

    def test_missing_runtime_before_key_budget_backend(self):
        tool=a.Adapter(self.frozen,root=self.root)
        with patch.object(tool,'runtime_preflight',side_effect=ValueError('RUNTIME_NOT_CONFIGURED')), \
             patch.object(a,'runtime_key') as key, patch.object(tool,'reserve_search') as budget, patch.object(tool,'backend') as backend:
            with self.assertRaisesRegex(ValueError,'RUNTIME_NOT_CONFIGURED'):tool.search('public question')
            key.assert_not_called(); budget.assert_not_called(); backend.assert_not_called()

    def test_live_synthetic_backend_and_receipt(self):
        # Entirely synthetic live-shaped control; never a real PIPELINE_COMPLETE claim.
        tool=a.Adapter(self.frozen,root=self.root/'state')
        run='tvp-20000101-000000-00000000'
        result=copy.deepcopy(self.adapter().replay_result('search')[0])
        result['source']['mode']='public'
        receipt=dict(phase='PIPELINE_COMPLETE',mode='live',kind='search',question=a.QUERY_REPLAY,
                     source_url='https://api.tavily.com/search',parsed=dict(mode='live',results=[result]))
        original=self.root/'.local/tavily_pipeline_v01'/run/'run_receipt.json'
        original.parent.mkdir(parents=True);original.write_text(json.dumps(receipt))
        import types
        record=types.SimpleNamespace(record={'run_id':run})
        backend=types.SimpleNamespace(search=lambda _:result,runs=[record])
        page=copy.deepcopy(self.adapter().replay_result('html')[0]);page['source']['mode']='public'
        backend.read=lambda *args:page
        with patch.object(a,'REPO',self.root),patch.object(a,'runtime_key',return_value='tvly-SYNTHETIC'),patch.object(tool,'backend',return_value=backend):
            found=tool.search(a.QUERY_REPLAY)
            self.assertEqual(found['mode'],'live')
            self.assertEqual(tool.read(found['search_id'],1)['status'],'READ_OK')
            with self.assertRaisesRegex(a.Stopped,'READ_ALREADY_RESERVED_NO_RETRY'):tool.read(found['search_id'],1)

    def test_public_urls(self):
        for url in ['https://127.0.0.1/','https://10.0.0.1/','https://user:pass@example.com/',
                    'file:///private','http://example.com/','https://example.com:22/']:
            with self.assertRaises(ValueError):self.adapter().reader.public_url(url)

    def test_runtime_config_missing_and_duplicate(self):
        module=self.adapter().reader.load('config_synthetic_test',self.adapter().reader.BASE/'runtime_config.py')
        with patch.object(module,'ROOT',self.root):
            with self.assertRaisesRegex(ValueError,'RUNTIME_NOT_CONFIGURED'):module.load_config()
            (self.root/'runtime.local.json').write_text('{"work_dir":"x","work_dir":"y"}')
            with patch.object(module.sys,'platform','win32'):
                with self.assertRaisesRegex(ValueError,'RUNTIME_CONFIG_INVALID'):module.load_config()

    def test_query_normalization_stays_fail_closed(self):
        reader=self.adapter().reader
        backend=reader.Backend.__new__(reader.Backend)
        backend.search_used=False
        with patch.object(backend,'run',return_value={'query':'changed public question'}):
            with self.assertRaisesRegex(ValueError,'SEARCH_QUERY_CHANGED'):backend.search('public question')

    def test_runtime_material_source_closure(self):
        reader=self.adapter().reader
        cls=reader.build_pipeline_class(self.frozen)
        self.assertTrue(issubclass(cls,object))


if __name__=='__main__':unittest.main()
