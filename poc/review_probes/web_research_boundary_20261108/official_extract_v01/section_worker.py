"""Offline fixed get-text section extractor; no whole-document fallback."""
import json
import re
from pathlib import Path
import socket
import sys
from response_contract import Rejected, require, process

def render(node):
    from bs4 import NavigableString, Comment
    if isinstance(node,Comment):return ''
    if isinstance(node,NavigableString):return re.sub(r'\s+',' ',str(node))
    if node.name=='pre':
        code=node.get_text('',strip=False)
        longest=max((len(x) for x in re.findall(r'`+',code)),default=0)
        fence='`'*max(3,longest+1)
        return '\n\n'+fence+'\n'+code.rstrip('\r\n')+'\n'+fence+'\n\n'
    if node.name=='br':return '\n'
    text=''.join(render(child) for child in node.children)
    if node.name in ('section','div','p','li','ul','ol','blockquote','h1','h2','h3','h4','h5','h6'):
        return '\n\n'+text.strip()+'\n\n'
    return text

def section(kind, body):
    from bs4 import BeautifulSoup
    require(kind == 'html', 'HTML_ONLY')
    soup=BeautifulSoup(body.decode('utf-8'), 'html.parser')
    title=soup.title.get_text(' ',strip=True) if soup.title else ''
    nodes=soup.find_all(id='get-text')
    require(len(nodes)==1, 'SECTION_MISSING_OR_AMBIGUOUS')
    node=nodes[0]
    require(node.name in ('section','div') and node.find(['h1','h2','h3','h4','h5','h6']) is not None, 'SECTION_CONTAINER')
    for child in node.find_all(['script','style','template','noscript','iframe','object','embed','svg','form']):child.decompose()
    heading=node.find(['h1','h2','h3','h4','h5','h6']).get_text(' ',strip=True)
    require('get_text()' in heading, 'SECTION_HEADING')
    text=render(node).strip()
    require(len(title)<=256 and 20<=len(text)<=16384, 'TEXT_OUTPUT_LIMIT')
    return dict(outcome='TEXT',title=title,text=text,text_format='markdown',extractor='bs4-fixed-get-text-section-v2',
                scope='section:get-text',whole_document=False,resources_fetched=0)

def blocked(*args,**kwargs):raise RuntimeError('PARSER_NETWORK_FORBIDDEN')

def main():
    import resource
    for kind,limit in [(resource.RLIMIT_AS,192*1024*1024),(resource.RLIMIT_CPU,3),(resource.RLIMIT_FSIZE,32768)]:
        resource.setrlimit(kind,(limit,limit))
    socket.getaddrinfo=socket.create_connection=socket.socket.connect=blocked
    try:
        result=process(Path(sys.argv[1]),json.loads(Path(sys.argv[2]).read_text()),section)
    except (Rejected,KeyError,TypeError,ValueError,UnicodeError):
        result=dict(outcome='REJECTED',reason='INPUT_OR_FIXED_SECTION_CONTRACT')
    encoded=json.dumps(result,ensure_ascii=True).encode()
    if len(encoded)>32768:encoded=b'{"outcome":"REJECTED","reason":"OUTPUT_LIMIT"}'
    sys.stdout.buffer.write(encoded+b'\n')

if __name__=='__main__':main()
