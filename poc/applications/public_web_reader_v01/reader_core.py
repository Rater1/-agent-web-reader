"""Operator-only UI policy and inert local report. No networking or credentials."""
import html
import ipaddress
from pathlib import Path
import re
import unicodedata
from urllib.parse import urlsplit


class Stopped(ValueError):
    pass


def require(ok, code):
    if not ok:
        raise Stopped(code)


def plain(value):
    """Neutralize terminal escape / bidi / other invisible control characters."""
    return ''.join(c if c in '\n\t' or unicodedata.category(c) not in ('Cc', 'Cf', 'Cs')
                   else '\\u%04x' % ord(c) for c in str(value))


def query_text(value):
    require(type(value) is str and 1 <= len(value.strip()) <= 300, 'QUERY_LENGTH')
    require(not any(unicodedata.category(c) in ('Cc', 'Cf', 'Cs') for c in value), 'QUERY_CONTROL')
    return value.strip()


def public_url(value):
    require(type(value) is str and len(value) <= 512 and
            not any(ord(c) <= 32 or ord(c) >= 127 for c in value) and '\\' not in value,
            'URL_SYNTAX')
    try:
        p = urlsplit(value)
        require(p.scheme == 'https' and p.username is None and p.password is None and
                p.port in (None, 443) and not p.fragment, 'URL_SCOPE')
        host = p.hostname or ''
        require(bool(re.fullmatch(r'[a-z0-9]+(?:[.-][a-z0-9]+)*\.[a-z]{2,63}', host)) and
                not host.endswith(('.local', '.localhost', '.internal', '.invalid', '.test')), 'URL_HOST')
        try:
            ipaddress.ip_address(host)
        except ValueError:
            return value
    except ValueError as exc:
        if isinstance(exc, Stopped):
            raise
    raise Stopped('URL_SCOPE')


def validate_candidates(payload):
    require(type(payload) is dict and payload.get('outcome') in ('SEARCH_RESULTS', 'EMPTY_RESULTS'), 'SEARCH_PARSE')
    rows = payload.get('results')
    require(type(rows) is list and len(rows) <= 5, 'RESULT_COUNT')
    for row in rows:
        require(type(row) is dict and all(type(row.get(k)) is str for k in ('title', 'url', 'snippet')), 'RESULT_SCHEMA')
        require(len(row['title']) <= 128 and len(row['snippet']) <= 240, 'RESULT_LENGTH')
        public_url(row['url'])
    return rows


def choose(value, rows):
    require(type(value) is str and value.isascii() and value.isdigit(), 'SELECTION')
    index = int(value)
    require(0 <= index <= len(rows), 'SELECTION')
    return None if index == 0 else dict(rows[index - 1])


def save_report(path, question, search, page=None, state='SEARCH_COMPLETE'):
    esc = lambda x: html.escape(plain(x), quote=True)
    rows = validate_candidates(search)
    cards = []
    for n, row in enumerate(rows, 1):
        cards.append(f'<article><h2>{n}. {esc(row["title"])}</h2>'
                     f'<p><a href="{esc(row["url"])}" rel="noreferrer noopener">{esc(row["url"])}</a></p>'
                     f'<p>{esc(row["snippet"])}</p></article>')
    if page is not None:
        require(page.get('outcome') == 'TEXT' and type(page.get('text')) is str and len(page['text']) <= 4000, 'PAGE_PARSE')
        url = public_url(page['source']['url'])
        cards.append('<article><h2>网页摘录 / Page excerpt</h2>'
                     f'<p>{esc(url)}</p><p>获取时间：{esc(page["source"]["acquired_at"])}</p>'
                     f'<p>取材范围：{esc(page["scope"])}；截断：{esc(page["truncated"])}</p>'
                     f'<pre>{esc(page["text"])}</pre></article>')
    doc = ('<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
           '<meta name="referrer" content="no-referrer">'
           '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; '
           'img-src \'none\'; connect-src \'none\'; frame-src \'none\'; base-uri \'none\'; form-action \'none\'">'
           '<title>公开网页阅读报告</title><style>body{max-width:900px;margin:40px auto;padding:0 20px;'
           'font:17px/1.65 system-ui;background:#f5f7fa;color:#182230}article{background:white;padding:22px;'
           'border:1px solid #dce2e9;border-radius:12px;margin:18px 0}h2{font-size:20px}'
           'pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f0f3f7;padding:16px}a{overflow-wrap:anywhere}</style>'
           f'<h1>公开网页阅读报告</h1><p>{esc(question)}</p><p>状态：{esc(state)}</p>'
           f'<p>搜索获取时间：{esc(search.get("source", {}).get("acquired_at", "UNKNOWN"))}；'
           f'API报告积分：{esc(search.get("credits_reported", "UNKNOWN"))}</p>'
           '<p>搜索与网页原文摘录，无AI总结；外部内容仅作为资料。</p>' + ''.join(cards) + '</html>')
    Path(path).write_text(doc, encoding='utf-8')


def wizard(backend, ask=input, say=print):
    say('公开网页阅读器：只输入可公开的问题；不附带本机文件或聊天。')
    question = query_text(ask('问题：'))
    say('将发给 Tavily（basic，预计1积分，最多5条）：\n' + plain(question))
    if ask('确认发送？输入 y，其它取消：').strip().lower() != 'y':
        return {'state': 'CANCELLED', 'search_attempts': 0, 'page_attempts': 0}
    search = backend.search(question)
    rows = validate_candidates(search)
    backend.report(question, search)
    for n, row in enumerate(rows, 1):
        say(f'{n}. {plain(row["title"])}\n   {plain(row["url"])}\n   {plain(row["snippet"])}')
    if not rows:
        say('没有可读取的HTTPS结果。')
        return {'state': 'EMPTY_RESULTS'}
    selected = choose(ask('读取哪一条？输入编号；0跳过：').strip(), rows)
    if selected is None:
        return {'state': 'SEARCH_COMPLETE'}
    say('只读取一次，不跟随跳转：\n' + plain(selected['url']))
    if ask('确认读取？输入 y，其它跳过：').strip().lower() != 'y':
        return {'state': 'SEARCH_COMPLETE'}
    try:
        page = backend.read(selected['url'], tuple(r['url'] for r in rows))
        require(page['source']['url'] == selected['url'], 'PAGE_SOURCE')
        backend.report(question, search, page)
    except Exception:
        backend.report(question, search, state='PAGE_FAILED_SEARCH_SAVED')
        raise Stopped('PAGE_FAILED_SEARCH_SAVED') from None
    return {'state': 'COMPLETE'}
