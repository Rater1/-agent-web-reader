"""Bounded verified-byte input contract. No networking or executable response handling."""
import hashlib
import re
from pathlib import Path

MAX_BODY = 65536
MAX_HEADERS = 8192

class Rejected(ValueError):
    pass

def require(value, reason):
    if not value:
        raise Rejected(reason)

def sha(data):
    return hashlib.sha256(data).hexdigest()

def read_bound(root, name, expected, limit):
    require(isinstance(name,str) and re.fullmatch(r'[a-z0-9_.-]{1,80}',name) and name not in ('.','..'), 'INPUT_NAME')
    file = Path(root)/name
    require(not file.is_symlink() and file.is_file(), 'INPUT_NOT_REGULAR')
    with file.open('rb') as stream:
        data = stream.read(limit+1)
    require(len(data)<=limit, 'INPUT_SIZE')
    require(sha(data)==expected, 'INPUT_HASH_MISMATCH')
    return data

def check_headers(headers, body, kind):
    require(len(headers)<=MAX_HEADERS and len(body)<=MAX_BODY, 'RESPONSE_SIZE')
    require(b'\x00' not in headers and b'\r\n\r\n' in headers, 'HEADERS_MALFORMED')
    blocks = [x for x in headers.split(b'\r\n\r\n') if x]
    lines = blocks[-1].split(b'\r\n')
    require(bool(re.fullmatch(rb'HTTP/(?:1\.[01]|2|3) 200(?: .*)?',lines[0])), 'HTTP_STATUS')
    fields = {}
    for line in lines[1:]:
        require(b':' in line and not line.startswith((b' ',b'\t')), 'HEADER_SYNTAX')
        k,v = line.split(b':',1)
        k=k.lower(); v=v.strip().decode('ascii',errors='strict')
        require(bool(re.fullmatch(rb'[a-z0-9-]+',k)), 'HEADER_NAME')
        require(k not in fields or k not in [b'content-type',b'content-length',b'content-encoding'], 'HEADER_DUPLICATE')
        fields[k]=v
    expected={'html':'text/html','search':'application/json'}
    require(kind in expected, 'UNKNOWN_KIND')
    ctype=fields.get(b'content-type','').lower()
    require(ctype.split(';')[0].strip()==expected[kind], 'CONTENT_TYPE')
    if 'charset=' in ctype:
        require(ctype.split('charset=',1)[1].strip(' "') in ('utf-8','us-ascii'), 'CHARSET')
    require(fields.get(b'content-encoding','identity').lower()=='identity', 'CONTENT_ENCODING')
    if b'content-length' in fields:
        require(fields[b'content-length'].isdigit() and int(fields[b'content-length'])==len(body), 'CONTENT_LENGTH')
    return expected[kind]

def checked_input(root,spec):
    require(isinstance(spec,dict) and set(spec)=={'id','kind','body','body_sha256','headers','headers_sha256','source'}, 'SPEC_SCHEMA')
    require(isinstance(spec['source'],dict) and set(spec['source'])=={'url','mode','acquired_at','staged_at'}, 'SOURCE_SCHEMA')
    require(spec['source']['mode'] in ['synthetic','cached_public'], 'SOURCE_MODE')
    body=read_bound(root,spec['body'],spec['body_sha256'],MAX_BODY)
    headers=read_bound(root,spec['headers'],spec['headers_sha256'],MAX_HEADERS)
    mime=check_headers(headers,body,spec['kind'])
    # Return these exact verified bytes, not a path for a later unverified reopen.
    return body,mime

def process(root,spec,parser):
    body,mime=checked_input(root,spec)
    result=parser(spec['kind'],body)
    return {**result,'source':dict(spec['source']),'input_sha256':sha(body),'content_type':mime}
