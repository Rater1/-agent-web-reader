"""Portable fixture-only interface demonstration; no VM receipt is simulated."""
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent


def call(data):
    child = subprocess.run([sys.executable, '-I', '-S', '-B', str(ROOT/'agent_web.py'), '--fixture'],
                           input=json.dumps(data).encode(), capture_output=True, timeout=90)
    result = json.loads(child.stdout)
    if child.returncode or result.get('mode') != 'fixture':
        raise SystemExit(json.dumps(result))
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return result


result = call(dict(op='search', query='Python URL parsing documentation'))
call(dict(op='read', search_id=result['search_id'], rank=1))
