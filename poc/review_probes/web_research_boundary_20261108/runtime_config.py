"""Trusted operator configuration. No networking, credential access, or provisioning."""
import hashlib
import json
from pathlib import Path
import re
import sys
import urllib.parse

ROOT = Path(__file__).resolve().parents[3]


def require(ok):
    if not ok:
        raise ValueError('RUNTIME_CONFIG_INVALID')


def load_config():
    file = ROOT / 'runtime.local.json'
    if not file.is_file():
        raise ValueError('RUNTIME_NOT_CONFIGURED')
    if sys.platform != 'win32':
        raise ValueError('LIVE_WINDOWS_ONLY')
    try:
        require(not file.is_symlink() and file.stat().st_size <= 8192)
        def unique(pairs):
            result = {}
            for k, v in pairs:
                require(k not in result)
                result[k] = v
            return result
        data = json.loads(file.read_text(encoding='utf-8'), object_pairs_hook=unique)
        require(type(data) is dict and set(data) == {'work_dir', 'vbox_path', 'vm_name', 'baseline_vm',
                'curl_path', 'curl_sha256', 'proxy_url', 'operator_approved_live_runtime'})
        require(data['operator_approved_live_runtime'] is True)
        for key in ('work_dir', 'vbox_path', 'vm_name', 'curl_path', 'curl_sha256', 'proxy_url'):
            require(type(data[key]) is str and len(data[key]) <= 512 and
                    not any(ord(c) < 32 for c in data[key]) and "'" not in data[key])
        require(1 <= len(data['vm_name']) <= 128 and not data['vm_name'].startswith('-'))
        baseline = data['baseline_vm']
        require(baseline is None or (type(baseline) is str and 1 <= len(baseline) <= 128 and
                not baseline.startswith('-') and not any(ord(c) < 32 for c in baseline)))
        require(baseline != data['vm_name'])
        work = Path(data['work_dir'])
        require(work.is_absolute() and work.is_dir() and not work.is_symlink())
        work = work.resolve()
        require(work != Path(work.anchor) and not work.is_relative_to(ROOT) and not ROOT.is_relative_to(work))
        for key in ('vbox_path', 'curl_path'):
            p = Path(data[key])
            require(p.is_absolute() and p.is_file() and not p.is_symlink())
        require(re.fullmatch(r'[a-f0-9]{64}', data['curl_sha256']) is not None)
        require(hashlib.sha256(Path(data['curl_path']).read_bytes()).hexdigest() == data['curl_sha256'])
        if data['proxy_url']:
            url = urllib.parse.urlsplit(data['proxy_url'])
            require(url.scheme == 'http' and url.hostname == '127.0.0.1' and
                    url.username is None and url.password is None and url.path == '' and
                    not url.query and not url.fragment and url.port is not None and 1 <= url.port <= 65535)
        return data
    except Exception:
        raise ValueError('RUNTIME_CONFIG_INVALID') from None
