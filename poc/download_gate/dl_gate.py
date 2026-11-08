"""Project-owned Defender acquisition gate, with public source labels only.
The legacy --pi-exception flag means explicit operator approval, not identity proof.
"""
import argparse
import datetime
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass
HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, '..', '..'))
LEDGER = os.path.join(HERE, '_dl_manifest.jsonl')
MANDIR = os.path.join(tempfile.gettempdir(), 'dl_gate_manifests')
ALLOW_SOURCES = {'local-staging': 'local://synthetic-and-verified-public-cache', 'api.tavily.com': 'https://api.tavily.com', 'operator-confirmed-public-https': 'operator://confirmed-https-public-dns-pinned', 'dl-cdn.alpinelinux.org': 'https://dl-cdn.alpinelinux.org'}
KINDS = {'pip', 'git', 'hf-model', 'hf-dataset', 'other'}

def now():
    return datetime.datetime.now().isoformat(timespec='seconds')

def norm(p):
    return os.path.normcase(os.path.abspath(p)).rstrip('\\/')

def inside_repo(p):
    try:
        return os.path.commonpath([os.path.abspath(p), REPO]) == REPO
    except Exception:
        return False

def load(path=None):
    path = path or LEDGER
    rows = []
    if os.path.exists(path):
        with open(path, encoding='utf-8') as f:
            for ln in f:
                ln = ln.strip()
                if ln:
                    rows.append(json.loads(ln))
    return rows

def append(rec):
    with open(LEDGER, 'a', encoding='utf-8', newline='\n') as f:
        f.write(json.dumps(rec, ensure_ascii=False) + '\n')

def reject(msg):
    print('GATE: REJECT - ' + msg)
    return 1

def cmd_declare(a):
    if a.kind not in KINDS:
        return reject('kind 不在闭集 %s' % sorted(KINDS))
    if a.source not in ALLOW_SOURCES:
        return reject('source 不在允许清单 %s' % sorted(ALLOW_SOURCES))
    reason = (getattr(a, 'reason', '') or '').strip()
    pi_ok = bool(getattr(a, 'pi_exception', False))
    if a.executes_code:
        if not (pi_ok and reason):
            return reject('executes_code=yes ⇒ 默认拒；如确需走 PI 例外，须 `--pi-exception --reason <文本>`')
    if not a.tag:
        return reject('tag 必填（scan 要靠它绑定 declare；空 tag 无法配对）')
    if not a.scratch:
        return reject('scratch 必填（scan 要核对 path==scratch）')
    if inside_repo(a.scratch):
        return reject('scratch 必须在**仓外**：%s 落在仓内 %s' % (a.scratch, REPO))
    try:
        size = int(a.size_mb)
    except Exception:
        return reject('size_mb 必须为整数（收到 %r）' % (a.size_mb,))
    rec = dict(event='declare', ts=now(), kind=a.kind, target=a.target, source=a.source, size_mb=size, executes_code=bool(a.executes_code), scratch=os.path.abspath(a.scratch), tag=a.tag, note='字段系**自报**，本门不校验真伪')
    if pi_ok and reason:
        rec['pi_exception'] = True
        rec['reason'] = reason
    append(rec)
    print('DECLARED ' + json.dumps(rec, ensure_ascii=False))
    return 0

def declares_for(tag):
    return [r for r in load() if r.get('event') == 'declare' and r.get('tag') == tag]

def evidence_from(started, done):
    """N5：取证级只由"扫起(1000)+扫完(1001)"决定。"""
    return 'eventlog' if started >= 1 and done >= 1 else 'weak'

def scan_verdict(evidence, threats):
    """v1.2：取证级 → (标签, rc)。**永不**把 unverified 说成 OK。"""
    if threats:
        return ('THREATS', 1)
    if evidence == 'eventlog':
        return ('OK', 0)
    return ('UNVERIFIED', 1)

def receipt_verdict(scan_label, content_binding):
    """M1：**两个不可混读的维度** 合成单一 verdict：
       ① Defender 扫描结论（scan_label）② 内容绑定（content_binding）。
       **content_binding≠full 时，verdict 绝不得呈现为 "OK"（全量）。**"""
    if scan_label == 'THREATS':
        return ('THREATS', 1)
    if scan_label != 'OK':
        return ('UNVERIFIED', 1)
    if content_binding == 'full':
        return ('OK', 0)
    return ('OK-CONTENT-PARTIAL', 0)

def validate_scan(tag, path, force=False, reason=''):
    """N1/N2 核心：scan 必须绑定到匹配的 declare，且 path==scratch。返回 (ok, msg)。"""
    if not tag:
        return (False, 'tag 为空 ⇒ 无法绑定 declare（顺序未强制 ⇒ 拒）')
    ds = declares_for(tag)
    if not ds:
        return (False, '无匹配 declare（tag=%s）⇒ 拒：不得先扫后声明' % tag)
    if not any((norm(path) == norm(r['scratch']) for r in ds)):
        return (False, 'scan.path(%s) ≠ 任何 declare.scratch ⇒ 拒' % path)
    done = [r for r in load() if r.get('event') == 'scan' and r.get('tag') == tag and (norm(r.get('path', '')) == norm(path))]
    if done and (not force):
        return (False, '重复扫描（tag+path 已有回执）；如需合法重扫加 --force --reason')
    if force and (not reason.strip()):
        return (False, '--force 必须带 --reason（留痕；否则拒）')
    return (True, 'ok')

def sha256_file(p, chunk=1 << 20):
    """M4：**分块**哈希（勿把 GB 级权重整读入内存）。"""
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        for blk in iter(lambda: f.read(chunk), b''):
            h.update(blk)
    return h.hexdigest()

def scratch_manifest(root, max_files=20000, max_bytes=1 << 30, tag='', sidecar=True):
    """N7 + M1/M2/M4：**结构摘要（全量·便宜·永不截断）** ＋ **内容摘要（有界·可截断）**。
    返回 dict：files/total_bytes/structural_sha256/structural_complete
               ＋ content_files/content_bytes/content_sha256/content_truncated/**content_binding**。
    """
    struct, clines = ([], [])
    n_files = total = 0
    c_files = c_bytes = 0
    truncated = False
    for dp, dn, fn in os.walk(root):
        for f in sorted(fn):
            p = os.path.join(dp, f)
            try:
                sz = os.path.getsize(p)
            except OSError:
                continue
            rel = os.path.relpath(p, root).replace('\\', '/')
            struct.append('%s\t%d' % (rel, sz))
            n_files += 1
            total += sz
            if truncated:
                continue
            if c_files >= max_files or c_bytes + sz > max_bytes:
                truncated = True
                continue
            try:
                h = sha256_file(p)
            except Exception:
                continue
            clines.append('%s\t%d\t%s' % (rel, sz, h))
            c_files += 1
            c_bytes += sz
    struct.sort()
    clines.sort()
    binding = 'full' if not truncated else 'none' if c_files == 0 else 'partial'
    out = dict(files=n_files, total_bytes=total, structural_sha256=hashlib.sha256('\n'.join(struct).encode('utf-8')).hexdigest(), structural_complete=True, content_files=c_files, content_bytes=c_bytes, content_sha256=hashlib.sha256('\n'.join(clines).encode('utf-8')).hexdigest(), content_truncated=truncated, content_binding=binding)
    if sidecar:
        os.makedirs(MANDIR, exist_ok=True)
        stamp = datetime.datetime.now().strftime('%Y%m%dT%H%M%S_%f')
        side = os.path.join(MANDIR, '%s_%s_%s.txt' % (os.path.basename(root), tag or 'notag', stamp))
        with open(side, 'w', encoding='utf-8', newline='\n') as f:
            f.write('# structural (complete)\n' + '\n'.join(struct) + '\n')
            f.write('# content (%s)\n' % binding + '\n'.join(clines) + '\n')
        out['manifest_file'] = side
    return out

def _run_ps(cmd):
    return subprocess.run(['powershell', '-NoProfile', '-Command', cmd], capture_output=True, text=True, encoding='utf-8', errors='replace')

def _parse_kv(text):
    kv = {}
    for ln in (text or '').splitlines():
        if '=' in ln:
            k, v = ln.split('=', 1)
            kv[k.strip()] = v.strip()
    return kv

def _defender_evidence(d, interval=1.0, tries=10):
    """N5：跑 custom scan，然后**轮询**事件日志（~1s 索引竞态）。
    返回 (ok, threats, evidence, err)。"""
    t0 = now()
    r0 = _run_ps("Start-MpScan -ScanPath '%s' -ScanType CustomScan" % d.replace("'", "''"))
    if r0.returncode != 0:
        return (False, 0, 'weak', ((r0.stdout or '') + (r0.stderr or '')).strip()[-300:])
    q = "$ev=@(Get-WinEvent -FilterHashtable @{LogName='Microsoft-Windows-Windows Defender/Operational'; Id=1000,1001,1116,1117; StartTime=[datetime]::Parse('%s')} -ErrorAction SilentlyContinue);Write-Output ('STARTED=' + @($ev|Where-Object{$_.Id -eq 1000}).Count);Write-Output ('DONE=' + @($ev|Where-Object{$_.Id -eq 1001}).Count);Write-Output ('DET=' + @($ev|Where-Object{$_.Id -in 1116,1117}).Count)" % t0
    started = done = det = 0
    for i in range(max(1, tries)):
        kv = _parse_kv(_run_ps(q).stdout)
        started = int(kv.get('STARTED', '0') or 0)
        done = int(kv.get('DONE', '0') or 0)
        det = int(kv.get('DET', '0') or 0)
        if evidence_from(started, done) == 'eventlog' or det:
            break
        if i < tries - 1 and interval > 0:
            time.sleep(interval)
    return (True, det, evidence_from(started, done), '')

def cmd_scan(a):
    d = os.path.abspath(a.path)
    if not os.path.isdir(d):
        return reject('扫描路径不存在：%s' % d)
    force = bool(getattr(a, 'force', False))
    reason = getattr(a, 'reason', '') or ''
    ok, msg = validate_scan(a.tag, d, force=force, reason=reason)
    if not ok:
        return reject(msg)
    mani = scratch_manifest(d, max_files=int(getattr(a, 'max_files', 20000)), max_bytes=int(getattr(a, 'max_bytes', 1 << 30)), tag=a.tag)
    good, threats, evidence, err = _defender_evidence(d, interval=float(getattr(a, 'interval', 1.0)), tries=int(getattr(a, 'tries', 10)))
    if not good:
        return reject('扫描未完成：%s' % err)
    scan_label, _ = scan_verdict(evidence, threats)
    verdict, rc = receipt_verdict(scan_label, mani['content_binding'])
    rec = dict(event='scan', ts=now(), path=d, tag=a.tag, threats=threats, evidence=evidence, scan_verdict=scan_label, content_binding=mani['content_binding'], verdict=verdict, forced=force, reason=reason, files=mani['files'], total_bytes=mani['total_bytes'], structural_sha256=mani['structural_sha256'], structural_complete=True, content_files=mani['content_files'], content_bytes=mani['content_bytes'], content_sha256=mani['content_sha256'], content_truncated=mani['content_truncated'], manifest_file=mani.get('manifest_file', ''))
    append(rec)
    if verdict == 'THREATS':
        print('SCANNED threats=%d evidence=%s' % (threats, evidence))
        return reject('Defender 报威胁 %d 条' % threats)
    if verdict == 'OK':
        print('SCANNED OK ' + json.dumps(rec, ensure_ascii=False))
        return 0
    if verdict == 'OK-CONTENT-PARTIAL':
        print('SCANNED OK (content binding PARTIAL) ' + json.dumps(rec, ensure_ascii=False))
        print('  ⚠ 结构摘要=全量（structural_sha256）｜内容摘要=**已截断** ⇒ **不得读作"字节全量绑定"**')
        return 0
    print('SCANNED UNVERIFIED ' + json.dumps(rec, ensure_ascii=False))
    print('  ⚠ evidence=weak：轮询仍未见 1000/1001 ⇒ threats=0 是**无结果**，非**干净**')
    if getattr(a, 'allow_weak', False):
        print('  ⚠ --allow-weak 显式放行（已留痕）')
        return 0
    return reject('证据不足 ⇒ 默认不放行（fail-closed）；如需放行加 --allow-weak 并留痕')

def cmd_check(a):
    rows = load(a.manifest)
    decl = [r for r in rows if r.get('event') == 'declare']
    if not decl:
        return reject('清单为空：**未声明不得获取**')
    legacy = [r for r in decl if not isinstance(r.get('size_mb'), int)]
    cur = [r for r in decl if isinstance(r.get('size_mb'), int)]
    bad = []
    for r in cur:
        why = []
        if r.get('source') not in ALLOW_SOURCES:
            why.append('source 非白名单')
        if r.get('executes_code') and (not (r.get('pi_exception') and r.get('reason'))):
            why.append('声明执行远端代码且无 PI 例外留痕')
        if not r.get('scratch'):
            why.append('缺 scratch')
        elif inside_repo(r['scratch']):
            why.append('scratch 在仓内')
        if not r.get('tag'):
            why.append('缺 tag')
        if why:
            bad.append((r, why))
    if bad:
        for r, why in bad:
            print('  BAD %s | %s' % (r.get('target'), '; '.join(why)))
        return reject('%d 条当前声明不合格' % len(bad))
    print('GATE: PASS - %d 条当前声明合规（白名单/不执行远端代码/scratch 仓外/tag·size 齐）' % len(cur))
    if legacy:
        print('  历史档 legacy %d 条（**不回填、不冒充通过**；pre-v1.1 无 int 约束）：' % len(legacy))
        for r in legacy:
            print('    · %s %s size_mb=%r tag=%s' % (r.get('kind'), r.get('target'), r.get('size_mb'), r.get('tag')))
    return 0

def cmd_status(a):
    rows = load()
    d = [r for r in rows if r.get('event') == 'declare']
    s = [r for r in rows if r.get('event') == 'scan']
    print('declared=%d scanned=%d' % (len(d), len(s)))
    for r in d:
        hit = any((x.get('tag') == r.get('tag') and norm(x.get('path', '')) == norm(r.get('scratch', '')) for x in s))
        print('  [%s] %s %s (%s) size=%sMB scratch=%s tag=%s' % ('scanned' if hit else 'PENDING', r.get('kind'), r.get('target'), r.get('source'), r.get('size_mb'), r.get('scratch'), r.get('tag')))
    for x in s:
        if not declares_for(x.get('tag')):
            print('  [ORPHAN-SCAN] tag=%s path=%s（无匹配 declare）' % (x.get('tag'), x.get('path')))
    return 0

def selftest():
    import tempfile
    global LEDGER, MANDIR
    LEDGER = os.path.join(tempfile.mkdtemp(prefix='dlgate_'), 'm.jsonl')
    MANDIR = tempfile.mkdtemp(prefix='dlmani_selftest_')
    ok = 0
    TOTAL = 28

    class A:
        pass

    def mk(**kw):
        a = A()
        for k, v in dict(kind='pip', target='X', source='pypi:tuna', size_mb=1, executes_code=False, scratch='', tag='t1', reason='', pi_exception=False).items():
            setattr(a, k, v)
        for k, v in kw.items():
            setattr(a, k, v)
        return a
    tmp = os.path.dirname(LEDGER)
    tmp2 = tempfile.mkdtemp(prefix='dlgate2_')
    assert cmd_declare(mk(scratch=tmp)) == 0
    ok += 1
    assert cmd_declare(mk(source='evil.example.com', scratch=tmp, tag='t2')) != 0
    ok += 1
    assert cmd_declare(mk(executes_code=True, scratch=tmp, tag='t3')) != 0
    ok += 1
    assert cmd_declare(mk(scratch=HERE, tag='t4')) != 0
    ok += 1
    b = A()
    b.manifest = LEDGER
    assert cmd_check(b) == 0
    ok += 1
    assert validate_scan('nope', tmp)[0] is False
    ok += 1
    assert validate_scan('t1', tmp2)[0] is False
    ok += 1
    assert validate_scan('t1', tmp)[0] is True
    ok += 1
    assert cmd_declare(mk(size_mb='abc', scratch=tmp, tag='t9')) != 0
    ok += 1
    assert load() == load(LEDGER)
    ok += 1
    assert validate_scan('t1', tmp, force=True, reason='')[0] is False
    ok += 1
    assert validate_scan('t1', tmp, force=True, reason='more files')[0] is True
    ok += 1
    assert scan_verdict('eventlog', 0) == ('OK', 0)
    ok += 1
    assert scan_verdict('weak', 0) == ('UNVERIFIED', 1)
    ok += 1
    assert scan_verdict('eventlog', 2) == ('THREATS', 1)
    ok += 1
    assert evidence_from(0, 0) == 'weak'
    ok += 1
    assert evidence_from(1, 1) == 'eventlog'
    ok += 1
    append_legacy = dict(event='declare', ts=now(), kind='pip', target='legacy', source='pypi:tuna', size_mb='60', executes_code=False, scratch=tmp, tag='tleg', note='')
    with open(LEDGER, 'a', encoding='utf-8', newline='\n') as f:
        f.write(json.dumps(append_legacy, ensure_ascii=False) + '\n')
    assert cmd_check(b) == 0
    ok += 1
    assert cmd_declare(mk(executes_code=True, pi_exception=True, reason='approved', scratch=tmp2, tag='t16')) == 0
    ok += 1
    assert cmd_declare(mk(executes_code=True, pi_exception=True, reason='', scratch=tmp2, tag='t17')) != 0
    ok += 1
    md = tempfile.mkdtemp(prefix='dlmani_')
    open(os.path.join(md, 'a.txt'), 'w').write('hello')
    open(os.path.join(md, 'b.txt'), 'w').write('world')
    m1 = scratch_manifest(md)
    assert m1['files'] == 2 and len(m1['content_sha256']) == 64
    ok += 1
    assert scratch_manifest(md)['content_sha256'] == m1['content_sha256']
    ok += 1
    md2 = tempfile.mkdtemp(prefix='dlbig_')
    for i in range(5):
        open(os.path.join(md2, 'f%d.txt' % i), 'w').write('x' * 10)
    m2 = scratch_manifest(md2, max_files=3)
    assert m2['files'] == 5 and m2['structural_complete'] is True
    ok += 1
    assert m2['content_binding'] == 'partial' and m2['content_truncated'] is True
    ok += 1
    da = tempfile.mkdtemp(prefix='dloa_')
    db = tempfile.mkdtemp(prefix='dlob_')
    for nm in ('b.txt', 'a.txt'):
        open(os.path.join(da, nm), 'w').write('y')
    for nm in ('a.txt', 'b.txt'):
        open(os.path.join(db, nm), 'w').write('y')
    assert scratch_manifest(da)['structural_sha256'] == scratch_manifest(db)['structural_sha256']
    ok += 1
    assert receipt_verdict('OK', 'full') == ('OK', 0)
    ok += 1
    assert receipt_verdict('OK', 'partial')[0] != 'OK'
    ok += 1
    assert receipt_verdict('OK', 'none')[0] != 'OK'
    ok += 1
    assert ok == TOTAL, 'selftest 计数不符：实跑 %d ≠ 标称 %d' % (ok, TOTAL)
    print('SELFTEST: PASS %d/%d' % (ok, TOTAL))
    os.remove(LEDGER)
    return 0

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--selftest', action='store_true')
    sub = ap.add_subparsers(dest='cmd')
    d = sub.add_parser('declare')
    d.add_argument('--kind', required=True)
    d.add_argument('--target', required=True)
    d.add_argument('--source', required=True)
    d.add_argument('--size-mb', dest='size_mb', required=True)
    d.add_argument('--executes-code', dest='executes_code', action='store_true')
    d.add_argument('--pi-exception', dest='pi_exception', action='store_true')
    d.add_argument('--reason', default='')
    d.add_argument('--scratch', required=True)
    d.add_argument('--tag', required=True)
    c = sub.add_parser('check')
    c.add_argument('--manifest', default=None)
    s = sub.add_parser('scan')
    s.add_argument('--path', required=True)
    s.add_argument('--tag', required=True)
    s.add_argument('--force', action='store_true')
    s.add_argument('--reason', default='')
    s.add_argument('--allow-weak', dest='allow_weak', action='store_true')
    s.add_argument('--interval', default=1.0)
    s.add_argument('--tries', default=10)
    s.add_argument('--max-files', dest='max_files', default=20000)
    s.add_argument('--max-bytes', dest='max_bytes', default=1 << 30)
    sub.add_parser('status')
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    return {'declare': cmd_declare, 'check': cmd_check, 'scan': cmd_scan, 'status': cmd_status}[a.cmd](a)
if __name__ == '__main__':
    sys.exit(main())
