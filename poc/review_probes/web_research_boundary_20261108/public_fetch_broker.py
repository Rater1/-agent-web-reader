"""Narrow technical probe: fixed public GETs, no guest NIC or general-purpose proxy.

Trusted host code. Not an OS sandbox for this Python process. No caller paths/headers.
"""
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import urllib.parse

_config_spec = __import__('importlib.util', fromlist=['']).spec_from_file_location(
    'public_reader_runtime', Path(__file__).resolve().parent/'runtime_config.py')
_config_module = __import__('importlib.util', fromlist=['']).module_from_spec(_config_spec)
_config_spec.loader.exec_module(_config_module)
_runtime = _config_module.load_config()
CURL = Path(_runtime['curl_path'])
CURL_SHA = _runtime['curl_sha256']
PROXY = _runtime['proxy_url']
PUBLIC = 'https://example.com/'
REDIRECT = 'https://httpbingo.org/redirect-to?url=https%3A%2F%2F127.0.0.1%2F'
URLS = frozenset([PUBLIC, REDIRECT])
MAX_BODY = 65536
V4_DENY = tuple(map(ipaddress.ip_network, ['0.0.0.0/8', '10.0.0.0/8', '100.64.0.0/10',
    '127.0.0.0/8', '169.254.0.0/16', '172.16.0.0/12', '192.0.0.0/24', '192.0.2.0/24',
    '192.88.99.0/24', '192.168.0.0/16', '198.18.0.0/15', '198.51.100.0/24',
    '203.0.113.0/24', '224.0.0.0/4', '240.0.0.0/4']))
V6_DENY = tuple(map(ipaddress.ip_network, ['2001::/23', '2002::/16', '3fff::/20']))

class Refused(ValueError):
    pass

def public_ip(value):
    try:
        ip = ipaddress.ip_address(value)
    except ValueError:
        return False
    if not ip.is_global or ip.is_multicast or ip.is_reserved or ip.is_unspecified or '%' in value:
        return False
    if ip.version == 4:
        return not any(ip in net for net in V4_DENY)
    return ip in ipaddress.ip_network('2000::/3') and not any(ip in net for net in V6_DENY) and ip.ipv4_mapped is None

def resolve(host):
    return sorted({x[4][0] for x in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)})

def validate(url, resolver=resolve):
    if not isinstance(url, str) or len(url) > 512 or any(ord(c) <= 32 or ord(c) >= 127 for c in url) or '\\' in url:
        raise Refused('URL_SYNTAX')
    try:
        p = urllib.parse.urlsplit(url)
        if p.scheme != 'https' or p.username is not None or p.password is not None or p.fragment or p.port not in (None, 443):
            raise Refused('URL_SCHEME_AUTH_PORT')
        host = p.hostname or ''
        if not re.fullmatch(r'[a-z0-9]+(?:[.-][a-z0-9]+)*', host):
            raise Refused('HOST_SYNTAX')
        if url not in URLS:
            raise Refused('URL_NOT_IN_PROBE')
        addresses = resolver(host)
    except (ValueError, OSError) as exc:
        if isinstance(exc, Refused):
            raise
        raise Refused('PARSE_OR_DNS_FAILURE') from exc
    if not addresses or len(addresses) > 32 or not all(public_ip(x) for x in addresses):
        raise Refused('DNS_NONPUBLIC_OR_EMPTY')
    v4 = [x for x in addresses if ipaddress.ip_address(x).version == 4]
    if not v4:
        raise Refused('NO_SUPPORTED_PUBLIC_IPV4')
    return host, sorted(v4)[0], addresses

def unique_pairs(pairs):
    obj = {}
    for k, v in pairs:
        if k in obj:
            raise Refused('DUPLICATE_KEY')
        obj[k] = v
    return obj

def parse_request(line):
    if not isinstance(line, str) or len(line.encode('utf-8')) > 1024:
        raise Refused('REQUEST_SIZE')
    try:
        obj = json.loads(line, object_pairs_hook=unique_pairs)
    except (ValueError, TypeError) as exc:
        raise Refused('INVALID_JSON') from exc
    if not isinstance(obj, dict) or set(obj) != {'id', 'url'} or not isinstance(obj['url'], str) or not isinstance(obj['id'], str) or not re.fullmatch(r'[a-z0-9_-]{1,32}', obj['id']):
        raise Refused('REQUEST_SCHEMA')
    return obj

def classify(code, status, size):
    if code != 0:
        return 'TRANSPORT_FAILED'
    if size > MAX_BODY:
        return 'BODY_LIMIT'
    if 300 <= status <= 399:
        return 'REDIRECT_REFUSED'
    if status != 200:
        return 'HTTP_REJECTED'
    return 'FETCHED'

class Broker:
    def __init__(self, output, *, resolver=resolve, execute=None):
        self.output = Path(output)
        self.resolver = resolver
        self.execute = execute or self._curl
        self.calls = 0
        self.seen_ids = set()
        self.seen_urls = set()

    def handle(self, line):
        try:
            req = parse_request(line)
            if req['id'] in self.seen_ids or req['url'] in self.seen_urls:
                raise Refused('DUPLICATE_REQUEST')
            host, ip, addresses = validate(req['url'], self.resolver)
            self.seen_ids.add(req['id'])
            self.seen_urls.add(req['url'])
            self.calls += 1
            result = self.execute(req, host, ip)
            return {'id': req['id'], 'url': req['url'], 'validated_addresses': addresses,
                    'pinned_ipv4': ip, **result}
        except Refused as exc:
            return {'outcome': 'REFUSED', 'reason': str(exc)}

    def _curl(self, req, host, ip):
        if hashlib.sha256(CURL.read_bytes()).hexdigest() != CURL_SHA:
            raise Refused('CURL_BINARY_CHANGED')
        dest = self.output / req['id']
        dest.mkdir(parents=True, exist_ok=False)
        body, headers = dest / 'body.bin', dest / 'headers.txt'
        argv = [str(CURL), '-q', '--silent', '--show-error', '--proxy', PROXY, '--noproxy', '',
                '--connect-to', f'{host}:443:{ip}:443', '--proto', '=https', '--proto-redir', '=https',
                '--max-redirs', '0', '--retry', '0', '--connect-timeout', '6', '--max-time', '12',
                '--max-filesize', str(MAX_BODY), '--request', 'GET', '--header', 'Accept-Encoding: identity',
                '--header', 'Cookie:', '--header', 'Authorization:', '--output', str(body),
                '--dump-header', str(headers), '--write-out', '%{http_code}', '--url', req['url']]
        env = {'SystemRoot': os.environ.get('SystemRoot', r'C:\Windows'), 'WINDIR': r'C:\Windows',
               'PATH': r'C:\Windows\System32', 'CURL_HOME': str(dest)}
        try:
            p = subprocess.run(argv, shell=False, env=env, capture_output=True, timeout=18)
            status = int(p.stdout.decode('ascii').strip()) if p.stdout.strip().isdigit() else 0
            size = body.stat().st_size if body.exists() else 0
            return {'outcome': classify(p.returncode, status, size), 'http_status': status,
                    'curl_exit': p.returncode, 'body_bytes': size,
                    'body_sha256': hashlib.sha256(body.read_bytes()).hexdigest() if body.exists() else None,
                    'redirects_followed': 0, 'argv': argv,
                    'stderr': p.stderr[:2048].decode('utf-8', errors='replace')}
        except subprocess.TimeoutExpired:
            return {'outcome': 'TRANSPORT_TIMEOUT', 'redirects_followed': 0}
