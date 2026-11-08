"""Offline parser: intended for diskless Linux guest with no NIC; no fetching API."""
import ipaddress
import json
from pathlib import Path
import re
import socket
import sys
import urllib.parse

from response_contract import Rejected, require, process

def unique(pairs):
    data={}
    for k,v in pairs:
        require(k not in data,'JSON_DUPLICATE')
        data[k]=v
    return data

def candidate_url(value):
    if not isinstance(value,str) or len(value)>2048 or any(ord(c)<=32 for c in value) or '\\' in value:
        return False
    try:
        p=urllib.parse.urlsplit(value)
        if p.scheme!='https' or p.username is not None or p.port not in (None,443) or not p.hostname:
            return False
        host=p.hostname
        try:
            ipaddress.ip_address(host)
            return False
        except ValueError:
            pass
        return bool(re.fullmatch(r'[a-zA-Z0-9-]+(?:\.[a-zA-Z0-9-]+)+',host)) and not host.lower().endswith(('.local','.localhost','.internal','.invalid'))
    except ValueError:
        return False

def search(body):
    try:
        raw=json.loads(body.decode('utf-8'),object_pairs_hook=unique)
    except (ValueError,RecursionError,UnicodeError) as exc:
        raise Rejected('SEARCH_JSON') from exc
    require(isinstance(raw,dict) and isinstance(raw.get('results'),list),'SEARCH_SCHEMA')
    require(len(raw['results'])<=20,'SEARCH_RESULT_LIMIT')
    results=[]; omitted=0
    for item in raw['results']:
        require(isinstance(item,dict),'SEARCH_RESULT_SCHEMA')
        title,url,snippet=item.get('title'),item.get('url'),item.get('content','')
        require(isinstance(title,str) and isinstance(snippet,str) and len(title)<=256 and len(snippet)<=1024,'SEARCH_FIELD_LIMIT')
        if not candidate_url(url):
            omitted+=1; continue
        results.append({'title':title,'url':url,'snippet':snippet,'url_check':'syntax_only_not_resolved','fetched':False})
    return {'outcome':'SEARCH_RESULTS' if results else 'EMPTY_RESULTS','results':results,'omitted_unsafe_urls':omitted,'links_followed':0}

def html(body):
    from bs4 import BeautifulSoup
    try:
        source=body.decode('utf-8')
    except UnicodeError as exc:
        raise Rejected('HTML_ENCODING') from exc
    soup=BeautifulSoup(source,'html.parser')
    title=soup.title.get_text(' ',strip=True) if soup.title else ''
    for node in soup.find_all(['script','style','noscript','nav','header','footer','iframe','object','embed','svg','form','link','meta']):
        node.decompose()
    main=soup.find('article') or soup.find('main') or soup.body or soup
    text=main.get_text('\n',strip=True)
    require(len(title)<=256 and len(text)<=16384,'TEXT_OUTPUT_LIMIT')
    require(len(text.strip())>=20,'EMPTY_CONTENT')
    return {'outcome':'TEXT','title':title,'text':text,'extractor':'beautifulsoup-html.parser-simple-main-v1','resources_fetched':0}

def parse(kind,body):
    return {'search':search,'html':html}[kind](body)

def forbid_network(*args,**kwargs):
    raise RuntimeError('PARSER_NETWORK_FORBIDDEN')

def main():
    import resource
    resource.setrlimit(resource.RLIMIT_AS,(192*1024*1024,192*1024*1024))
    resource.setrlimit(resource.RLIMIT_CPU,(3,3))
    resource.setrlimit(resource.RLIMIT_FSIZE,(32768,32768))
    socket.getaddrinfo=forbid_network
    socket.create_connection=forbid_network
    socket.socket.connect=forbid_network
    root=Path(sys.argv[1]); job=json.loads(Path(sys.argv[2]).read_text())
    try:
        result=process(root,job,parse)
    except (Rejected,KeyError,TypeError,UnicodeError) as exc:
        result={'outcome':'REJECTED','reason':str(exc)}
    data=json.dumps(result,ensure_ascii=False).encode('utf-8')
    if len(data)>32768:
        data=b'{"outcome":"REJECTED","reason":"SERIALIZED_OUTPUT_LIMIT"}'
    sys.stdout.buffer.write(data+b'\n')

if __name__=='__main__':
    main()
