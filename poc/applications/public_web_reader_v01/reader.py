"""Local operator wizard; trusted acquisition -> scanned bytes -> offline VM."""
import argparse
import getpass
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import warnings
HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
BASE = REPO / 'poc/review_probes/web_research_boundary_20261108'
sys.path.insert(0, str(HERE))
from reader_core import Stopped, require, public_url, query_text, save_report, wizard, plain, validate_candidates

def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def operator_key(record):
    record['key_read'] = True
    key = os.environ.get('TAVILY_API_KEY')
    if key:
        return key
    require(sys.stdin.isatty(), 'KEY_REQUIRES_TERMINAL')
    with warnings.catch_warnings():
        warnings.simplefilter('error', getpass.GetPassWarning)
        return getpass.getpass('Tavily Key（隐藏输入）：')

def build_pipeline_class(frozen):
    official = load('reader_official_reuse', BASE / 'official_extract_v01/run_extract.py')
    previous = official.previous
    previous.SOURCE_PATHS = [str((REPO / n).relative_to(REPO)).replace('\\', '/') for n in frozen['sources']]
    original_preflight = previous.source_preflight

    def preflight(current, tests, protected, **kw):
        return original_preflight(current, tests, protected, base=REPO)
    previous.source_preflight = preflight

    class ReaderPipeline(official.OfficialPipeline):

        def __init__(self, kind, question, url, demo):
            previous.Pipeline.__init__(self, 'live', frozen)
            self.kind, self.question, self.url, self.demo = (kind, question, url, demo)
            self.mode = 'demo' if demo else 'live'
            self.contract = load('reader_contract', HERE / 'response_contract.py')
            self.record.update(mode=self.mode, purpose='operator_public_reader', kind=kind, tavily_calls=0, page_attempts=0, transport_attempts=0, real_transport_attempts=0, question=question, source_url='https://api.tavily.com/search' if kind == 'search' else url)

        def command(self, argv, timeout=90):
            args = list(argv)
            if len(args) > 2 and str(args[1]) == str(self.gate) and (args[2] == 'declare'):
                args[args.index('--target') + 1] = 'Operator public reader ' + self.kind + (' offline capsule' if '--executes-code' in args else ' response')
                args[args.index('--size-mb') + 1] = '3'
                if '--reason' in args:
                    args[args.index('--reason') + 1] = 'Operator-configured offline parser runtime; no host execution of page code.'
            return previous.Pipeline.command(self, args, timeout)

        def acquire(self):
            incoming = self.work / 'incoming'
            tag = self.run_id + '-reader-response'
            source = 'local-staging' if self.demo else 'api.tavily.com' if self.kind == 'search' else 'operator-confirmed-public-https'
            self.declare(incoming, tag, source)
            incoming.mkdir()
            if self.demo:
                if self.kind == 'search':
                    body = json.dumps(dict(query=self.question, results=[dict(title='合成演示页面', url='https://example.com/reader-demo', content='演示：来源、代码排版与隔离解析。')], usage=dict(credits=0)), ensure_ascii=False).encode()
                else:
                    body = b'<html><title>Demo only</title><main><h1>Public reader demo</h1><p>This is synthetic text, not a fetched website.</p><pre>answer = 2 ** 200\nprint(answer)</pre><script>DO_NOT_RUN()</script></main></html>'
            elif self.kind == 'search':
                control = self.control
                control.QUERY, control.JOB_ID = (self.question, 'operator_search')

                def credential():
                    return operator_key(self.record)
                broker = control.FixedSearchBroker(credential)
                try:
                    reply = broker.handle('{"id":"operator_search"}')
                finally:
                    self.record.update(transport_attempts=broker.attempts, real_transport_attempts=broker.attempts, tavily_calls=broker.attempts)
                require(reply.get('outcome') == 'READY_FOR_SCAN', 'SEARCH_ACQUISITION')
                body = reply['body']
                self.record['provider_headers_sha256'] = reply['headers_sha256']
            else:
                broker_module = load('reader_page_broker', BASE / 'public_fetch_broker.py')
                broker_module.URLS = frozenset([self.url])
                broker_module.MAX_BODY = 1048576
                broker = broker_module.Broker(incoming / 'raw')
                try:
                    reply = broker.handle(json.dumps(dict(id='page', url=self.url)))
                finally:
                    self.record.update(transport_attempts=broker.calls, real_transport_attempts=broker.calls, page_attempts=broker.calls)
                require(reply.get('outcome') == 'FETCHED', 'PAGE_ACQUISITION')
                dest = incoming / 'raw/page'
                require((dest / 'body.bin').stat().st_size <= 1048576 and (dest / 'headers.txt').stat().st_size <= 8192, 'RESPONSE_SIZE')
                body, headers = ((dest / 'body.bin').read_bytes(), (dest / 'headers.txt').read_bytes())
                self.contract.check_headers(headers, body, 'html')
                self.record['provider_headers_sha256'] = hashlib.sha256(headers).hexdigest()
                self.record['validated_addresses'] = reply['validated_addresses']
                self.record['pinned_ipv4'] = reply['pinned_ipv4']
            mime = 'application/json' if self.kind == 'search' else 'text/html'
            canonical = f'HTTP/1.1 200 OK\r\nContent-Type: {mime}; charset=utf-8\r\nContent-Length: {len(body)}\r\n\r\n'.encode()
            self.contract.check_headers(canonical, body, self.kind)
            (incoming / 'input.body').write_bytes(body)
            (incoming / 'input.headers').write_bytes(canonical)
            self.record.update(acquired_at=previous.now(), provider_body_sha256=hashlib.sha256(body).hexdigest(), projected_headers_sha256=hashlib.sha256(canonical).hexdigest(), body_bytes=len(body))
            return (incoming, self.scan(incoming, tag))

        def stage(self, incoming, receipt):
            self.binding.verify(incoming, receipt)
            require(sha(incoming / 'input.body') == self.record['provider_body_sha256'] and sha(incoming / 'input.headers') == self.record['projected_headers_sha256'], 'INPUT_CHANGED')
            capsule, tag = (self.work / 'capsule', self.run_id + '-reader-capsule')
            self.declare(capsule, tag, 'local-staging', code=True)
            capsule.mkdir()
            mapping = {'response_contract.py': HERE / 'response_contract.py', 'reader_core.py': HERE / 'reader_core.py', 'test_worker.py': HERE / 'test_worker.py', 'tavily_worker.py': HERE / 'reader_worker.py', 'reader_worker.py': HERE / 'reader_worker.py', 'section_worker.py': BASE / 'official_extract_v01/section_worker.py', 'parser_worker.py': BASE / 'search_extract_v01/parser_worker.py', 'guest_probe.sh': HERE / 'guest_probe.sh', 'guest_batch.py': BASE / 'tavily_pipeline_v01/guest_batch.py'}
            for name, path in mapping.items():
                (capsule / name).write_bytes(path.read_bytes().replace(b'\r\n', b'\n'))
            for name in ('input.body', 'input.headers'):
                (capsule / name).write_bytes((incoming / name).read_bytes())
            spec = dict(id='reader', kind=self.kind, body='input.body', body_sha256=self.record['provider_body_sha256'], headers='input.headers', headers_sha256=self.record['projected_headers_sha256'], source=dict(url=self.record['source_url'], mode='synthetic' if self.demo else 'public', acquired_at=self.record['acquired_at'], staged_at=previous.now()))
            previous.put(capsule / 'input.json', spec)
            previous.put(capsule / 'jobs.json', dict(mode=self.mode, jobs=[dict(id='reader', spec='input.json', expected=['SEARCH_RESULTS', 'EMPTY_RESULTS'] if self.kind == 'search' else ['TEXT'])]))
            checks = [previous.sha(p) + '  ' + p.name for p in sorted(capsule.iterdir())]
            checks += [r['sha256'] + '  apks/' + r['filename'] for r in self.package_rows]
            (capsule / 'input_checksums.txt').write_text('\n'.join(checks) + '\n', encoding='utf-8', newline='\n')
            scan = self.scan(capsule, tag)
            self.record.update(checksum_sha256=sha(capsule / 'input_checksums.txt'), job_count=1)
            previous.put(self.evidence / 'staged_frozen.json', dict(sources=self.frozen, record=self.record, incoming_scan=receipt, capsule_scan=scan))
            return (capsule, scan)
    return ReaderPipeline

class Backend:

    def __init__(self, frozen, demo=False, pipeline_class=None):
        self.frozen, self.demo = (frozen, demo)
        self.pipeline_class = pipeline_class or build_pipeline_class(frozen)
        self.search_used = self.page_used = False
        self.report_path = None
        self.runs = []

    def run(self, kind, question, url=None):
        pipe = self.pipeline_class(kind, question, url, self.demo)
        self.runs.append(pipe)
        print('正在隔离解析，通常约半分钟；请保留窗口。')
        print('回执目录：' + plain(str(pipe.evidence)))
        receipt = pipe.execute()
        require(receipt.get('phase') == 'PIPELINE_COMPLETE', 'PIPELINE_STOPPED')
        return receipt['parsed']['results'][0]

    def search(self, question):
        require(not self.search_used, 'SEARCH_ALREADY_USED')
        self.search_used = True
        self.question = query_text(question)
        result = self.run('search', self.question)
        require(result.get('query') == self.question, 'SEARCH_QUERY_CHANGED')
        self.candidate_urls = tuple((row['url'] for row in validate_candidates(result)))
        self.report_path = self.runs[-1].evidence / 'report.html'
        return result

    def read(self, url, candidates):
        require(self.search_used and (not self.page_used) and (url in candidates) and (url in self.candidate_urls), 'PAGE_SCOPE')
        public_url(url)
        self.page_used = True
        return self.run('html', self.question, url)

    def report(self, question, search, page=None, state='SEARCH_COMPLETE'):
        save_report(self.report_path, question, search, page, 'COMPLETE' if page else state)
if __name__ == '__main__':
    try:
        sys.exit(main())
    except (Exception, KeyboardInterrupt):
        print('STOPPED：本次已停止；请求可能已发送，请查看本地回执，不会自动重试。')
        sys.exit(1)
