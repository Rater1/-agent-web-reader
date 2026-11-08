"""Recompute bounded tree bytes against an existing full Defender scan receipt."""
import hashlib
import os
from pathlib import Path

class BindingError(ValueError):
    pass

def snapshot(root):
    root=Path(root)
    if not root.is_dir() or root.is_symlink(): raise BindingError('ROOT_INVALID')
    structure=[]; content=[]; hashes={}; total=0
    for folder,dirs,names in os.walk(root,followlinks=False):
        for name in dirs+names:
            p=Path(folder)/name
            if p.is_symlink() or (hasattr(p,'is_junction') and p.is_junction()): raise BindingError('LINK_FORBIDDEN')
        for name in sorted(names):
            file=Path(folder)/name
            if not file.is_file(): raise BindingError('NONREGULAR_FILE')
            size=file.stat().st_size; total+=size
            if len(hashes)>=256 or total>64*1024*1024: raise BindingError('TREE_LIMIT')
            h=hashlib.sha256()
            with file.open('rb') as stream:
                count=0
                while chunk:=stream.read(65536):
                    count+=len(chunk)
                    if count>size: raise BindingError('CHANGED_DURING_READ')
                    h.update(chunk)
            if count!=size: raise BindingError('CHANGED_DURING_READ')
            rel=file.relative_to(root).as_posix(); hashes[rel]=h.hexdigest()
            structure.append(f'{rel}\t{size}'); content.append(f'{rel}\t{size}\t{h.hexdigest()}')
    return {'files':len(hashes),'total_bytes':total,'content_files':len(hashes),'content_bytes':total,
            'structural_sha256':hashlib.sha256('\n'.join(sorted(structure)).encode()).hexdigest(),
            'content_sha256':hashlib.sha256('\n'.join(sorted(content)).encode()).hexdigest(),'file_hashes':hashes}

def verify(root,receipt):
    if receipt.get('verdict')!='OK' or receipt.get('content_binding')!='full' or receipt.get('evidence')!='eventlog' or receipt.get('content_truncated'):
        raise BindingError('SCAN_NOT_FULL_OK')
    if Path(receipt.get('path','')).resolve()!=Path(root).resolve(): raise BindingError('SCAN_PATH_MISMATCH')
    state=snapshot(root)
    if not state['files']: raise BindingError('EMPTY_SCAN')
    for key in ['files','total_bytes','content_files','content_bytes','structural_sha256','content_sha256']:
        if state[key]!=receipt.get(key): raise BindingError('SCAN_BYTES_MISMATCH:'+key)
    return state

def guarded(root,receipt,action):
    state=verify(root,receipt)
    return action(state)
