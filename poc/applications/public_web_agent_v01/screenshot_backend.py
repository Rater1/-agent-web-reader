"""Acquire a bounded public page and stage exact scanned bytes for offline capture."""

import datetime
import hashlib
from html.parser import HTMLParser
import importlib.util
import ipaddress
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
from urllib.parse import parse_qsl, urlsplit
import uuid

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
BASE = ROOT / 'poc/review_probes/web_research_boundary_20261108'
GATE = ROOT / 'poc/download_gate/dl_gate.py'
MAX_BODY = 1024**2
MAX_TOTAL = 8 * MAX_BODY
MAX_HEADERS = 8192
SECRET_KEYS = {'access_token', 'api_key', 'key', 'token', 'password', 'secret'}
CONTENT_TYPES = {
    'html': {'text/html'},
    'script': {'application/javascript', 'text/javascript', 'application/x-javascript'},
    'style': {'text/css'},
}
REPLAY_HEADERS = frozenset({
    'content-type', 'content-security-policy', 'content-security-policy-report-only',
    'x-content-type-options', 'x-frame-options', 'permissions-policy', 'referrer-policy',
    'cross-origin-embedder-policy', 'cross-origin-opener-policy', 'cross-origin-resource-policy',
    'access-control-allow-origin', 'access-control-allow-credentials',
})
BASE_URI = re.compile(r'(?:^|[;,])\s*base-uri(?:\s|[;,]|$)', re.I)


def need(condition, reason):
    if not condition:
        raise ValueError(reason)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def ascii_escape(value, encode):
    return ''.join(f'%{ord(c):02X}' if c in encode else c for c in value)


def browser_path(path):
    segments = (path or '/')[1:].split('/')
    clean = []
    for index, segment in enumerate(segments):
        dots = re.sub(r'%2e', '.', segment, flags=re.I)
        if dots == '..' and clean:
            clean.pop()
        if dots not in ('.', '..'):
            clean.append(segment)
        elif index == len(segments) - 1:
            clean.append('')
    return '/' + ascii_escape('/'.join(clean), '"<>^\x60{}')


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def url(value):
    need(type(value) is str and 0 < len(value) <= 512 and
         all(32 < ord(c) < 127 for c in value) and '\\' not in value, 'SCREENSHOT_URL')
    try:
        parts = urlsplit(value)
        host = parts.hostname or ''
        port = parts.port
    except ValueError:
        raise ValueError('SCREENSHOT_URL') from None
    need(parts.scheme == 'https' and parts.username is None and parts.password is None and
         '#' not in value and port in (None, 443) and '.' in host and
         re.fullmatch(r'[a-z0-9]+(?:[.-][a-z0-9]+)*', host), 'SCREENSHOT_URL')
    need(not re.search(r'(?:tvly-|sk-[a-z0-9]{12})', value, re.I) and
         all(name.lower() not in SECRET_KEYS for name, _ in parse_qsl(parts.query, keep_blank_values=True)),
         'SCREENSHOT_URL_SECRET')
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        # Reject legacy numeric host spellings that a browser could reinterpret.
        need(not re.fullmatch(r'(?:[0-9]+|0x[0-9a-f]*)', host.rsplit('.', 1)[-1]), 'SCREENSHOT_URL')
    else:
        need(address.is_global and not address.is_multicast and not address.is_reserved, 'SCREENSHOT_URL')
    query = '?' + ascii_escape(parts.query, '"<>\x27') if '?' in value else ''
    normalized = 'https://' + host + browser_path(parts.path) + query
    need(len(normalized) <= 512, 'SCREENSHOT_URL')
    return normalized


def origin(value):
    parts = urlsplit(value)
    return parts.scheme, parts.hostname, parts.port or 443


def resolve_reference(base, raw):
    reference = raw.strip().split('#', 1)[0]
    need('\\' not in reference and not reference.startswith('///'), 'SCREENSHOT_URL')
    parts = urlsplit(reference)
    if parts.scheme:
        return reference
    if reference.startswith('//'):
        return urlsplit(base).scheme + ':' + reference
    if not parts.path:
        return base.split('?', 1)[0] + reference if '?' in reference else base
    parent = urlsplit(base)
    need(parent.netloc, 'SCREENSHOT_URL')
    path = parts.path
    if not path.startswith('/'):
        path = parent.path.rpartition('/')[0] + '/' + path
    query = '?' + parts.query if '?' in reference else ''
    return parent.scheme + '://' + parent.netloc + path + query


class Assets(HTMLParser):
    def __init__(self, source, response_headers=()):
        super().__init__()
        self.source = source
        self.base = source
        self.base_seen = False
        self.base_may_be_blocked = any(row['name'] == 'content-security-policy' and
                                       BASE_URI.search(row['value']) for row in response_headers)
        self.inert = []
        self.rows = {}
        self.excluded = 0

    def handle_starttag(self, tag, attrs):
        if tag in ('template', 'noscript'):
            self.inert.append(tag)
            return
        if self.inert:
            return
        fields = {}
        for key, value in attrs:
            fields.setdefault(key, value)
        attrs = fields
        if tag == 'meta' and (attrs.get('http-equiv') or '').lower() == 'content-security-policy':
            self.base_may_be_blocked |= bool(BASE_URI.search(attrs.get('content') or ''))
        if tag == 'base' and 'href' in attrs and not self.base_seen:
            self.base_seen = True
            try:
                candidate = resolve_reference(self.source, attrs['href'] or '')
                parsed = urlsplit(candidate)
                if parsed.scheme not in ('data', 'javascript'):
                    if parsed.scheme == 'https' and parsed.netloc:
                        query = '?' + parsed.query if '?' in candidate else ''
                        candidate = 'https://' + parsed.netloc + browser_path(parsed.path) + query
                    self.base = candidate
            except ValueError:
                self.base = None
            return
        if tag == 'script':
            raw, kind = attrs.get('src'), 'script'
        elif tag == 'link' and 'stylesheet' in (attrs.get('rel') or '').lower().split():
            raw, kind = attrs.get('href'), 'style'
        else:
            return
        if not raw:
            return
        try:
            # Leave relative references unresolved when CSP may reject this base.
            if self.base is None or (self.base_may_be_blocked and self.base != self.source):
                need(urlsplit(raw.strip()).scheme, 'SCREENSHOT_ASSET_ORIGIN')
            candidate = url(resolve_reference(self.base, raw))
            need(origin(candidate) == origin(self.source), 'SCREENSHOT_ASSET_ORIGIN')
        except ValueError:
            self.excluded += 1
            return
        if candidate == self.source or candidate in self.rows:
            return
        need(len(self.rows) < 8, 'SCREENSHOT_ASSET_LIMIT')
        self.rows[candidate] = kind

    def handle_endtag(self, tag):
        if self.inert and self.inert[-1] == tag:
            self.inert.pop()


def response_headers(raw, kind):
    """Project the final HTTP 200 block onto the guest response contract."""
    need(len(raw) <= MAX_HEADERS and raw.endswith(b'\r\n\r\n') and b'\0' not in raw,
         'SCREENSHOT_HEADERS')
    lines = raw[:-4].rsplit(b'\r\n\r\n', 1)[-1].split(b'\r\n')
    need(re.fullmatch(rb'HTTP/(?:1\.[01]|2|3) 200(?: .*)?', lines[0]), 'SCREENSHOT_HEADERS')
    headers = []
    for line in lines[1:]:
        name, sep, value = line.partition(b':')
        need(sep and re.fullmatch(rb"[!#$%&'*+.^_\x60|~0-9A-Za-z-]+", name), 'SCREENSHOT_HEADERS')
        name = name.decode('ascii').lower()
        value = value.strip(b' \t')
        need(all(c == 9 or (c >= 32 and c != 127) for c in value), 'SCREENSHOT_HEADERS')
        headers.append((name, value.decode('latin1')))
    types = [value for name, value in headers if name == 'content-type']
    need(len(types) == 1 and types[0].split(';', 1)[0].strip().lower() in CONTENT_TYPES[kind], 'SCREENSHOT_MIME')
    for name, value in headers:
        if name == 'content-disposition':
            need(value.split(';', 1)[0].strip().lower() == 'inline', 'SCREENSHOT_ATTACHMENT')
        if name == 'content-encoding':
            need(all(part.strip().lower() == 'identity' for part in value.split(',')), 'SCREENSHOT_ENCODING')
    projected = [(name, value) for name, value in headers if name in REPLAY_HEADERS]
    need(all(value.isascii() for _, value in projected), 'SCREENSHOT_HEADERS')
    return [dict(name=name, value=value) for name, value in projected]


def read_headers(path):
    with path.open('rb') as stream:
        return stream.read(MAX_HEADERS + 1)


def preflight():
    vm = load('public_screenshot_vm', HERE / 'screenshot_vm.py')
    runtime = vm.preflight()
    broker = load('public_screenshot_broker', BASE / 'public_fetch_broker.py')
    parser_vm = broker._runtime['vm_name'].casefold()
    need(parser_vm != runtime['vm_name'].casefold() and parser_vm.strip('{}') != runtime['vm_uuid'],
         'SCREENSHOT_VM_SCOPE')
    return vm, runtime, broker


class Backend:
    def __init__(self, prepared, state_root):
        self.vm, self.runtime, self.broker = prepared
        self.started = time.perf_counter()
        self.run_id = 'shot-' + uuid.uuid4().hex
        self.evidence = Path(state_root) / 'screenshots' / self.run_id
        self.evidence.mkdir(parents=True, exist_ok=False)
        self.work = Path(tempfile.mkdtemp(prefix='capture-', dir=self.runtime['work_dir']))
        self.record = dict(run_id=self.run_id, phase='PREFLIGHT', network_attempts=0)
        self.binding = load('public_screenshot_binding', BASE / 'search_extract_v01/scan_binding.py')

    def gate(self, *args):
        env = {key: value for key, value in os.environ.items()
               if key.upper() in {'SYSTEMROOT', 'WINDIR', 'PATH', 'TEMP', 'TMP'}}
        result = subprocess.run([sys.executable, '-I', '-S', '-B', str(GATE), *map(str, args)],
                                env=env, capture_output=True, timeout=180)
        need(result.returncode == 0, 'SCREENSHOT_SCAN_GATE')

    def latest(self, tag, event):
        ledger = GATE.with_name('_dl_manifest.jsonl')
        rows = (json.loads(line) for line in ledger.read_text(encoding='utf-8').splitlines() if line.strip())
        matches = [row for row in rows if row.get('tag') == tag and row.get('event') == event]
        need(len(matches) == 1, 'SCREENSHOT_SCAN_GATE')
        return matches[0]

    def declare(self, target, part):
        tag = self.run_id + '-' + part
        source = 'operator-confirmed-public-https' if part == 'incoming' else 'local-staging'
        self.gate('declare', '--kind', 'other', '--target', 'Bounded public screenshot capsule',
                  '--source', source, '--size-mb', 12, '--scratch', target, '--tag', tag,
                  '--executes-code', '--pi-exception', '--reason',
                  'Operator configured screenshot support. Scanned page code runs only in a separate no-NIC VM.')
        declaration = self.evidence / (part + '-declaration.jsonl')
        declaration.write_text(json.dumps(self.latest(tag, 'declare')) + '\n', encoding='utf-8')
        self.gate('check', '--manifest', declaration)

    def scan(self, target, part):
        tag = self.run_id + '-' + part
        self.gate('scan', '--path', target, '--tag', tag, '--max-bytes', 12 * MAX_BODY, '--max-files', 40)
        receipt = self.latest(tag, 'scan')
        self.binding.verify(target, receipt)
        return receipt

    def acquire(self, source):
        self.record['phase'] = 'ACQUIRE'
        incoming = self.work / 'incoming'
        self.declare(incoming, 'incoming')
        incoming.mkdir()
        client = self.broker.Broker(incoming / 'raw')
        maps = {}
        spent = 0

        def fetch(target, kind):
            nonlocal spent
            need(spent < MAX_TOTAL, 'SCREENSHOT_ASSET_LIMIT')
            self.broker.MAX_BODY = min(MAX_BODY, MAX_TOTAL - spent)
            self.broker.URLS = frozenset([target])
            key = 'r' + str(len(maps))
            try:
                result = client.handle(json.dumps(dict(id=key, url=target)))
            finally:
                self.record['network_attempts'] = client.calls
            need(result.get('outcome') == 'FETCHED', 'SCREENSHOT_ACQUISITION')
            raw = incoming / 'raw' / key
            header_path = raw / 'headers.txt'
            header_data = read_headers(header_path)
            projected = response_headers(header_data, kind)
            body = raw / 'body.bin'
            size = body.stat().st_size
            need(0 < size <= self.broker.MAX_BODY, 'SCREENSHOT_ASSET_LIMIT')
            spent += size
            maps[target] = dict(body=body, header_path=header_path, sha256=sha(body), kind=kind,
                                headers_sha256=hashlib.sha256(header_data).hexdigest(), response_headers=projected)

        fetch(source, 'html')
        main = maps[source]
        assets = Assets(source, main['response_headers'])
        assets.feed(main['body'].read_text(encoding='utf-8'))
        assets.close()
        for target, kind in assets.rows.items():
            fetch(target, kind)
        self.record['excluded_assets'] = assets.excluded
        self.record['acquired_at'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        return incoming, maps, self.scan(incoming, 'incoming')

    def stage(self, source, incoming, maps, receipt):
        self.record['phase'] = 'STAGE'
        bound = self.binding.verify(incoming, receipt)['file_hashes']
        cap = self.work / 'capsule'
        self.declare(cap, 'capsule')
        cap.mkdir()
        rows = {}
        for index, (target, item) in enumerate(maps.items()):
            original = item['body']
            data = original.read_bytes()
            relative = original.relative_to(incoming).as_posix()
            need(hashlib.sha256(data).hexdigest() == item['sha256'] == bound.get(relative), 'SCREENSHOT_INPUT_CHANGED')
            header_path = item['header_path']
            header_data = read_headers(header_path)
            relative_headers = header_path.relative_to(incoming).as_posix()
            need(hashlib.sha256(header_data).hexdigest() == item['headers_sha256'] == bound.get(relative_headers),
                 'SCREENSHOT_INPUT_CHANGED')
            name = f'body{index}.bin'
            (cap / name).write_bytes(data)
            rows[target] = dict(body=name, sha256=item['sha256'],
                                response_headers=response_headers(header_data, item['kind']))
        entry_points = {'screenshot_guest.mjs': 'screenshot_guest.mjs', 'screenshot_guest.sh': 'screenshot_warm_task.sh'}
        for name, origin_file in entry_points.items():
            (cap / name).write_bytes((HERE / origin_file).read_bytes().replace(b'\r\n', b'\n'))
        job = dict(run_id=self.run_id, url=source, maps=rows)
        (cap / 'job.json').write_text(json.dumps(job), encoding='utf-8')
        checks = [f'{sha(path)}  {path.name}\n' for path in sorted(cap.iterdir())]
        (cap / 'checksums.txt').write_bytes(''.join(checks).encode('ascii'))
        return cap, self.scan(cap, 'capsule'), job

    def capture(self, source):
        source = url(source)
        try:
            incoming, maps, scan = self.acquire(source)
            cap, receipt, job = self.stage(source, incoming, maps, scan)
            self.record['phase'] = 'RENDER'
            result = self.vm.render(self, cap, receipt, job, self.runtime)
            result['resource_complete'] = not (self.record['excluded_assets'] or
                                                result['blocked_count'] or result['script_errors'])
            result.update(source=dict(url=source, acquired_at=self.record['acquired_at']),
                          external_content_is_untrusted=True, excluded_assets=self.record['excluded_assets'],
                          new_search_calls=0, total_seconds=round(time.perf_counter() - self.started, 3))
            self.record['phase'] = 'SCREENSHOT_COMPLETE'
            return result
        finally:
            (self.evidence / 'receipt.json').write_text(json.dumps(self.record, indent=2), encoding='utf-8')
