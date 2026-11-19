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
from urllib.parse import parse_qsl, urljoin, urlsplit
import uuid

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
BASE = ROOT / 'poc/review_probes/web_research_boundary_20261108'
GATE = ROOT / 'poc/download_gate/dl_gate.py'
MAX_BODY = 1024**2
MAX_TOTAL = 8 * MAX_BODY
SECRET_KEYS = {'access_token', 'api_key', 'key', 'token', 'password', 'secret'}


def need(condition, reason):
    if not condition:
        raise ValueError(reason)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


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
        return value
    need(address.is_global and not address.is_multicast and not address.is_reserved, 'SCREENSHOT_URL')
    return value


def origin(value):
    parts = urlsplit(value)
    return parts.scheme, parts.hostname, parts.port or 443


class Assets(HTMLParser):
    def __init__(self, source):
        super().__init__()
        self.source = source
        self.rows = {}
        self.excluded = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'script':
            raw, kind = attrs.get('src'), 'script'
        elif tag == 'link' and 'stylesheet' in (attrs.get('rel') or '').lower().split():
            raw, kind = attrs.get('href'), 'style'
        else:
            return
        if not raw:
            return
        try:
            candidate = url(urljoin(self.source, raw).split('#', 1)[0])
            need(origin(candidate) == origin(self.source), 'SCREENSHOT_ASSET_ORIGIN')
        except ValueError:
            self.excluded += 1
            return
        if candidate == self.source or candidate in self.rows:
            return
        need(len(self.rows) < 8, 'SCREENSHOT_ASSET_LIMIT')
        self.rows[candidate] = kind


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
        accepted = {
            'html': {'text/html'},
            'script': {'application/javascript', 'text/javascript', 'application/x-javascript'},
            'style': {'text/css'},
        }
        maps = {}
        spent = 0

        def fetch(target, kind):
            nonlocal spent
            need(spent < MAX_TOTAL, 'SCREENSHOT_ASSET_LIMIT')
            self.broker.MAX_BODY = min(MAX_BODY, MAX_TOTAL - spent)
            self.broker.URLS = frozenset([url(target)])
            key = 'r' + str(len(maps))
            try:
                result = client.handle(json.dumps(dict(id=key, url=target)))
            finally:
                self.record['network_attempts'] = client.calls
            need(result.get('outcome') == 'FETCHED', 'SCREENSHOT_ACQUISITION')
            raw = incoming / 'raw' / key
            headers = (raw / 'headers.txt').read_text(encoding='latin1')
            types = re.findall(r'(?im)^Content-Type:\s*([^\r\n]+)', headers)
            need(types and types[-1].split(';', 1)[0].strip().lower() in accepted[kind], 'SCREENSHOT_MIME')
            body = raw / 'body.bin'
            size = body.stat().st_size
            need(0 < size <= self.broker.MAX_BODY, 'SCREENSHOT_ASSET_LIMIT')
            spent += size
            maps[target] = dict(body=str(body), bytes=size, sha256=sha(body), content_type=types[-1], kind=kind)

        fetch(source, 'html')
        assets = Assets(source)
        assets.feed(Path(maps[source]['body']).read_text(encoding='utf-8'))
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
            original = Path(item['body'])
            data = original.read_bytes()
            relative = original.relative_to(incoming).as_posix()
            need(hashlib.sha256(data).hexdigest() == item['sha256'] == bound.get(relative), 'SCREENSHOT_INPUT_CHANGED')
            name = f'body{index}.bin'
            (cap / name).write_bytes(data)
            rows[target] = {**item, 'body': name}
        entry_points = {'screenshot_guest.mjs': 'screenshot_guest.mjs', 'screenshot_guest.sh': 'screenshot_warm_task.sh'}
        for name, origin_file in entry_points.items():
            (cap / name).write_bytes((HERE / origin_file).read_bytes().replace(b'\r\n', b'\n'))
        job = dict(run_id=self.run_id, url=source, maps=rows)
        (cap / 'job.json').write_text(json.dumps(job), encoding='utf-8')
        checks = [f'{sha(path)}  {path.name}\n' for path in sorted(cap.iterdir())]
        (cap / 'checksums.txt').write_bytes(''.join(checks).encode('ascii'))
        return cap, self.scan(cap, 'capsule'), job

    def capture(self, source):
        url(source)
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
