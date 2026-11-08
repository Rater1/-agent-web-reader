"""Guest-only search projection / static HTML excerpt; external data never code."""
import json
from pathlib import Path
import socket
import sys

import parser_worker as base
from response_contract import Rejected, process, require
from reader_core import public_url
from section_worker import render


def parse(kind, body):
    if kind == 'search':
        raw = json.loads(body.decode('utf-8'), object_pairs_hook=base.unique)
        require(type(raw) is dict and type(raw.get('query')) is str and len(raw['query']) <= 300, 'SEARCH_QUERY')
        require(type(raw.get('results')) is list and len(raw['results']) <= 5, 'SEARCH_RESULTS')
        rows, omitted = [], 0
        for item in raw['results']:
            require(type(item) is dict and all(type(item.get(k)) is str for k in ('title', 'url', 'content')), 'SEARCH_FIELDS')
            try:
                public_url(item['url'])
            except ValueError:
                omitted += 1
                continue
            rows.append(dict(title=item['title'][:128], url=item['url'], snippet=item['content'][:240],
                             excerpt_truncated=len(item['title']) > 128 or len(item['content']) > 240))
        usage = raw.get('usage')
        credits = usage.get('credits') if type(usage) is dict else None
        require(credits is None or type(credits) is int and credits >= 0, 'USAGE')
        return dict(outcome='SEARCH_RESULTS' if rows else 'EMPTY_RESULTS', query=raw['query'], results=rows,
                    omitted_unsafe_urls=omitted, credits_reported=credits, links_followed=0)
    require(kind == 'html', 'HTML_KIND')
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(body.decode('utf-8'), 'html.parser')
    title = soup.title.get_text(' ', strip=True)[:160] if soup.title else ''
    for node in soup.find_all(['script', 'style', 'template', 'noscript', 'nav', 'header', 'footer',
                               'iframe', 'object', 'embed', 'svg', 'form', 'link', 'meta']):
        node.decompose()
    node = soup.find('article') or soup.find('main') or soup.body or soup
    text = render(node).strip()
    require(len(text) >= 20, 'EMPTY_CONTENT')
    return dict(outcome='TEXT', title=title, text=text[:4000], truncated=len(text) > 4000,
                scope=node.name or 'document', whole_document=False, text_format='markdown-as-plain-text', resources_fetched=0)


def blocked(*args, **kwargs):
    raise RuntimeError('PARSER_NETWORK_FORBIDDEN')


def main():
    import resource
    for name, limit in [(resource.RLIMIT_AS, 192*1024*1024), (resource.RLIMIT_CPU, 3), (resource.RLIMIT_FSIZE, 32768)]:
        resource.setrlimit(name, (limit, limit))
    socket.getaddrinfo = socket.create_connection = socket.socket.connect = blocked
    try:
        result = process(Path(sys.argv[1]), json.loads(Path(sys.argv[2]).read_text()), parse)
    except (Rejected, ValueError, KeyError, TypeError, UnicodeError, RecursionError):
        result = dict(outcome='REJECTED', reason='INPUT_OR_READER_CONTRACT')
    encoded = json.dumps(result, ensure_ascii=True).encode()
    if len(encoded) > 32768:
        encoded = b'{"outcome":"REJECTED","reason":"OUTPUT_LIMIT"}'
    sys.stdout.buffer.write(encoded + b'\n')


if __name__ == '__main__':
    main()
