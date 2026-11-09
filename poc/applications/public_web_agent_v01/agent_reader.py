"""JSON CLI for agents. Reuses the frozen offline reader; no interactive prompts."""
import contextlib
import datetime
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
READER = REPO/'poc/applications/public_web_reader_v01'
STATE = REPO/'.local/public_web_agent_v01'
MAX_DAILY_SEARCHES = 20
MAX_AGE = 7200
QUERY_REPLAY = 'Python URL parsing documentation'
FIXTURES = HERE / 'fixtures'


class Stopped(ValueError):
    pass


def require(ok, code):
    if not ok:
        raise Stopped(code)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_reader():
    spec = importlib.util.spec_from_file_location('agent_local_reader', READER/'reader.py')
    reader = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(reader)
    return reader


def request(text):
    require(type(text) is str and len(text.encode('utf-8')) <= 4096, 'REQUEST_SIZE')
    def unique(pairs):
        out = {}
        for key, value in pairs:
            require(key not in out, 'DUPLICATE_FIELD')
            out[key] = value
        return out
    try:
        data = json.loads(text, object_pairs_hook=unique)
    except (ValueError, RecursionError):
        raise Stopped('REQUEST_JSON') from None
    require(type(data) is dict, 'REQUEST_SCHEMA')
    if data.get('op') == 'search':
        require(set(data) == {'op', 'query'} and type(data['query']) is str, 'REQUEST_SCHEMA')
        q = data['query']
        require(1 <= len(q.strip()) <= 300 and not any(ord(c) < 32 for c in q), 'QUERY_LENGTH_OR_CONTROL')
        # Concrete secret/path patterns only; this cannot prove public semantics.
        require(not re.search(r'(?i)(?:tvly-|sk-[a-z0-9]{12}|authorization\s*:|cookie\s*:|[a-z]:[\\/]|/Users/|/home/)', q), 'QUERY_SECRET_OR_LOCAL_PATH')
        return dict(op='search', query=q.strip())
    if data.get('op') == 'read_many':
        require(set(data)=={'op','search_id','ranks'} and type(data['search_id']) is str and
                re.fullmatch(r'[a-f0-9]{32}',data['search_id']) and type(data['ranks']) is list and
                1<=len(data['ranks'])<=5 and all(type(n) is int and 1<=n<=5 for n in data['ranks']) and
                len(set(data['ranks']))==len(data['ranks']), 'REQUEST_SCHEMA')
        return data
    require(data.get('op') == 'read' and set(data) == {'op', 'search_id', 'rank'} and
            type(data['search_id']) is str and re.fullmatch(r'[a-f0-9]{32}', data['search_id']) and
            type(data['rank']) is int and 1 <= data['rank'] <= 5, 'REQUEST_SCHEMA')
    return data


def runtime_key(record):
    """Only this trusted process obtains the configured API key; never serialized."""
    record['key_read'] = True
    key = os.environ.get('TAVILY_API_KEY')
    if not key and sys.platform == 'win32':
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, 'Environment') as handle:
                key = winreg.QueryValueEx(handle, 'TAVILY_API_KEY')[0]
        except OSError:
            key = None
    require(type(key) is str and key, 'AUTH_NOT_CONFIGURED')
    return key


def write_json(path, value):
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=True, indent=2)
        stream.write('\n')

def query_matches(requested, returned, rows):
    if requested==returned:return True
    # Observed Tavily normalization: only a leading site operator may disappear.
    # Keep the requested query intact and require every result to obey its scope.
    m=re.fullmatch(r'site:([a-z0-9.-]+)(/[^\s]*)? (.+)',requested)
    if m is None or returned!=m[3] or not rows:return False
    prefix=(m[2] or '/').rstrip('/')
    return all(urlsplit(r['url']).hostname==m[1] and
               (not prefix or urlsplit(r['url']).path==prefix or urlsplit(r['url']).path.startswith(prefix+'/')) for r in rows)


class Adapter:
    def __init__(self, frozen, *, replay=False, root=STATE, backend_factory=None, batch_backend_factory=None, clock=time.time):
        self.frozen, self.replay, self.root, self.clock = frozen, replay, Path(root), clock
        self.reader = load_reader()
        self.reader.operator_key = runtime_key
        self.backend_factory = backend_factory
        self.batch_backend_factory = batch_backend_factory
        self.root.mkdir(parents=True, exist_ok=True)

    def backend(self):
        if self.backend_factory:
            return self.backend_factory()
        return self.reader.Backend(self.frozen, demo=False)

    def runtime_preflight(self):
        module = self.reader.load('agent_runtime_config', self.reader.BASE/'runtime_config.py')
        return module.load_config()

    def replay_result(self, kind):
        file = FIXTURES / (kind + '.json')
        require(sha(file) == self.frozen['fixture_sha256'][kind], 'FIXTURE_CHANGED')
        data = json.loads(file.read_text(encoding='utf-8'))
        require(data.get('fixture_version') == 1 and data.get('kind') == kind and
                data.get('synthetic') is True, 'FIXTURE_SCOPE')
        return data['result'], 'fixture-' + kind

    def reserve_search(self):
        folder = self.root/'budget'
        folder.mkdir(exist_ok=True)
        day = datetime.datetime.now().date().isoformat()
        for number in range(MAX_DAILY_SEARCHES):
            try:
                write_json(folder/f'{day}-{number:02d}.json', dict(reserved_at=self.clock(), status='RESERVED_MAY_HAVE_SENT'))
                return number + 1
            except FileExistsError:
                continue
        raise Stopped('LOCAL_DAILY_BUDGET_EXHAUSTED')

    def search(self, q):
        self.reader.query_text(q)
        if self.replay:
            require(q == QUERY_REPLAY, 'REPLAY_QUERY_ONLY')
            result, run = self.replay_result('search')
            reserved = 0
        else:
            # Fail before budget/transport when there is no configured key. The
            # actual bridge still validates key syntax and never passes it to VM.
            self.runtime_preflight()
            runtime_key({})
            reserved = self.reserve_search()
            backend = self.backend()
            result = backend.search(q)
            run = backend.runs[-1].record['run_id']
        return self.accept_search(q,result,run,reserved)

    def accept_search(self,q,result,run,reserved):
        rows = self.reader.validate_candidates(result)
        require(query_matches(q,result.get('query'),rows), 'QUERY_BINDING')
        search_id = uuid.uuid4().hex
        folder = self.root/search_id
        folder.mkdir()
        require(run == 'fixture-search' if self.replay else re.fullmatch(r'tvp-\d{8}-\d{6}-[a-f0-9]{8}', run) is not None, 'RUN_ID')
        receipt = FIXTURES/'search.json' if self.replay else REPO/'.local/tavily_pipeline_v01'/run/'run_receipt.json'
        state = dict(search_id=search_id, created_at=self.clock(), replay=self.replay, query=q,
                     frozen_sha256=sha(HERE/'frozen.json'), source_run=run, receipt_sha256=sha(receipt),
                     result=result, urls=[r['url'] for r in rows])
        write_json(folder/'state.json', state)
        return dict(status='SEARCH_OK', mode='fixture' if self.replay else 'live', search_id=search_id,
                    query=q, provider_query=result.get('query'), results=[dict(rank=i+1, **row) for i, row in enumerate(rows)],
                    source=result['source'], credits_reported=result.get('credits_reported'),
                    local_search_slots_reserved=reserved, local_daily_limit=MAX_DAILY_SEARCHES,
                    external_content_is_untrusted=True)

    def read(self, search_id, rank):
        page=self.read_many(search_id,[rank])['pages'][0]
        return dict(status='READ_OK',mode='fixture' if self.replay else 'live',search_id=search_id,**page,
                    external_content_is_untrusted=True)

    def read_many(self, search_id, ranks):
        require(type(ranks) is list and 1<=len(ranks)<=5 and all(type(n) is int and 1<=n<=5 for n in ranks) and len(set(ranks))==len(ranks),'RANK_SCOPE')
        folder = self.root/search_id
        file = folder/'state.json'
        require(file.is_file() and not file.is_symlink() and file.stat().st_size <= 65536, 'SEARCH_ID_NOT_FOUND')
        state = json.loads(file.read_text(encoding='utf-8'))
        require(state['search_id'] == search_id and state['replay'] is self.replay and
                state['frozen_sha256'] == sha(HERE/'frozen.json') and 0 <= self.clock()-state['created_at'] <= MAX_AGE, 'SESSION_EXPIRED_OR_CHANGED')
        run = state['source_run']
        if self.replay:
            require(run == 'fixture-search', 'SESSION_RECEIPT_SCOPE')
            receipt_file = FIXTURES/'search.json'
        else:
            require(type(run) is str and re.fullmatch(r'tvp-\d{8}-\d{6}-[a-f0-9]{8}', run), 'SESSION_RECEIPT_SCOPE')
            receipt_file = REPO/'.local/tavily_pipeline_v01'/run/'run_receipt.json'
        require(sha(receipt_file) == state['receipt_sha256'], 'SEARCH_RECEIPT_CHANGED')
        original = json.loads(receipt_file.read_text(encoding='utf-8'))
        if self.replay:
            require(original.get('fixture_version') == 1 and original.get('synthetic') is True and
                    original.get('kind') == 'search' and original.get('result') == state['result'], 'SEARCH_RESULT_CHANGED')
            require(state['query'] == QUERY_REPLAY and state['result']['source'].get('mode') == 'synthetic' and
                    query_matches(state['query'],state['result'].get('query'),self.reader.validate_candidates(state['result'])), 'SESSION_RECEIPT_SCOPE')
        else:
            require(original['phase'] == 'PIPELINE_COMPLETE' and original['parsed']['results'][0] == state['result'], 'SEARCH_RESULT_CHANGED')
            require(original.get('mode') == 'live' and original.get('kind') == 'search' and
                    original.get('question') == state['query'] and
                    original.get('source_url') == 'https://api.tavily.com/search' and
                    original['parsed'].get('mode') == 'live' and
                    query_matches(state['query'],state['result'].get('query'),self.reader.validate_candidates(state['result'])) and
                    state['result']['source'].get('mode') == 'public', 'SESSION_RECEIPT_SCOPE')
        rows = self.reader.validate_candidates(state['result'])
        require([r['url'] for r in rows] == state['urls'] and all(n<=len(rows) for n in ranks), 'RANK_SCOPE')
        urls=[rows[n-1]['url'] for n in ranks]
        require(len(set(urls))==len(urls),'DUPLICATE_PAGE_URL')
        for url in urls:self.reader.public_url(url)
        if not self.replay:
            self.runtime_preflight()
        try:
            write_json(folder/'read_reserved.json', dict(ranks=ranks, reserved_at=self.clock(), status='RESERVED_MAY_HAVE_SENT'))
        except FileExistsError:
            raise Stopped('READ_ALREADY_RESERVED_NO_RETRY') from None
        if self.replay:
            results=[]
            for rank in ranks:
                result, page_run = self.replay_result('html' if rank==1 else 'html2')
                results.append(result)
        elif len(ranks)==1:
            backend = self.backend()
            backend.search_used, backend.question, backend.candidate_urls = True, state['query'], tuple(state['urls'])
            results=[backend.read(urls[0], tuple(state['urls']))]
            page_run = backend.runs[-1].record['run_id']
        else:
            from_batch=importlib.util.spec_from_file_location('agent_batch_backend',HERE/'batch_backend.py')
            module=importlib.util.module_from_spec(from_batch);from_batch.loader.exec_module(module)
            backend=self.batch_backend_factory() if self.batch_backend_factory else module.BatchBackend(self.reader,self.frozen)
            results=backend.read_many(state['query'],urls)
            page_run = backend.runs[-1].record['run_id']
        require(type(results) is list and len(results)==len(urls),'BATCH_RESULT_COUNT')
        pages=[]
        for rank,url,result in zip(ranks,urls,results):
            require(result.get('outcome') == 'TEXT' and result['source']['url'] == url, 'PAGE_BINDING')
            pages.append(dict(rank=rank,url=url,title=result.get('title'),text=result['text'],scope=result['scope'],truncated=result['truncated'],source=result['source'],source_run=page_run))
        return dict(status='READ_MANY_OK', mode='fixture' if self.replay else 'live', search_id=search_id,pages=pages,
                    page_count=len(pages),external_content_is_untrusted=True)

    def handle(self, data):
        if data['op']=='search':return self.search(data['query'])
        return self.read_many(data['search_id'],data['ranks']) if data['op']=='read_many' else self.read(data['search_id'],data['rank'])

@contextlib.contextmanager
def operation_lock(root=STATE):
    root=Path(root);root.mkdir(parents=True,exist_ok=True);lock=root/'vm_operation.lock'
    try:lock.mkdir()
    except FileExistsError:raise Stopped('TOOL_BUSY_OR_STALE_LOCK') from None
    try:yield
    finally:lock.rmdir()


def selftests():
    env = dict(SystemRoot=r'C:\Windows', WINDIR=r'C:\Windows', PATH=r'C:\Windows\System32')
    for opt in (False, True):
        for name in ('test_agent.py','test_batch.py'):
            child = subprocess.run([sys.executable, '-I', '-S', '-B', *(['-O'] if opt else []), str(HERE/name)],
                                   env=env, capture_output=True, timeout=30)
            if child.returncode:
                return 1
    return 0


def guard(frozen, tests, protected):
    require(set(frozen['sources']) == set(json.loads((HERE/'source_paths.json').read_text(encoding='utf-8'))), 'SOURCE_SET')
    require(all(sha(REPO/p) == value for p, value in frozen['sources'].items()), 'SOURCE_CHANGED')
    require(sha(REPO/'poc/download_gate/dl_gate.py') == frozen['download_gate_sha256'], 'GATE_CHANGED')
    require(tests() == 0, 'SELFTEST_FAILED')
    return protected()


def main():
    if sys.argv[1:] == ['--describe']:
        print(json.dumps(dict(tool='local_public_web', transport='JSON stdin/stdout', operations=['search', 'read', 'read_many'],max_pages_per_batch=5,
                              replay_query=QUERY_REPLAY, max_daily_searches=MAX_DAILY_SEARCHES)))
        return 0
    require(sys.argv[1:] in ([], ['--fixture']), 'CLI_SCOPE')
    raw = sys.stdin.buffer.read(4097)
    require(len(raw) <= 4096, 'REQUEST_SIZE')
    data = request(raw.decode('utf-8'))
    frozen = json.loads((HERE/'frozen.json').read_text(encoding='utf-8'))
    def perform():
        # The old driver prints progress. Keep the machine response as one JSON.
        with contextlib.redirect_stdout(io.StringIO()):
            if sys.argv[1:]==['--fixture']:return Adapter(frozen,replay=True).handle(data)
            with operation_lock():return Adapter(frozen).handle(data)
    result = guard(frozen, selftests, perform)
    sys.stdout.write(json.dumps(result, ensure_ascii=True)+'\n')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as exc:
        safe = {'REQUEST_SIZE', 'REQUEST_JSON', 'REQUEST_SCHEMA', 'QUERY_LENGTH_OR_CONTROL', 'QUERY_SECRET_OR_LOCAL_PATH',
                'AUTH_NOT_CONFIGURED', 'LOCAL_DAILY_BUDGET_EXHAUSTED', 'SEARCH_ID_NOT_FOUND', 'SESSION_EXPIRED_OR_CHANGED',
                'READ_ALREADY_RESERVED_NO_RETRY', 'RANK_SCOPE', 'SOURCE_SET', 'SOURCE_CHANGED', 'GATE_CHANGED', 'SELFTEST_FAILED',
                'SEARCH_RECEIPT_CHANGED', 'SEARCH_RESULT_CHANGED', 'SESSION_RECEIPT_SCOPE', 'REPLAY_QUERY_ONLY', 'REPLAY_CHANGED', 'CLI_SCOPE',
                'DUPLICATE_PAGE_URL','FIXTURE_CHANGED','FIXTURE_SCOPE','RUNTIME_NOT_CONFIGURED','RUNTIME_CONFIG_INVALID','LIVE_WINDOWS_ONLY','TOOL_BUSY_OR_STALE_LOCK','BATCH_RESULT_COUNT'}
        safe.add('QUERY_BINDING')
        code = exc.args[0] if len(exc.args) == 1 and type(exc.args[0]) is str and exc.args[0] in safe else 'TOOL_STOPPED'
        print(json.dumps(dict(status='STOPPED', reason=code, automatic_retry=False, request_status='UNKNOWN')))
        sys.exit(1)
