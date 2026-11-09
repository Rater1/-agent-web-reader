"""Thin multi-page adapter over the existing acquisition/scan/offline VM stack."""
import hashlib
import json
from pathlib import Path
import time
import datetime

HERE=Path(__file__).resolve().parent
def now(): return datetime.datetime.now(datetime.timezone.utc).isoformat()
def need(ok,why):
    if not ok: raise ValueError(why)
def validate_results(rows,urls,hashes=None):
    need(type(rows) is list and len(rows)==len(urls),'BATCH_RESULT_COUNT')
    for i,(r,url) in enumerate(zip(rows,urls)):
        need(type(r) is dict and r.get('id')=='page'+str(i) and r.get('outcome')=='TEXT','BATCH_RESULT_ID')
        need(type(r.get('source')) is dict and r['source'].get('url')==url,'BATCH_PAGE_BINDING')
        need(type(r.get('text')) is str and len(r['text'])<=4000 and type(r.get('truncated')) is bool and type(r.get('scope')) is str,'BATCH_PAGE_SCHEMA')
        if hashes is not None: need(r.get('input_sha256')==hashes[i],'BATCH_BODY_BINDING')
    return rows

def acquire_pages(pipe,incoming,broker,urls):
    """Stop at first fetch/header failure; never request remaining URLs."""
    rows=[]
    try:
        for i,url in enumerate(urls):
            reply=broker.handle(json.dumps(dict(id='page'+str(i),url=url)))
            need(reply.get('outcome')=='FETCHED','BATCH_PAGE_ACQUISITION')
            dest=incoming/'raw'/('page'+str(i))
            need((dest/'body.bin').stat().st_size<=1048576 and (dest/'headers.txt').stat().st_size<=8192,'RESPONSE_SIZE')
            body,headers=(dest/'body.bin').read_bytes(),(dest/'headers.txt').read_bytes()
            pipe.contract.check_headers(headers,body,'html')
            rows.append((body,hashlib.sha256(headers).hexdigest(),reply['validated_addresses'],reply['pinned_ipv4'],now()))
    finally:
        pipe.record.update(transport_attempts=broker.calls,real_transport_attempts=broker.calls,page_attempts=broker.calls)
    return rows

def pipeline_class(reader,frozen):
    Parent=reader.build_pipeline_class(frozen)
    class Batch(Parent):
        def __init__(self,question,urls,demo=False,fixtures=None):
            need(1<=len(urls)<=5 and len(set(urls))==len(urls),'BATCH_URLS')
            for u in urls: reader.public_url(u)
            super().__init__('html',question,urls[0],demo)
            self.urls=tuple(urls);self.fixtures=fixtures
            self.record.update(purpose='agent-bounded-batch',requested_pages=len(urls),vm_start_count=0)
        def command(self,argv,timeout=90):
            args=list(argv)
            if len(args)>2 and str(args[1])==str(self.gate) and args[2]=='declare':
                args[args.index('--target')+1]='Agent batch: at most five verified static pages'
                # Five raw bodies + five canonical copies can exceed 6 MiB.
                args[args.index('--size-mb')+1]='12'
                if '--reason' in args: args[args.index('--reason')+1]='Operator-requested bounded multi-page offline parsing; no VM networking or host page execution.'
            return super().command(args,timeout)
        def vbox(self,*args):
            if args and args[0]=='startvm': self.record['vm_start_count']+=1
            return super().vbox(*args)
        def acquire(self):
            incoming=self.work/'incoming';tag=self.run_id+'-batch-response'
            self.declare(incoming,tag,'local-staging' if self.demo else 'operator-confirmed-public-https');incoming.mkdir()
            if self.demo:
                need(self.fixtures is not None and len(self.fixtures)==len(self.urls),'DEMO_FIXTURES')
                rows=[(b,None,[],None,now()) for b in self.fixtures]
                self.record.update(transport_attempts=0,real_transport_attempts=0,page_attempts=0)
            else:
                module=reader.load('agent_batch_broker',reader.BASE/'public_fetch_broker.py')
                module.URLS=frozenset(self.urls);module.MAX_BODY=1048576
                rows=acquire_pages(self,incoming,module.Broker(incoming/'raw'),self.urls)
            self.page_specs=[]
            for i,(url,(body,header_hash,addresses,pinned,acquired)) in enumerate(zip(self.urls,rows)):
                need(type(body) is bytes and len(body)<=1048576,'BODY_SIZE')
                canonical=f'HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nContent-Length: {len(body)}\r\n\r\n'.encode()
                self.contract.check_headers(canonical,body,'html')
                body_name=f'page{i}.body';header_name=f'page{i}.headers'
                (incoming/body_name).write_bytes(body);(incoming/header_name).write_bytes(canonical)
                source=dict(url=url,mode='synthetic' if self.demo else 'public',acquired_at=acquired,staged_at=None)
                spec=dict(id='page'+str(i),kind='html',body=body_name,body_sha256=hashlib.sha256(body).hexdigest(),headers=header_name,headers_sha256=hashlib.sha256(canonical).hexdigest(),source=source)
                self.page_specs.append(spec)
                (incoming/f'page{i}.json').write_text(json.dumps(spec),encoding='utf-8')
            self.record.update(provider_body_sha256=self.page_specs[0]['body_sha256'],page_inputs=[dict(id=x['id'],body_sha256=x['body_sha256'],url=x['source']['url'],acquired_at=x['source']['acquired_at'],provider_headers_sha256=rows[i][1],validated_addresses=rows[i][2],pinned_ipv4=rows[i][3]) for i,x in enumerate(self.page_specs)])
            return incoming,self.scan(incoming,tag)
        def stage(self,incoming,receipt):
            self.binding.verify(incoming,receipt)
            cap=self.work/'capsule';tag=self.run_id+'-batch-capsule';self.declare(cap,tag,'local-staging',code=True);cap.mkdir()
            mapping={'response_contract.py':reader.HERE/'response_contract.py','reader_core.py':reader.HERE/'reader_core.py','test_worker.py':reader.HERE/'test_worker.py','tavily_worker.py':reader.HERE/'reader_worker.py','reader_worker.py':reader.HERE/'reader_worker.py','section_worker.py':reader.BASE/'official_extract_v01/section_worker.py','parser_worker.py':reader.BASE/'search_extract_v01/parser_worker.py','guest_probe.sh':reader.HERE/'guest_probe.sh','guest_batch.py':HERE/'batch_guest.py'}
            for name,p in mapping.items(): (cap/name).write_bytes(p.read_bytes().replace(b'\r\n',b'\n'))
            for i,spec in enumerate(self.page_specs):
                for key in ('body','headers'):
                    b=(incoming/spec[key]).read_bytes();need(hashlib.sha256(b).hexdigest()==spec[key+'_sha256'],'INPUT_CHANGED');(cap/spec[key]).write_bytes(b)
                spec={**spec,'source':{**spec['source'],'staged_at':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())}}
                (cap/f'page{i}.json').write_text(json.dumps(spec),encoding='utf-8')
            jobs=dict(mode=self.mode,jobs=[dict(id='page'+str(i),spec=f'page{i}.json',expected=['TEXT']) for i in range(len(self.urls))])
            (cap/'jobs.json').write_text(json.dumps(jobs),encoding='utf-8')
            checks=[reader.sha(p)+'  '+p.name for p in sorted(cap.iterdir())]+[r['sha256']+'  apks/'+r['filename'] for r in self.package_rows]
            (cap/'input_checksums.txt').write_text('\n'.join(checks)+'\n',encoding='utf-8',newline='\n')
            scan=self.scan(cap,tag);self.record.update(checksum_sha256=reader.sha(cap/'input_checksums.txt'),job_count=len(self.urls))
            (self.evidence/'staged_frozen.json').write_text(json.dumps(dict(sources=self.frozen,incoming_scan=receipt,capsule_scan=scan)),encoding='utf-8')
            return cap,scan
        def guest(self,cap,scan):
            start=time.perf_counter();super().guest(cap,scan)
            self.record['phase']='BATCH_RESULT_VALIDATION'
            validate_results(self.record['parsed']['results'],self.urls,[s['body_sha256'] for s in self.page_specs])
            need(self.record['vm_start_count']==1,'BATCH_VM_START_COUNT')
            self.record.update(phase='PIPELINE_COMPLETE',guest_with_cleanup_seconds=time.perf_counter()-start)
    return Batch

class BatchBackend:
    def __init__(self,reader,frozen): self.reader,self.frozen,self.runs=reader,frozen,[]
    def read_many(self,question,urls):
        cls=pipeline_class(self.reader,self.frozen);pipe=cls(question,urls);self.runs.append(pipe)
        start=time.perf_counter();receipt=pipe.execute();pipe.record['total_seconds']=time.perf_counter()-start
        # Persist the complete timing even if execute() already wrote its receipt.
        (pipe.evidence/'run_receipt.json').write_text(json.dumps(pipe.record,ensure_ascii=True,indent=2),encoding='utf-8')
        need(receipt.get('phase')=='PIPELINE_COMPLETE','BATCH_PIPELINE_STOPPED')
        return validate_results(receipt['parsed']['results'],urls)
