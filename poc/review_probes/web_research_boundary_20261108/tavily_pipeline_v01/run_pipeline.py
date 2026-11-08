"""Fixed one-query acquisition -> scan -> read-only capsule -> no-NIC guest.

Synthetic is the default. --live must be invoked by the operator in their key's
PowerShell window. All child processes receive an explicit credential-free env.
"""
import argparse
import datetime
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid
HERE = Path(__file__).resolve().parent
BASE = HERE.parent
REPO = HERE.parents[3]
OLD = BASE / 'search_extract_v01'
SOURCE_PATHS = []


def require(ok, reason):
    if not ok:
        raise RuntimeError(reason)

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def put(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=True, indent=2) + '\n', encoding='utf-8')

def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()

def load(name, file):
    spec = importlib.util.spec_from_file_location(name, file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def source_preflight(frozen, tests, protected, *, base=BASE, gate=None):
    require(set(frozen['sources']) == set(SOURCE_PATHS), 'SOURCE_SET')
    require(all((sha(base / n) == h for n, h in frozen['sources'].items())), 'SOURCE_HASH')
    require(sha(gate or REPO / 'poc/download_gate/dl_gate.py') == frozen['download_gate_sha256'], 'GATE_HASH')
    require(tests() == 0, 'SELFTEST_FAILED')
    return protected()

class Pipeline:

    def __init__(self, mode, frozen):
        require(mode in ('synthetic', 'live'), 'MODE')
        self.mode, self.frozen = (mode, frozen)
        self.vm = load('pipeline_vm_constants', BASE / 'prepare_offline_vm.py')
        self.binding = load('pipeline_scan_binding', OLD / 'scan_binding.py')
        self.control = load('pipeline_fixed_broker', BASE / 'tavily_controlled_v01/controlled_search.py')
        self.run_id = 'tvp-' + datetime.datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:8]
        self.work = self.vm.TASK / 'tavily_pipeline_v01' / self.run_id
        self.evidence = REPO / '.local' / 'tavily_pipeline_v01' / self.run_id
        self.pkg = self.vm.TASK / 'search_extract_v01/packages'
        self.work.mkdir(parents=True, exist_ok=False)
        self.evidence.mkdir(parents=True, exist_ok=False)
        (self.work / 'temp').mkdir()
        profile = Path.home()
        self.env = dict(SystemRoot='C:\\Windows', WINDIR='C:\\Windows', SystemDrive='C:', PATH='C:\\Windows\\System32;C:\\Windows;C:\\Windows\\System32\\WindowsPowerShell\\v1.0', USERPROFILE=str(profile), APPDATA=str(profile / 'AppData/Roaming'), LOCALAPPDATA=str(profile / 'AppData/Local'), ProgramData='C:\\ProgramData', TEMP=str(self.work / 'temp'), TMP=str(self.work / 'temp'), PYTHONIOENCODING='utf-8', PYTHONDONTWRITEBYTECODE='1')
        self.vm.run = self.vbox
        self.gate = REPO / 'poc/download_gate/dl_gate.py'
        self.record = dict(mode=mode, phase='PREFLIGHT', run_id=self.run_id, real_transport_attempts=0, key_read=False, result_urls_followed=0)

    def command(self, argv, timeout=90):
        child = subprocess.run(list(map(str, argv)), env=self.env, capture_output=True, timeout=timeout)
        if child.returncode:
            (self.evidence / 'failed_child_stdout.txt').write_bytes(child.stdout)
            (self.evidence / 'failed_child_stderr.txt').write_bytes(child.stderr)
        require(child.returncode == 0, 'CHILD_COMMAND_FAILED')
        return child.stdout

    def vbox(self, *args):
        return self.command([self.vm.VBOX, *args], timeout=35).decode('utf-8', errors='replace')

    def latest_scan(self, path, tag):
        rows = [json.loads(s) for s in (self.gate.parent / '_dl_manifest.jsonl').read_text(encoding='utf-8').splitlines() if s.strip()]
        row = [r for r in rows if r.get('event') == 'scan' and r.get('tag') == tag][-1]
        self.binding.verify(path, row)
        return row

    def material_preflight(self):
        require(sha(self.vm.ISO) == self.vm.EXPECTED, 'ISO_HASH')
        receipt = self.latest_scan(self.pkg, 'web-parser-apks-20261108')
        require(receipt['content_sha256'] == self.frozen['packages_content_sha256'], 'PACKAGE_BINDING')
        package_rows = json.loads((OLD / 'apk_download_receipt.json').read_text())['packages']
        require(set((p.name for p in self.pkg.iterdir())) == {r['filename'] for r in package_rows}, 'PACKAGE_MEMBERS')
        require(all((sha(self.pkg / r['filename']) == r['sha256'] for r in package_rows)), 'PACKAGE_HASH')
        self.package_rows = package_rows
        _, conf = self.vm.info(self.vm.NAME)
        self.check_vm(conf)

    @staticmethod
    def check_vm(conf):
        require(conf['VMState'] == 'poweroff' and all((conf.get('nic' + str(i)) == 'none' for i in range(1, 9))), 'VM_BUSY_OR_NETWORK')
        require(conf.get('clipboard') == 'disabled' and conf.get('draganddrop') == 'disabled' and (not any((k.startswith('SharedFolder') for k in conf))), 'VM_DATA_CHANNEL')

    def declare(self, path, tag, source, code=False):
        args = [sys.executable, self.gate, 'declare', '--kind', 'other', '--target', 'Fixed Tavily search response' if not code else 'Verified parser runtime and fixed Tavily response capsule', '--source', source, '--size-mb', '2', '--scratch', path, '--tag', tag]
        if code:
            args += ['--executes-code', '--pi-exception', '--reason', 'Operator-configured no-NIC parser runtime; no host execution of external page code.']
        self.command(args)
        rows = [json.loads(s) for s in (self.gate.parent / '_dl_manifest.jsonl').read_text(encoding='utf-8').splitlines() if s.strip()]
        row = [r for r in rows if r.get('event') == 'declare' and r.get('tag') == tag][-1]
        declaration = self.evidence / (tag + '.jsonl')
        declaration.write_text(json.dumps(row) + '\n', encoding='utf-8')
        self.command([sys.executable, self.gate, 'check', '--manifest', declaration])

    def scan(self, path, tag):
        log = self.command([sys.executable, self.gate, 'scan', '--path', path, '--tag', tag], timeout=180)
        (self.evidence / (tag + '_scan.log')).write_bytes(log)
        receipt = self.latest_scan(path, tag)
        put(self.evidence / (tag + '_scan.json'), receipt)
        return receipt

    def send(self, text):
        self.vbox('controlvm', self.vm.NAME, 'keyboardputstring', text)
        self.vbox('controlvm', self.vm.NAME, 'keyboardputscancode', '1c', '9c')

    def guest(self, capsule, scan):
        self.binding.verify(capsule, scan)
        self.latest_scan(self.pkg, 'web-parser-apks-20261108')
        source_preflight(self.frozen, lambda: 0, lambda: None)
        before, conf = self.vm.info(self.vm.NAME)
        self.check_vm(conf)
        old_before = self.vm.info(self.vm.OLD)[0] if self.vm.OLD else None
        (self.evidence / 'vm_before.txt').write_text(before, encoding='utf-8')
        viso, serial = (self.work / 'parser.viso', self.work / 'serial.txt')
        maps = [f"/{p.name}='{p.as_posix()}'" for p in sorted(capsule.iterdir())]
        maps += [f"/apks/{p.name}='{p.as_posix()}'" for p in sorted(self.pkg.iterdir())]
        viso.write_text('--iprt-iso-maker-file-marker-bourne-sh ' + str(uuid.uuid4()) + '\n--volume-id=TAVILY_OFFLINE\n--file-mode=0444\n' + '\n'.join(maps) + '\n', encoding='utf-8', newline='\n')
        attached = False
        try:
            self.vbox('storageattach', self.vm.NAME, '--storagectl', 'OfflineOptical', '--port', '1', '--device', '0', '--type', 'dvddrive', '--medium', viso)
            attached = True
            self.vbox('modifyvm', self.vm.NAME, '--uart-mode1', 'file', serial)
            self.vbox('startvm', self.vm.NAME, '--type', 'headless')
            time.sleep(17)
            self.send('root')
            time.sleep(2)
            checksum = self.record['checksum_sha256']
            require(len(checksum) == 64 and all((c in '0123456789abcdef' for c in checksum)), 'CHECKSUM_SYNTAX')
            self.send("{ mkdir -p /mnt/fixtures; mount -o ro /dev/sr1 /mnt/fixtures && cd /mnt/fixtures && echo '" + checksum + "  input_checksums.txt' | sha256sum -c - && sh guest_probe.sh; } > /dev/ttyS0 2>&1")
            deadline, raw = (time.monotonic() + 90, b'')
            while time.monotonic() < deadline:
                if serial.exists():
                    with serial.open('rb') as stream:
                        raw = stream.read(131073)
                    require(len(raw) <= 131072, 'SERIAL_LIMIT')
                    if b'TAVILY_GUEST_DONE' in raw:
                        break
                time.sleep(1)
            (self.evidence / 'guest_serial_raw.txt').write_bytes(raw)
            require(b'TAVILY_GUEST_DONE' in raw, 'GUEST_INCOMPLETE')
            frames = [r[len(b'TAVILY_PARSED_RESULT:'):] for r in raw.splitlines() if r.startswith(b'TAVILY_PARSED_RESULT:')]
            require(len(frames) == 1, 'RESULT_FRAME')
            parsed = json.loads(frames[0])
            require(parsed['mode'] == self.mode and parsed['network_interfaces'] == ['lo'] and (parsed['result_urls_followed'] == 0), 'RESULT_NETWORK_OR_MODE')
            require(len(parsed['results']) == self.record['job_count'], 'RESULT_COUNT')
            require(parsed['results'][0]['input_sha256'] == self.record['provider_body_sha256'], 'RESULT_BODY_BINDING')
            if self.mode == 'synthetic':
                require([r['outcome'] for r in parsed['results']] == ['SEARCH_RESULTS', 'EMPTY_RESULTS', 'REJECTED', 'REJECTED'], 'SYNTHETIC_OUTCOMES')
                require(len(parsed['results'][0]['results']) == 1 and parsed['results'][0]['omitted_unsafe_urls'] == 1, 'CANDIDATE_FILTER')
            self.record.update(phase='PIPELINE_COMPLETE', parsed=parsed, serial_sha256=sha(self.evidence / 'guest_serial_raw.txt'))
            put(self.evidence / 'parsed_results.json', parsed)
        finally:
            if attached:
                if self.vm.info(self.vm.NAME)[1]['VMState'] == 'running':
                    self.send('poweroff')
                    for _ in range(8):
                        time.sleep(1)
                        if self.vm.info(self.vm.NAME)[1]['VMState'] == 'poweroff':
                            break
                    if self.vm.info(self.vm.NAME)[1]['VMState'] != 'poweroff':
                        self.vbox('controlvm', self.vm.NAME, 'poweroff')
                require(self.vm.info(self.vm.NAME)[1]['VMState'] == 'poweroff', 'CLEANUP_POWER')
                self.vbox('storageattach', self.vm.NAME, '--storagectl', 'OfflineOptical', '--port', '1', '--device', '0', '--type', 'dvddrive', '--medium', self.vm.TASK / 'public_inputs.viso')
                self.vbox('modifyvm', self.vm.NAME, '--uart-mode1', 'file', self.vm.TASK / 'serial_result.txt')
            after, final = self.vm.info(self.vm.NAME)
            self.check_vm(final)
            unchanged = old_before == self.vm.info(self.vm.OLD)[0] if self.vm.OLD else None
            self.record.update(final_vm_state=final['VMState'], final_nics_none=True, old_vm_config_unchanged=unchanged)
            (self.evidence / 'vm_after.txt').write_text(after, encoding='utf-8')
            require(unchanged is not False, 'OLD_VM_CHANGED')

    def execute(self):
        try:
            self.record['phase'] = 'MATERIAL_PREFLIGHT'
            self.material_preflight()
            self.record['phase'] = 'ACQUISITION_AND_SCAN'
            incoming, receipt = self.acquire()
            self.record['phase'] = 'STAGING_AND_SCAN'
            capsule, scan = self.stage(incoming, receipt)
            self.record['phase'] = 'GUEST'
            self.guest(capsule, scan)
            return self.record
        except Exception as exc:
            self.record['failed_phase'] = self.record['phase']
            self.record['phase'] = 'FAILED_STOPPED'
            self.record['error'] = 'PIPELINE_CHECK_FAILED'
            self.record['error_type'] = type(exc).__name__
            return self.record
        finally:
            put(self.evidence / 'run_receipt.json', self.record)
if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception:
        print('{"phase":"DRIVER_STOPPED","request_status":"UNKNOWN"}')
        sys.exit(1)
