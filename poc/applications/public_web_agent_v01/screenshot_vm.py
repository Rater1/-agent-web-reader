"""Run one scanned screenshot task in an operator-provisioned offline snapshot."""
import base64
import binascii
from contextlib import contextmanager
import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import subprocess
import sys
import time
import uuid
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[3]
CONFIG = ROOT / 'screenshot.local.json'
MAX_SERIAL = 2 * 1024**2
MEMORY_FLOOR = 4608 * 1024**2
SHUTDOWN_POLLS = 10
IMAGE_NAMES = ('page_full.png', 'page_top.png', 'page_bottom.png')
FIELDS = {'work_dir', 'vbox_path', 'vm_name', 'vm_config_path', 'state_path', 'serial_path',
          'snapshot_uuid', 'snapshot_xml_sha256', 'state_sha256', 'state_bytes',
          'prepared_without_pages', 'operator_approved_live_runtime'}


def need(condition, reason):
    if not condition:
        raise ValueError(reason)


def unique(pairs):
    result = dict(pairs)
    need(len(result) == len(pairs), 'SCREENSHOT_JSON_KEYS')
    return result


def digest(path):
    hashed = hashlib.sha256()
    with path.open('rb') as stream:
        while chunk := stream.read(1024**2):
            hashed.update(chunk)
    return hashed.hexdigest()


def safe_path(path):
    candidate = Path(path)
    need(candidate.is_absolute() and not any(c in path for c in "'\r\n\x00"),
         'SCREENSHOT_RUNTIME_CONFIG')
    if sys.platform == 'win32':
        need(re.fullmatch(r'[A-Za-z]:', candidate.drive) and not any(':' in part for part in candidate.parts[1:]),
             'SCREENSHOT_RUNTIME_CONFIG')
    for component in (candidate, *candidate.parents):
        if component.exists() or component.is_symlink():
            attrs = getattr(component.lstat(), 'st_file_attributes', 0)
            need(not component.is_symlink() and not attrs & 0x400, 'SCREENSHOT_RUNTIME_CONFIG')
    return candidate.resolve()


def read_runtime():
    need(CONFIG.is_file(), 'SCREENSHOT_NOT_CONFIGURED')
    need(sys.platform == 'win32', 'SCREENSHOT_WINDOWS_ONLY')
    need(not CONFIG.is_symlink() and CONFIG.stat().st_size <= 8192, 'SCREENSHOT_RUNTIME_CONFIG')
    try:
        runtime = json.loads(CONFIG.read_text(encoding='utf-8'), object_pairs_hook=unique)
        need(type(runtime) is dict and set(runtime) == FIELDS, 'SCREENSHOT_RUNTIME_CONFIG')
        need(runtime['prepared_without_pages'] is True and runtime['operator_approved_live_runtime'] is True,
             'SCREENSHOT_RUNTIME_CONFIG')
        keys = ('work_dir', 'vbox_path', 'vm_name', 'vm_config_path', 'state_path', 'serial_path',
                'snapshot_uuid', 'snapshot_xml_sha256', 'state_sha256')
        need(all(type(runtime[key]) is str and 0 < len(runtime[key]) <= 512 for key in keys),
             'SCREENSHOT_RUNTIME_CONFIG')
        need(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9 _.-]{0,127}', runtime['vm_name']),
             'SCREENSHOT_RUNTIME_CONFIG')
        need(str(uuid.UUID(runtime['snapshot_uuid'])) == runtime['snapshot_uuid'],
             'SCREENSHOT_RUNTIME_CONFIG')
        need(all(re.fullmatch(r'[a-f0-9]{64}', runtime[name])
                 for name in ('snapshot_xml_sha256', 'state_sha256')), 'SCREENSHOT_RUNTIME_CONFIG')
        need(type(runtime['state_bytes']) is int and 0 < runtime['state_bytes'] <= 4 * 1024**3,
             'SCREENSHOT_RUNTIME_CONFIG')
        work = safe_path(runtime['work_dir'])
        vbox = safe_path(runtime['vbox_path'])
        need(work.is_dir() and work != Path(work.anchor) and not work.is_relative_to(ROOT)
             and not ROOT.is_relative_to(work) and vbox.is_file(), 'SCREENSHOT_RUNTIME_CONFIG')
        runtime.update(work_dir=work, vbox_path=vbox)
        for name in ('vm_config_path', 'state_path', 'serial_path'):
            path = safe_path(runtime[name])
            need(path.is_relative_to(work) and path != work, 'SCREENSHOT_RUNTIME_CONFIG')
            runtime[name] = path
        config, state, serial = (runtime[key] for key in ('vm_config_path', 'state_path', 'serial_path'))
        need(len({config, state, serial, vbox}) == 4 and config.suffix.lower() == '.vbox'
             and state.suffix.lower() == '.sav' and serial.parent.is_dir()
             and (not serial.exists() or serial.is_file()), 'SCREENSHOT_RUNTIME_CONFIG')
        if serial.exists():
            for other in (config, state, vbox):
                need(not other.is_file() or not serial.samefile(other), 'SCREENSHOT_RUNTIME_CONFIG')
        return runtime
    except (OSError, AttributeError, TypeError, ValueError, RuntimeError):
        raise ValueError('SCREENSHOT_RUNTIME_CONFIG') from None


def runtime_integrity(runtime):
    """Pin the exact saved state selected by the exact VBox snapshot subtree."""
    state = runtime['state_path']
    need(state.is_file() and not state.is_symlink(), 'SCREENSHOT_STATE_MISSING')
    need(state.stat().st_size == runtime['state_bytes'], 'SCREENSHOT_STATE_SIZE')
    config = runtime['vm_config_path']
    need(config.is_file() and not config.is_symlink() and config.stat().st_size <= 4 * 1024**2,
         'SCREENSHOT_VM_CONFIG')
    try:
        tree = ET.parse(config)
    except ET.ParseError:
        raise ValueError('SCREENSHOT_SNAPSHOT_CHANGED') from None
    nodes = [node for node in tree.iter() if node.tag.rsplit('}', 1)[-1] == 'Snapshot'
             and node.get('uuid', '').strip('{}') == runtime['snapshot_uuid']]
    need(len(nodes) == 1, 'SCREENSHOT_SNAPSHOT_CHANGED')
    node = nodes[0]
    need(hashlib.sha256(ET.tostring(node)).hexdigest() == runtime['snapshot_xml_sha256'],
         'SCREENSHOT_SNAPSHOT_CHANGED')
    attached = Path(node.get('stateFile', ''))
    need(attached != Path('.') and not attached.is_absolute() and state.is_relative_to(config.parent)
         and (config.parent / attached).resolve() == state, 'SCREENSHOT_SNAPSHOT_CHANGED')
    need(digest(state) == runtime['state_sha256'], 'SCREENSHOT_STATE_CHANGED')


def free_memory():
    class MemoryStatus(ctypes.Structure):
        _fields_ = [('length', ctypes.c_uint32), ('load', ctypes.c_uint32)] + [
            (key, ctypes.c_uint64) for key in
            ('total', 'available', 'page_total', 'page_available', 'virt_total',
             'virt_available', 'virt_extended')
        ]
    info = MemoryStatus()
    info.length = ctypes.sizeof(info)
    need(ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(info)), 'SCREENSHOT_MEMORY_QUERY')
    return info.available


class VBoxFailure(RuntimeError):
    pass


def command(runtime, *args):
    try:
        result = subprocess.run([str(runtime['vbox_path']), *map(str, args)], check=False,
                                capture_output=True, encoding='utf-8', errors='replace', timeout=35)
    except (OSError, subprocess.TimeoutExpired):
        raise VBoxFailure('SCREENSHOT_VM_COMMAND') from None
    if result.returncode:
        if args[0] == 'showvminfo' and 'VBOX_E_INVALID_OBJECT_STATE' in result.stderr:
            raise VBoxFailure('SCREENSHOT_VM_TRANSIENT')
        raise VBoxFailure('SCREENSHOT_VM_COMMAND')
    return result.stdout


def vm_state(runtime):
    for attempt in range(16):
        try:
            raw = command(runtime, 'showvminfo', runtime['vm_name'], '--machinereadable')
            break
        except VBoxFailure as exc:
            if str(exc) != 'SCREENSHOT_VM_TRANSIENT' or attempt == 15:
                raise
            time.sleep(.5)
    pairs = [line.split('=', 1) for line in raw.splitlines() if '=' in line]
    return {key.strip('"'): value.strip('"') for key, value in pairs}


def vm_binding(runtime, status):
    """Check local file bindings before changing a registered VM."""
    configured = status.get('CfgFile', '')
    mode, _, serial = status.get('uartmode1', '').partition(',')
    vm_uuid = status.get('UUID', '')
    need(status.get('name') == runtime['vm_name']
         and re.fullmatch(r'[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}', vm_uuid)
         and vm_uuid == runtime.get('vm_uuid', vm_uuid)
         and configured and safe_path(configured) == runtime['vm_config_path']
         and mode == 'file' and serial and safe_path(serial) == runtime['serial_path'],
         'SCREENSHOT_VM_BINDING')


def vm_configuration(runtime, status, vm_state_name):
    vm_binding(runtime, status)
    need(status.get('VMState') == vm_state_name and status.get('memory') == '3072'
         and status.get('cpus') == '2', 'SCREENSHOT_VM_CONFIG')
    need(all(status.get(f'nic{index}') == 'none' for index in range(1, 9)),
         'SCREENSHOT_VM_NETWORK')
    channel_settings = {'clipboard': 'disabled', 'clipboard_file_transfers': 'off',
                        'draganddrop': 'disabled', 'usb': 'off', 'ehci': 'off', 'xhci': 'off',
                        'vrde': 'off', 'audio': 'none', 'uart1': '0x03f8,4',
                        'uart2': 'off', 'uart3': 'off', 'uart4': 'off', 'accelerate3d': 'off'}
    need(all(status.get(key) == value for key, value in channel_settings.items())
         and not any(key.lower().startswith('sharedfolder') for key in status), 'SCREENSHOT_VM_CHANNEL')
    controllers = {key: value for key, value in status.items() if re.fullmatch('storagecontrollername[0-9]+', key)}
    need(controllers == {'storagecontrollername0': 'OfflineOptical'}
         and status.get('storagecontrollertype0') == 'PIIX4'
         and status.get('storagecontrollerportcount0') == '2'
         and [status.get(f'boot{index}') for index in range(1, 5)] == ['dvd', 'none', 'none', 'none'],
         'SCREENSHOT_VM_STORAGE')
    boot = status.get('OfflineOptical-0-0', '')
    need(boot and all(status.get(key) == 'none' for key in ('OfflineOptical-0-1', 'OfflineOptical-1-1')),
         'SCREENSHOT_VM_STORAGE')
    path = safe_path(boot)
    need(path.is_relative_to(runtime['work_dir']) and path.is_file() and path.suffix.lower() == '.iso',
         'SCREENSHOT_VM_STORAGE')
    return path


def validate_serial(runtime, boot_media):
    serial = safe_path(str(runtime['serial_path']))
    protected = (boot_media, runtime['vm_config_path'], runtime['state_path'], runtime['vbox_path'])
    need(serial not in protected and (not serial.exists() or serial.is_file()), 'SCREENSHOT_SERIAL_PATH')
    if serial.exists():
        need(not any(path.is_file() and serial.samefile(path) for path in protected), 'SCREENSHOT_SERIAL_PATH')
    return serial, protected


def clear_serial(runtime):
    serial, protected = validate_serial(runtime, runtime['boot_media'])
    if not serial.exists():
        with serial.open('xb'):
            return
    with serial.open('r+b') as stream:
        info = os.fstat(stream.fileno())
        need(not any(path.is_file() and os.path.samestat(info, path.stat()) for path in protected),
             'SCREENSHOT_SERIAL_PATH')
        stream.truncate(0)


def preflight():
    """Bind the clean snapshot, boot disc and VM before any public GET."""
    runtime = read_runtime()
    runtime_integrity(runtime)
    need(free_memory() >= MEMORY_FLOOR, 'SCREENSHOT_MEMORY_LOW')
    status = vm_state(runtime)
    boot = vm_configuration(runtime, status, 'poweroff')
    need(status.get('OfflineOptical-1-0') == 'emptydrive', 'SCREENSHOT_TASK_MEDIA')
    validate_serial(runtime, boot)
    return {**runtime, 'boot_media': boot, 'vm_uuid': status['UUID']}


@contextmanager
def vm_capture_lock(runtime):
    """Serialize captures using the registered VM directory, shared by checkouts."""
    config = safe_path(str(runtime['vm_config_path']))
    vm_uuid = str(uuid.UUID(runtime['vm_uuid']))
    lock = config.parent / f'.agent-web-screenshot-{vm_uuid}.lock'
    try:
        lock.mkdir()
    except FileExistsError:
        raise ValueError('SCREENSHOT_VM_BUSY_OR_STALE_LOCK') from None
    try:
        yield
    finally:
        lock.rmdir()


def frame(raw, job):
    need(len(raw) <= MAX_SERIAL, 'SCREENSHOT_SERIAL_LIMIT')
    lines = raw.splitlines()
    prefix = b'SCREENSHOT_RESULT:'
    rows = [line[len(prefix):] for line in lines if line.startswith(prefix)]
    need(len(rows) == 1 and lines.count(b'WARM_TASK_CLEAN') == 1 and lines.count(b'SCREENSHOT_DONE') == 1
         and [line for line in lines if line.startswith(b'SCREENSHOT_EXIT:')] == [b'SCREENSHOT_EXIT:0'],
         'SCREENSHOT_FRAME')
    try:
        result = json.loads(rows[0], object_pairs_hook=unique)
    except (UnicodeError, ValueError, RecursionError):
        raise ValueError('SCREENSHOT_FRAME') from None
    need(type(result) is dict and result.get('status') == 'CAPTURED'
         and result.get('run_id') == job['run_id'] and result.get('url') == job['url'],
         'SCREENSHOT_BINDING')
    need(result.get('network_interfaces') == ['lo'] and result.get('browser_exit') == {'code': 0, 'signal': None},
         'SCREENSHOT_GUEST_ISOLATION')
    need(type(result.get('text')) is str and len(result['text']) <= 4000
         and type(result.get('title')) is str and len(result['title']) <= 4096
         and all(type(result.get(key)) is bool for key in ('vertical_truncated', 'text_truncated'))
         and all(type(result.get(key)) is int and result[key] >= 0
                 for key in ('blocked_count', 'script_errors', 'duration_ms')), 'SCREENSHOT_SCHEMA')
    requests = result.get('blocked_requests')
    need(type(requests) is list and len(requests) == min(result['blocked_count'], 32)
         and all(type(row) is dict and set(row) == {'url', 'type', 'method'}
                 and all(type(value) is str and len(value) <= 512 for value in row.values())
                 for row in requests), 'SCREENSHOT_SCHEMA')
    metadata = result.get('screenshots')
    need(type(metadata) is list and all(type(item) is dict for item in metadata)
         and [item.get('name') for item in metadata] == list(IMAGE_NAMES), 'SCREENSHOT_IMAGE_SET')
    images = []
    for item in metadata:
        try:
            data = base64.b64decode(item.get('data', ''), validate=True)
        except (TypeError, ValueError, binascii.Error):
            raise ValueError('SCREENSHOT_PNG_BYTES') from None
        need(45 <= len(data) <= 600000 and len(data) == item.get('bytes'), 'SCREENSHOT_PNG_BYTES')
        need(data.startswith(b'\x89PNG\r\n\x1a\n\0\0\0\rIHDR')
             and data.endswith(b'\0\0\0\0IEND\xaeB\x60\x82'), 'SCREENSHOT_PNG')
        width, height = struct.unpack('>II', data[16:24])
        need(width == item.get('width') == 1100 and height == item.get('height')
             and 750 <= height <= 6000 and type(item.get('y')) is int
             and 0 <= item['y'] <= 5250, 'SCREENSHOT_DIMENSIONS')
        need(hashlib.sha256(data).hexdigest() == item.get('sha256'), 'SCREENSHOT_PNG_HASH')
        images.append(data)
    full, top, bottom = metadata
    need(full['y'] == top['y'] == 0 and top['height'] == bottom['height'] == 750
         and bottom['y'] == full['height'] - 750, 'SCREENSHOT_DIMENSIONS')
    need(sum(map(len, images)) <= 1200000, 'SCREENSHOT_PNG_TOTAL')
    return result, images


def prepare_disc(backend, cap, scan):
    backend.binding.verify(cap, scan)
    work = safe_path(str(backend.work))
    cap = safe_path(str(cap))
    need(cap.is_relative_to(work) and cap != work and not work.is_relative_to(ROOT)
         and not ROOT.is_relative_to(work), 'SCREENSHOT_TASK_MEDIA')
    entries = sorted(cap.iterdir())
    names = {path.name for path in entries}
    required = {'checksums.txt', 'job.json', 'screenshot_guest.sh', 'screenshot_guest.mjs'}
    need(required <= names and len(names) <= 14 and all(
        path.is_file() and re.fullmatch('[A-Za-z0-9._-]+', path.name) for path in entries),
        'SCREENSHOT_TASK_MEDIA')
    viso = work / 'capture.viso'
    lines = ['--iprt-iso-maker-file-marker-bourne-sh ' + str(uuid.uuid4()),
             '--volume-id=SCREENSHOT_TASK', '--file-mode=0444']
    lines.extend(f"/{path.name}='{path.as_posix()}'" for path in entries)
    with viso.open('xb') as stream:
        stream.write(('\n'.join(lines) + '\n').encode('utf-8'))
    return viso


def wait_for_frame(serial):
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        if serial.is_file():
            with serial.open('rb') as stream:
                raw = stream.read(MAX_SERIAL + 1)
            need(len(raw) <= MAX_SERIAL, 'SCREENSHOT_SERIAL_LIMIT')
            if re.search(rb'(?m)^SCREENSHOT_EXIT:[0-9]+\r?$', raw):
                return raw
        time.sleep(.25)
    raise ValueError('SCREENSHOT_RENDER_TIMEOUT')


def change_task_disc(runtime, medium):
    command(runtime, 'storageattach', runtime['vm_name'], '--storagectl', 'OfflineOptical',
            '--port', '1', '--device', '0', '--type', 'dvddrive', '--medium', medium)


def owned_vm_state(runtime, viso):
    status = vm_state(runtime)
    vm_binding(runtime, status)
    attached = status.get('OfflineOptical-1-0')
    need(attached == 'emptydrive' or (attached and safe_path(attached) == viso), 'SCREENSHOT_TASK_MEDIA')
    return status


def wait_for_shutdown(runtime, viso, status):
    for _ in range(SHUTDOWN_POLLS):
        if status.get('VMState') not in ('running', 'stopping'):
            break
        time.sleep(.5)
        status = owned_vm_state(runtime, viso)
    return status


def cleanup(runtime, viso, *, started_vm):
    status = owned_vm_state(runtime, viso)
    need(started_vm or status.get('VMState') != 'running', 'SCREENSHOT_VM_OWNERSHIP')
    status = wait_for_shutdown(runtime, viso, status)
    if status.get('VMState') == 'running':
        need(started_vm, 'SCREENSHOT_VM_OWNERSHIP')
        command(runtime, 'controlvm', runtime['vm_name'], 'poweroff')
        status = wait_for_shutdown(runtime, viso, owned_vm_state(runtime, viso))
    if status.get('VMState') == 'saved':
        command(runtime, 'discardstate', runtime['vm_name'])
        status = wait_for_shutdown(runtime, viso, owned_vm_state(runtime, viso))
    need(status.get('VMState') == 'poweroff', 'SCREENSHOT_VM_SHUTDOWN')
    need(vm_configuration(runtime, status, 'poweroff') == runtime['boot_media'], 'SCREENSHOT_VM_STORAGE')
    if status.get('OfflineOptical-1-0') != 'emptydrive':
        change_task_disc(runtime, 'emptydrive')
        status = owned_vm_state(runtime, viso)
        need(vm_configuration(runtime, status, 'poweroff') == runtime['boot_media']
             and status.get('OfflineOptical-1-0') == 'emptydrive', 'SCREENSHOT_TASK_MEDIA')
    runtime_integrity(runtime)


def render(backend, cap, scan, job, runtime):
    """Run exactly one existing scanned capsule, then verify complete teardown."""
    backend.record['phase'] = 'SCREENSHOT_VM_PREFLIGHT'
    vm = runtime['vm_name']
    started = time.perf_counter()
    with vm_capture_lock(runtime):
        need(preflight() == runtime, 'SCREENSHOT_RUNTIME_CHANGED')
        viso = prepare_disc(backend, cap, scan)
        checksum = digest(cap / 'checksums.txt')
        command(runtime, 'snapshot', vm, 'restore', runtime['snapshot_uuid'])
        started_vm = False
        try:
            status = vm_state(runtime)
            need(vm_configuration(runtime, status, 'saved') == runtime['boot_media']
                 and status.get('OfflineOptical-1-0') == 'emptydrive', 'SCREENSHOT_TASK_MEDIA')
            backend.binding.verify(cap, scan)
            clear_serial(runtime)
            command(runtime, 'startvm', vm, '--type', 'headless')
            started_vm = True
            change_task_disc(runtime, viso)
            script = ("{ mkdir -p /mnt/fixtures; mount -o ro /dev/sr1 /mnt/fixtures && cd /mnt/fixtures "
                      f"&& echo '{checksum}  checksums.txt' | sha256sum -c - && sh screenshot_guest.sh; "
                      "echo SCREENSHOT_EXIT:$?; sync; poweroff -f; } > /dev/ttyS0 2>&1")
            command(runtime, 'controlvm', vm, 'keyboardputstring', script)
            command(runtime, 'controlvm', vm, 'keyboardputscancode', '1c', '9c')
            raw = wait_for_frame(runtime['serial_path'])
            (backend.evidence / 'guest_serial.txt').write_bytes(raw)
            result, images = frame(raw, job)
        finally:
            cleanup(runtime, viso, started_vm=started_vm)
            backend.binding.verify(cap, scan)
    backend.record.update(vm_final_state='poweroff', task_media_detached=True,
                          vm_seconds=round(time.perf_counter() - started, 3))
    screenshots = []
    for item, data in zip(result['screenshots'], images):
        path = backend.evidence / item['name']
        with path.open('xb') as stream:
            stream.write(data)
        screenshots.append({key: item[key] for key in ('name', 'bytes', 'sha256', 'width', 'height', 'y')}
                           | {'path': str(path)})
    fields = ('run_id', 'url', 'title', 'text', 'text_truncated', 'vertical_truncated',
              'blocked_requests', 'blocked_count', 'script_errors')
    return {key: result[key] for key in fields} | {
        'status': 'SCREENSHOT_READY', 'screenshots': screenshots, 'browser_render_ms': result['duration_ms'],
        'resource_complete': result['blocked_count'] == 0 and result['script_errors'] == 0,
    }
