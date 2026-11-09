"""Synthetic boundary tests: no DNS, transport, key, scanner or VM calls."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
import agent_reader as a
import batch_backend as b
import batch_guest as g

def page(url,index=0):
    return dict(id='page'+str(index),outcome='TEXT',source=dict(url=url),input_sha256=str(index)*64,text='Public static text with enough detail.',scope='article',truncated=False,title='Public')

class Tests(unittest.TestCase):
    def setUp(self):
        (a.REPO/'.local').mkdir(exist_ok=True)
        self.runtime_patch=patch.object(a.Adapter,'runtime_preflight',return_value={});self.runtime_patch.start()
        self.temp=tempfile.TemporaryDirectory(dir=a.REPO/'.local',prefix='batch-test-')
        self.root=Path(self.temp.name)
    def tearDown(self):self.runtime_patch.stop();self.temp.cleanup()
    def session(self):
        root=self.root/'state';root.mkdir()
        run='tvp-20000101-000000-deadbeef';d=self.root/'.local/tavily_pipeline_v01'/run;d.mkdir(parents=True)
        urls=['https://example.com/one','https://example.org/two','https://example.net/three']
        result=dict(query='public batch query',outcome='SEARCH_RESULTS',results=[dict(url=u,title='Public',snippet='Public snippet') for u in urls],source=dict(mode='public'))
        receipt=dict(phase='PIPELINE_COMPLETE',mode='live',kind='search',question=result['query'],source_url='https://api.tavily.com/search',parsed=dict(mode='live',results=[result]))
        file=d/'run_receipt.json';file.write_text(json.dumps(receipt))
        sid='a'*32;folder=root/sid;folder.mkdir()
        state=dict(search_id=sid,created_at=100,replay=False,frozen_sha256=a.sha(HERE/'frozen.json'),source_run=run,receipt_sha256=a.sha(file),result=result,urls=urls,query=result['query'])
        (folder/'state.json').write_text(json.dumps(state))
        return root,sid,urls,folder
    def test_batch_schema(self):
        base=dict(op='read_many',search_id='a'*32,ranks=[2,1])
        self.assertEqual(a.request(json.dumps(base)),base)
        for ranks in [[],[1,1],[True],[0],[6],list(range(1,7)),'1']:
            with self.assertRaises(ValueError):a.request(json.dumps({**base,'ranks':ranks}))
        with self.assertRaises(ValueError):a.request(json.dumps({**base,'url':'https://attacker.example/'}))
    def test_two_pages_one_backend_in_order_and_no_retry(self):
        root,sid,urls,folder=self.session();calls=[]
        class Fake:
            runs=[types.SimpleNamespace(record=dict(run_id='tvp-20000101-000001-deadbeef'))]
            def read_many(self,q,selected):calls.append((q,selected));return [page(u,i) for i,u in enumerate(selected)]
        tool=a.Adapter({},root=root,batch_backend_factory=Fake,clock=lambda:101)
        with patch.object(a,'REPO',self.root):
            result=tool.read_many(sid,[2,1])
            with self.assertRaisesRegex(a.Stopped,'READ_ALREADY_RESERVED_NO_RETRY'):tool.read_many(sid,[3])
        self.assertEqual(len(calls),1);self.assertEqual(calls[0][1],[urls[1],urls[0]])
        self.assertEqual([r['rank'] for r in result['pages']],[2,1]);self.assertEqual(result['page_count'],2)
    def test_batch_failure_consumes_whole_reservation(self):
        root,sid,urls,folder=self.session()
        backend=types.SimpleNamespace(read_many=lambda *x: (_ for _ in ()).throw(TimeoutError('synthetic')))
        tool=a.Adapter({},root=root,batch_backend_factory=lambda:backend,clock=lambda:101)
        with patch.object(a,'REPO',self.root):
            with self.assertRaises(TimeoutError):tool.read_many(sid,[1,2])
            with self.assertRaisesRegex(a.Stopped,'READ_ALREADY_RESERVED_NO_RETRY'):tool.read_many(sid,[1,2])
        self.assertTrue((folder/'read_reserved.json').exists())
    def test_wrong_second_source_is_not_success(self):
        root,sid,urls,folder=self.session()
        fake=types.SimpleNamespace(read_many=lambda q,u:[page(u[0]),page('https://attacker.example/',1)],runs=[types.SimpleNamespace(record={'run_id':'tvp-20000101-000001-deadbeef'})])
        with patch.object(a,'REPO',self.root):
            with self.assertRaisesRegex(a.Stopped,'PAGE_BINDING'):a.Adapter({},root=root,batch_backend_factory=lambda:fake,clock=lambda:101).read_many(sid,[1,2])
    def test_wrong_second_hash_or_id_is_not_success(self):
        urls=['https://example.com/one','https://example.org/two'];rows=[page(u,i) for i,u in enumerate(urls)]
        self.assertEqual(b.validate_results(rows,urls,['0'*64,'1'*64]),rows)
        bad=copy.deepcopy(rows);bad[1]['input_sha256']='0'*64
        with self.assertRaisesRegex(ValueError,'BATCH_BODY_BINDING'):b.validate_results(bad,urls,['0'*64,'1'*64])
        bad=copy.deepcopy(rows);bad[1]['id']='page0'
        with self.assertRaisesRegex(ValueError,'BATCH_RESULT_ID'):b.validate_results(bad,urls)
    def test_session_tamper_before_backend(self):
        root,sid,urls,folder=self.session();p=folder/'state.json';s=json.loads(p.read_text());s['result']['results'][1]['url']='https://attacker.example/';p.write_text(json.dumps(s))
        with patch.object(a,'REPO',self.root):
            with self.assertRaisesRegex(a.Stopped,'SEARCH_RESULT_CHANGED'):a.Adapter({},root=root,batch_backend_factory=lambda:self.fail('must not run'),clock=lambda:101).read_many(sid,[1,2])
        self.assertFalse((folder/'read_reserved.json').exists())
    def test_fetch_second_failure_stops_before_third_scan_or_vm(self):
        incoming=self.root/'incoming';incoming.mkdir();spec=importlib.util.spec_from_file_location('contract',a.READER/'response_contract.py');contract=importlib.util.module_from_spec(spec);spec.loader.exec_module(contract)
        pipe=types.SimpleNamespace(contract=contract,record={});calls=[]
        class Broker:
            calls=0
            def handle(self,line):
                self.calls+=1;calls.append(json.loads(line)['url'])
                if self.calls==2:return dict(outcome='TRANSPORT_TIMEOUT')
                p=incoming/'raw/page0';p.mkdir(parents=True);body=b'<main>Public static content with enough detail.</main>'
                (p/'body.bin').write_bytes(body);(p/'headers.txt').write_bytes(f'HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nContent-Length: {len(body)}\r\n\r\n'.encode())
                return dict(outcome='FETCHED',validated_addresses=['93.184.216.34'],pinned_ipv4='93.184.216.34')
        protected=[]
        with self.assertRaisesRegex(ValueError,'BATCH_PAGE_ACQUISITION'):
            b.acquire_pages(pipe,incoming,Broker(),['https://example.com/one','https://example.org/two','https://example.net/three']);protected.append('scan_or_vm')
        self.assertEqual(len(calls),2);self.assertEqual(protected,[]);self.assertEqual(pipe.record['page_attempts'],2)
    def test_lock_and_release_on_failure(self):
        with a.operation_lock(self.root):
            with self.assertRaisesRegex(a.Stopped,'TOOL_BUSY_OR_STALE_LOCK'):
                with a.operation_lock(self.root):self.fail('must not enter')
        with self.assertRaises(RuntimeError):
            with a.operation_lock(self.root):raise RuntimeError('synthetic')
        self.assertFalse((self.root/'vm_operation.lock').exists())
    def test_frozen_gate_or_selftest_failure_no_protected(self):
        f=json.loads((HERE/'frozen.json').read_text());calls=[];bad=copy.deepcopy(f);bad['download_gate_sha256']='0'*64
        with self.assertRaisesRegex(a.Stopped,'GATE_CHANGED'):a.guard(bad,lambda:calls.append('tests'),lambda:calls.append('protected'))
        with self.assertRaisesRegex(a.Stopped,'SELFTEST_FAILED'):a.guard(f,lambda:1,lambda:calls.append('protected'))
        self.assertEqual(calls,[])
    def jobs(self,count):
        (self.root/'jobs.json').write_text(json.dumps(dict(mode='demo',jobs=[dict(id='page'+str(i),spec=f'page{i}.json',expected=['TEXT']) for i in range(count)])))
    def test_workers_separate_clean_env_and_stop_on_parse_failure(self):
        self.jobs(3);calls=[]
        def child(args,**kw):
            calls.append((args,kw));return subprocess.CompletedProcess(args,0,json.dumps(dict(outcome='TEXT' if len(calls)==1 else 'REJECTED')).encode())
        with self.assertRaisesRegex(ValueError,'JOB_OUTCOME_MISMATCH'):g.execute(self.root,runner=child)
        self.assertEqual(len(calls),2)
        self.assertEqual(calls[0][1]['env'],{'PATH':'/usr/bin:/bin','LANG':'C.UTF-8'});self.assertEqual(calls[0][1]['timeout'],5)
        self.assertIn('-I',calls[0][0]);self.assertNotEqual(calls[0][0][-1],calls[1][0][-1])
    def test_five_chinese_pages_fit_bounded_frame_and_overflow_fails(self):
        data={'results':[dict(text='中文'*2000) for _ in range(5)]}
        encoded=g.encode(data);self.assertGreater(len(encoded),32768);self.assertLessEqual(len(encoded),96*1024);self.assertEqual(json.loads(encoded),data)
        with self.assertRaisesRegex(ValueError,'BATCH_FRAME_LIMIT'):g.encode(dict(text='x'*(96*1024)))
    def test_job_count_rejected_before_worker(self):
        self.jobs(6)
        with self.assertRaisesRegex(ValueError,'JOB_COUNT'):g.execute(self.root,runner=lambda *a,**k:self.fail('must not spawn'))
    def test_site_query_normalization_is_narrow_and_scope_checked(self):
        q='site:docs.python.org/3/library pathlib shutil official documentation';p='pathlib shutil official documentation'
        rows=[{'url':'https://docs.python.org/3/library/pathlib.html'},{'url':'https://docs.python.org/3/library/shutil.html'}]
        self.assertTrue(a.query_matches(q,p,rows))
        self.assertFalse(a.query_matches(q,'shutil official documentation',rows))
        self.assertFalse(a.query_matches(q,p,[{'url':'https://attacker.example/3/library/a'}]))
        self.assertFalse(a.query_matches(q,p,[{'url':'https://docs.python.org/3/library-evil/a'}]))
        self.assertFalse(a.query_matches(q,p,[]))
        self.assertFalse(a.query_matches('pathlib shutil official documentation','pathlib',rows))

    def test_public_fixture_two_pages_no_live_backend(self):
        frozen=json.loads((HERE/'frozen.json').read_text())
        tool=a.Adapter(frozen,replay=True,root=self.root)
        with patch.object(a,'runtime_key',side_effect=AssertionError('no key')), patch.object(tool,'backend',side_effect=AssertionError('no backend')):
            found=tool.search(a.QUERY_REPLAY)
            result=tool.read_many(found['search_id'],[2,1])
        self.assertEqual(result['mode'],'fixture')
        self.assertEqual([p['rank'] for p in result['pages']],[2,1])
        self.assertTrue(all(p['source']['mode']=='synthetic' for p in result['pages']))
        with self.assertRaisesRegex(a.Stopped,'READ_ALREADY_RESERVED_NO_RETRY'):tool.read(found['search_id'],1)

    def test_public_batch_missing_config_before_reservation(self):
        root,sid,urls,folder=self.session();tool=a.Adapter({},root=root,clock=lambda:101)
        with patch.object(a,'REPO',self.root), patch.object(tool,'runtime_preflight',side_effect=ValueError('RUNTIME_NOT_CONFIGURED')), patch.object(a,'runtime_key') as key:
            with self.assertRaisesRegex(ValueError,'RUNTIME_NOT_CONFIGURED'):tool.read_many(sid,[1,2])
            key.assert_not_called()
        self.assertFalse((folder/'read_reserved.json').exists())

    def test_public_runtime_batch_declaration_stays_twelve_mib(self):
        reader=a.load_reader();frozen=json.loads((HERE/'frozen.json').read_text())
        cls=b.pipeline_class(reader,frozen);pipe=cls.__new__(cls)
        pipe.gate=Path('synthetic_gate.py');pipe.record={'purpose':'agent-bounded-batch'};pipe.kind='html'
        base=next(c for c in cls.__mro__ if c.__name__=='Pipeline');calls=[]
        def command(self,args,timeout):calls.append(args);return None
        with patch.object(base,'command',command):
            pipe.command(['python',str(pipe.gate),'declare','--target','x','--size-mb','2','--reason','x'])
        self.assertEqual(calls[0][calls[0].index('--size-mb')+1],'12')

    def test_public_live_backend_allows_only_scoped_normalization(self):
        reader=a.load_reader();backend=reader.Backend.__new__(reader.Backend);backend.search_used=False
        q='site:example.com/docs public docs'
        result=dict(query='public docs',outcome='SEARCH_RESULTS',source={'mode':'public'},results=[dict(url='https://example.com/docs/a',title='Public',snippet='Public')])
        backend.runs=[types.SimpleNamespace(evidence=self.root)]
        with patch.object(backend,'run',return_value=result):self.assertEqual(backend.search(q),result)
        backend.search_used=False;result['results'][0]['url']='https://example.org/docs/a'
        with patch.object(backend,'run',return_value=result):
            with self.assertRaisesRegex(ValueError,'SEARCH_QUERY_CHANGED'):backend.search(q)
if __name__=='__main__':unittest.main()
