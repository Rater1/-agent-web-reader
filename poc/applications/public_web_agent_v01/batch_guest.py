"""Bounded batch coordinator; every page still gets a separate clean worker."""
import json
from pathlib import Path
import subprocess
import sys
import time

MAX_FRAME=96*1024
def need(ok,why):
    if not ok: raise ValueError(why)
def encode(output):
    # UTF-8 avoids multiplying Chinese excerpts into six-byte ASCII escapes.
    result=json.dumps(output,ensure_ascii=False).encode('utf-8')
    need(len(result)<=MAX_FRAME,'BATCH_FRAME_LIMIT')
    return result
def execute(root,runner=subprocess.run):
    jobs=json.loads((root/'jobs.json').read_text(encoding='utf-8'))
    need(type(jobs) is dict and set(jobs)=={'mode','jobs'} and jobs['mode'] in ('live','demo'),'JOBS_SCHEMA')
    rows=jobs['jobs'];need(type(rows) is list and 1<=len(rows)<=5,'JOB_COUNT')
    need([r.get('id') for r in rows]==['page'+str(i) for i in range(len(rows))],'JOB_IDS')
    output=[]
    for i,job in enumerate(rows):
        need(set(job)=={'id','spec','expected'} and job['spec']==f'page{i}.json' and job['expected']==['TEXT'],'JOB_SCHEMA')
        start=time.perf_counter()
        child=runner([sys.executable,'-I','-B','-c',"import sys,runpy;sys.path.insert(0,'/mnt/fixtures');runpy.run_path('/mnt/fixtures/tavily_worker.py',run_name='__main__')",str(root),str(root/job['spec'])],capture_output=True,timeout=5,env={'PATH':'/usr/bin:/bin','LANG':'C.UTF-8'})
        need(child.returncode==0 and len(child.stdout)<=32768,'WORKER_EXIT_OR_LIMIT')
        result=json.loads(child.stdout)
        need(type(result) is dict and result.get('outcome')=='TEXT','JOB_OUTCOME_MISMATCH')
        output.append({**result,'id':job['id'],'worker_process_seconds':time.perf_counter()-start})
    return dict(mode=jobs['mode'],results=output,network_interfaces=sorted(p.name for p in Path('/sys/class/net').iterdir()),result_urls_followed=0)
if __name__=='__main__':
    sys.stdout.buffer.write(b'TAVILY_PARSED_RESULT:'+encode(execute(Path(sys.argv[1])))+b'\n')
