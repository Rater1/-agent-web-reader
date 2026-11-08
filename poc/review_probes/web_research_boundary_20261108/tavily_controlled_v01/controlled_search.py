"""Trusted-host fixed-job Tavily bridge. No live CLI or environment-key reader.

The untrusted side supplies an ID, never URL/query/headers/paths. Existing host
code supplies a credential callback. This is not a sandbox for the host process.
"""
import hashlib
import importlib.util
import ipaddress
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import threading

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
BASE_HASH = '304b67b398ce1e2f9f38d22fcd2eb6989339446cd5ec15d55f44fb2f01f075ed'
CONTRACT_HASH = '1b20c538be775b73746659eb98007df8eb06bb531318c5468e0d427bceeedcff'
ENDPOINT = 'https://api.tavily.com/search'
HOST = 'api.tavily.com'
JOB_ID = 'bs4_docs'
QUERY = 'Beautiful Soup get_text official documentation'
SAFE_REASONS = frozenset({
    'DEPENDENCY_HASH', 'REQUEST_SIZE', 'DUPLICATE_FIELD', 'REQUEST_JSON',
    'REQUEST_SCOPE', 'ATTEMPT_ALREADY_USED', 'CURL_BINARY_CHANGED',
    'DNS_NONPUBLIC_OR_EMPTY', 'NO_PUBLIC_IPV4', 'KEY_INVALID', 'TRANSPORT_SCHEMA',
    'TRANSPORT_FAILED', 'REDIRECT_OR_HTTP_ERROR', 'RESPONSE_LIMIT_OR_TYPE',
    'REFLECTED_CREDENTIAL', 'NO_APPROVED_INPUT', 'INPUT_BYTES_CHANGED',
})


class Refused(ValueError):
    pass


def require(ok, reason):
    if not ok:
        raise Refused(reason)


def load_bound(name, file, expected):
    require(hashlib.sha256(file.read_bytes()).hexdigest() == expected, 'DEPENDENCY_HASH')
    spec = importlib.util.spec_from_file_location(name, file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def parameters():
    return dict(query=QUERY, topic='general', search_depth='basic', max_results=5,
                chunks_per_source=1, auto_parameters=False, include_answer=False,
                include_raw_content=False, include_images=False, include_usage=True)


def parse_job(line):
    require(isinstance(line, str) and len(line.encode('utf-8')) <= 128, 'REQUEST_SIZE')
    def unique(pairs):
        obj = {}
        for k, v in pairs:
            require(k not in obj, 'DUPLICATE_FIELD')
            obj[k] = v
        return obj
    try:
        job = json.loads(line, object_pairs_hook=unique)
    except (ValueError, TypeError, RecursionError):
        raise Refused('REQUEST_JSON') from None
    require(type(job) is dict and job == {'id': JOB_ID}, 'REQUEST_SCOPE')
    return job


class FixedSearchBroker:
    def __init__(self, credential, *, resolver=None, transport=None, fingerprint=None):
        self.base = load_bound('tavily_bound_base', ROOT / 'public_fetch_broker.py', BASE_HASH)
        self.contract = load_bound('tavily_bound_contract', ROOT / 'search_extract_v01/response_contract.py', CONTRACT_HASH)
        self.credential = credential
        self.resolver = resolver or self.base.resolve
        self.transport = transport or self._curl
        self.fingerprint = fingerprint or (lambda: hashlib.sha256(self.base.CURL.read_bytes()).hexdigest())
        self.attempts = 0
        self.lock = threading.Lock()

    def handle(self, line):
        with self.lock:
            try:
                parse_job(line)
                require(self.attempts == 0, 'ATTEMPT_ALREADY_USED')
                require(self.fingerprint() == self.base.CURL_SHA, 'CURL_BINARY_CHANGED')
                addresses = self.resolver(HOST)
                require(type(addresses) is list and 0 < len(addresses) <= 32 and
                        all(isinstance(ip, str) and self.base.public_ip(ip) for ip in addresses),
                        'DNS_NONPUBLIC_OR_EMPTY')
                ipv4 = sorted(ip for ip in addresses if ipaddress.ip_address(ip).version == 4)
                require(bool(ipv4), 'NO_PUBLIC_IPV4')
                key = self.credential()
                require(isinstance(key, str) and len(key) <= 512 and
                        re.fullmatch(r'tvly-[\x21-\x7e]+', key) is not None, 'KEY_INVALID')
                # A timeout or transport exception consumes the sole attempt as well.
                self.attempts = 1
                reply = self.transport(ipv4[0], key)
                require(type(reply) is dict and set(reply) == {'curl_exit', 'http_status', 'body', 'headers'}, 'TRANSPORT_SCHEMA')
                require(reply['curl_exit'] == 0, 'TRANSPORT_FAILED')
                require(type(reply['http_status']) is int and reply['http_status'] == 200,
                        'REDIRECT_OR_HTTP_ERROR')
                body, headers = reply['body'], reply['headers']
                require(type(body) is bytes and type(headers) is bytes and
                        len(body) <= self.contract.MAX_BODY and len(headers) <= self.contract.MAX_HEADERS,
                        'RESPONSE_LIMIT_OR_TYPE')
                require(key.encode('ascii') not in body + headers, 'REFLECTED_CREDENTIAL')
                self.contract.check_headers(headers, body, 'search')
                return dict(outcome='READY_FOR_SCAN', request_attempts=self.attempts,
                            body=body, headers=headers, body_sha256=hashlib.sha256(body).hexdigest(),
                            headers_sha256=hashlib.sha256(headers).hexdigest(),
                            source_url=ENDPOINT, pinned_ipv4=ipv4[0], validated_addresses=addresses,
                            result_urls_followed=0)
            except Refused as exc:
                # Only local fixed codes may leave the trusted host. A credential
                # callback/transport can also raise this class with sensitive text.
                reason = exc.args[0] if len(exc.args) == 1 and type(exc.args[0]) is str and exc.args[0] in SAFE_REASONS else 'LOCAL_OR_RESPONSE_FAILURE'
                return dict(outcome='REFUSED', reason=reason, request_attempts=self.attempts)
            except Exception:
                return dict(outcome='REFUSED', reason='LOCAL_OR_RESPONSE_FAILURE', request_attempts=self.attempts)

    def _curl(self, ip, key, *, runner=subprocess.run):
        # Credentials are carried by an anonymous stdin pipe, not argv or env.
        with tempfile.TemporaryDirectory(prefix='tavily_fixed_headers_') as scratch:
            header_file = Path(scratch) / 'response.headers'
            argv = [str(self.base.CURL), '-q', '--silent', '--show-error',
                    '--proxy', self.base.PROXY, '--noproxy', '',
                    '--connect-to', f'{HOST}:443:{ip}:443',
                    '--proto', '=https', '--proto-redir', '=https', '--max-redirs', '0',
                    '--retry', '0', '--connect-timeout', '6', '--max-time', '20',
                    '--max-filesize', '65536', '--request', 'POST',
                    '--header', '@-', '--data-binary', json.dumps(parameters(), separators=(',', ':')),
                    '--dump-header', str(header_file), '--write-out', '%{stderr}__STATUS__%{http_code}',
                    '--url', ENDPOINT]
            env = dict(SystemRoot=os.environ.get('SystemRoot', r'C:\Windows'),
                       WINDIR=r'C:\Windows', PATH=r'C:\Windows\System32', CURL_HOME=scratch)
            secret_input = ('Authorization: Bearer ' + key + '\nContent-Type: application/json\n'
                            'Accept: application/json\nAccept-Encoding: identity\nCookie:\n').encode('ascii')
            result = runner(argv, input=secret_input, env=env, shell=False,
                            capture_output=True, timeout=25)
            status = re.search(rb'__STATUS__(\d{3})\s*$', result.stderr)
            with header_file.open('rb') as stream:
                headers = stream.read(8193)
            return dict(curl_exit=result.returncode, http_status=int(status[1]) if status else 0,
                        body=result.stdout, headers=headers)


def synthetic_envelope(reply):
    """In-memory bridge used only for synthetic tests; no files or guest start."""
    require(reply.get('outcome') == 'READY_FOR_SCAN', 'NO_APPROVED_INPUT')
    body, headers = reply['body'], reply['headers']
    require(hashlib.sha256(body).hexdigest() == reply['body_sha256'] and
            hashlib.sha256(headers).hexdigest() == reply['headers_sha256'], 'INPUT_BYTES_CHANGED')
    spec = dict(id=JOB_ID, kind='search', body='search.body', body_sha256=reply['body_sha256'],
                headers='search.headers', headers_sha256=reply['headers_sha256'],
                source=dict(url=ENDPOINT, mode='synthetic', acquired_at=None, staged_at=None))
    return {'search.body': body, 'search.headers': headers}, spec
