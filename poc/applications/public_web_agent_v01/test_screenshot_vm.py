"""Offline checks for the public screenshot protocol and VirtualBox guards."""
import base64
from contextlib import nullcontext
import copy
import hashlib
import json
from pathlib import Path
import struct
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import uuid
import xml.etree.ElementTree as ET
import zlib

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import screenshot_vm as vm


def tiny_png(width=1100, height=750):
    def chunk(name, data):
        crc = zlib.crc32(name + data)
        return struct.pack('>I', len(data)) + name + data + struct.pack('>I', crc)

    header = struct.pack('>IIBBBBB', width, height, 8, 0, 0, 0, 0)
    pixels = zlib.compress(bytes((width+1)*height))
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', header) + chunk(b'IDAT', pixels) + chunk(b'IEND', b'')


def fixture_result(job):
    image = tiny_png()
    entries = [dict(name=name, bytes=len(image), sha256=hashlib.sha256(image).hexdigest(),
                    width=1100, height=750, y=0, data=base64.b64encode(image).decode('ascii'))
               for name in vm.IMAGE_NAMES]
    return {**job, 'status': 'CAPTURED', 'title': 'Synthetic', 'text': 'Fixture content',
            'text_truncated': False, 'vertical_truncated': False, 'blocked_requests': [],
            'blocked_count': 0, 'script_errors': 0, 'duration_ms': 42,
            'network_interfaces': ['lo'], 'browser_exit': {'code': 0, 'signal': None},
            'screenshots': entries}


def packet(result):
    return (b'WARM_TASK_CLEAN\nSCREENSHOT_RESULT:' + json.dumps(result).encode('utf-8')
            + b'\nSCREENSHOT_DONE\nSCREENSHOT_EXIT:0\n')


class Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        work = Path(self.temp.name).resolve()
        state = work / 'snapshot.sav'
        config = work / 'machine.vbox'
        self.iso = work / 'boot.iso'
        self.iso.write_bytes(b'Synthetic ISO identity')
        saved = b'Synthetic saved state'
        state.write_bytes(saved)
        snapshot_id = str(uuid.uuid4())
        config.write_text(f'<Machine><Snapshot uuid="{{{snapshot_id}}}" stateFile="{state.name}"/></Machine>',
                          encoding='utf-8')
        subtree = next(ET.parse(config).iter('Snapshot'))
        self.runtime = dict(work_dir=work, vbox_path=work / 'VBoxManage.exe', vm_name='FixtureScreenshotVM',
                            vm_config_path=config, state_path=state, serial_path=work / 'console.txt',
                            snapshot_uuid=snapshot_id,
                            snapshot_xml_sha256=hashlib.sha256(ET.tostring(subtree)).hexdigest(),
                            state_sha256=hashlib.sha256(saved).hexdigest(), state_bytes=len(saved),
                            prepared_without_pages=True, operator_approved_live_runtime=True,
                            boot_media=self.iso, vm_uuid=str(uuid.uuid4()))
        self.configuration = {'CfgFile': str(config), 'uartmode1': 'file,'+str(self.runtime['serial_path']),
                              'name': self.runtime['vm_name'], 'UUID': self.runtime['vm_uuid'],
                              'VMState': 'poweroff', 'memory': '3072', 'cpus': '2', 'clipboard': 'disabled',
                              'clipboard_file_transfers': 'off', 'draganddrop': 'disabled', 'usb': 'off',
                              'ehci': 'off', 'xhci': 'off', 'vrde': 'off', 'audio': 'none', 'uart1': '0x03f8,4',
                              'uart2': 'off', 'uart3': 'off', 'uart4': 'off', 'accelerate3d': 'off',
                              'storagecontrollername0': 'OfflineOptical', 'storagecontrollertype0': 'PIIX4',
                              'storagecontrollerportcount0': '2', 'OfflineOptical-0-0': str(self.iso),
                              'OfflineOptical-0-1': 'none', 'OfflineOptical-1-0': 'emptydrive',
                              'OfflineOptical-1-1': 'none', 'boot1': 'dvd', 'boot2': 'none', 'boot3': 'none',
                              'boot4': 'none'} | {f'nic{index}': 'none' for index in range(1, 9)}
        self.job = dict(run_id='synthetic-test', url='https://example.com/')

    def test_snapshot_is_exact_identity(self):
        vm.runtime_integrity(self.runtime)
        changed_state = b'!' * self.runtime['state_bytes']
        self.runtime['state_path'].write_bytes(changed_state)
        with self.assertRaisesRegex(ValueError, 'SCREENSHOT_STATE_CHANGED'):
            vm.runtime_integrity(self.runtime)

    def test_signed_snapshot_bytes_cannot_mask_unbound_state_path(self):
        config = self.runtime['vm_config_path']
        subtree = next(ET.parse(config).iter('Snapshot'))
        subtree.set('stateFile', 'different.sav')
        config.write_text(ET.tostring(subtree, encoding='unicode'), encoding='utf-8')
        self.runtime['snapshot_xml_sha256'] = hashlib.sha256(ET.tostring(subtree)).hexdigest()
        with self.assertRaisesRegex(ValueError, 'SCREENSHOT_SNAPSHOT_CHANGED'):
            vm.runtime_integrity(self.runtime)

    def test_vm_topology_rejects_network_channels_or_storage(self):
        self.assertEqual(vm.vm_configuration(self.runtime, self.configuration, 'poweroff'), self.iso)
        for key, value, reason in (('nic8', 'nat', 'VM_NETWORK'),
                                   ('SharedFolderNameMachineMapping1', 'extra', 'VM_CHANNEL'),
                                   ('storagecontrollername1', 'extra', 'VM_STORAGE'),
                                   ('name', 'DifferentVM', 'VM_BINDING'),
                                   ('UUID', str(uuid.uuid4()), 'VM_BINDING'),
                                   ('uartmode1', 'file,'+str(self.iso), 'VM_BINDING')):
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, reason):
                vm.vm_configuration(self.runtime, self.configuration | {key: value}, 'poweroff')

    def test_preflight_missing_or_low_memory_blocks_virtualbox(self):
        with patch.object(vm, 'CONFIG', self.runtime['work_dir']/'missing.json'), \
             patch.object(vm, 'command') as command:
            with self.assertRaisesRegex(ValueError, 'SCREENSHOT_NOT_CONFIGURED'):
                vm.preflight()
            command.assert_not_called()
        with patch.object(vm, 'read_runtime', return_value=self.runtime), \
             patch.object(vm, 'free_memory', return_value=vm.MEMORY_FLOOR - 1), \
             patch.object(vm, 'command') as command:
            with self.assertRaisesRegex(ValueError, 'SCREENSHOT_MEMORY_LOW'):
                vm.preflight()
            command.assert_not_called()

    def test_preflight_blocks_boot_iso_as_serial_path_or_hard_link(self):
        alias = self.runtime['serial_path']
        alias.write_bytes(b'Alias fixture')
        samefile = Path.samefile

        def link_identity(path, target):
            return (path == alias and target == self.iso) or samefile(path, target)

        for path in (self.iso, alias):
            runtime = self.runtime | {'serial_path': path}
            config = self.configuration | {'uartmode1': 'file,' + str(path)}
            with self.subTest(path=path.name), patch.object(vm, 'read_runtime', return_value=runtime), \
                 patch.object(vm, 'free_memory', return_value=vm.MEMORY_FLOOR), \
                 patch.object(vm, 'vm_state', return_value=config), \
                 patch.object(Path, 'samefile', link_identity), \
                 self.assertRaisesRegex(ValueError, 'SCREENSHOT_SERIAL_PATH'):
                vm.preflight()
        self.assertEqual(self.iso.read_bytes(), b'Synthetic ISO identity')

    def test_guest_and_pngs_must_bind_to_job(self):
        result = fixture_result(self.job)
        value, images = vm.frame(packet(result), self.job)
        self.assertEqual(value, result)
        self.assertEqual(len(images), 3)
        for key, changed in (('url', 'https://example.org/'), ('network_interfaces', ['lo', 'eth0']),
                             ('screenshots', [result['screenshots'][0] | {'name': '../inside.png'}]),
                             ('blocked_count', 4)):
            with self.subTest(key=key), self.assertRaises(ValueError):
                vm.frame(packet(result | {key: changed}), self.job)
        changed = copy.deepcopy(result)
        changed['screenshots'][2]['y'] = 1
        with self.assertRaisesRegex(ValueError, 'SCREENSHOT_DIMENSIONS'):
            vm.frame(packet(changed), self.job)
        for frame in (packet(result).replace(b'SCREENSHOT_EXIT:0', b'SCREENSHOT_EXIT:1'),
                      packet(result) + packet(result)):
            with self.assertRaisesRegex(ValueError, 'SCREENSHOT_FRAME'):
                vm.frame(frame, self.job)

    def test_restore_checks_again_before_vm_start(self):
        cap = self.runtime['work_dir'] / 'capsule'
        cap.mkdir()
        (cap/'checksums.txt').write_bytes(b'Fixture checksums')
        restored = self.configuration | {'VMState': 'saved', 'nic1': 'nat'}
        backend = SimpleNamespace(record={}, binding=Mock(), work=self.runtime['work_dir'],
                                  measure=Mock(side_effect=lambda _: nullcontext()))
        with patch.object(vm, 'preflight', return_value=self.runtime), \
             patch.object(vm, 'prepare_disc', return_value=self.runtime['work_dir']/'task.viso'), \
             patch.object(vm, 'vm_state', return_value=restored), patch.object(vm, 'cleanup') as cleanup, \
             patch.object(vm, 'command') as command:
            with self.assertRaisesRegex(ValueError, 'SCREENSHOT_VM_NETWORK'):
                vm.render(backend, cap, {}, self.job, self.runtime)
            command.assert_called_once_with(self.runtime, 'snapshot', self.runtime['vm_name'],
                                            'restore', self.runtime['snapshot_uuid'])
            cleanup.assert_called_once_with(self.runtime, self.runtime['work_dir']/'task.viso', started_vm=False)
            self.assertEqual([call.args[0] for call in backend.measure.call_args_list],
                             ['vm_prepare', 'vm_render', 'vm_cleanup'])

    def test_late_iso_alias_blocks_serial_clear_and_vm_start(self):
        cap = self.runtime['work_dir'] / 'capsule'
        cap.mkdir()
        (cap/'checksums.txt').write_bytes(b'Fixture checksums')
        viso = self.runtime['work_dir'] / 'task.viso'
        backend = SimpleNamespace(record={}, binding=Mock(), work=self.runtime['work_dir'],
                                  measure=lambda _: nullcontext())
        alias = self.runtime['serial_path']
        samefile = Path.samefile

        def attach_alias(*_):
            if not alias.exists():
                alias.write_bytes(b'Alias fixture')

        def link_identity(path, target):
            return (path == alias and target == self.iso) or samefile(path, target)

        backend.binding.verify.side_effect = attach_alias
        restored = self.configuration | {'VMState': 'saved'}
        with patch.object(vm, 'preflight', return_value=self.runtime), \
             patch.object(vm, 'prepare_disc', return_value=viso), \
             patch.object(vm, 'vm_state', return_value=restored), patch.object(vm, 'cleanup') as cleanup, \
             patch.object(Path, 'samefile', link_identity), \
             patch.object(vm, 'command') as command:
            with self.assertRaisesRegex(ValueError, 'SCREENSHOT_SERIAL_PATH'):
                vm.render(backend, cap, {}, self.job, self.runtime)
            command.assert_called_once_with(self.runtime, 'snapshot', self.runtime['vm_name'],
                                            'restore', self.runtime['snapshot_uuid'])
            cleanup.assert_called_once_with(self.runtime, viso, started_vm=False)
        self.assertEqual(self.iso.read_bytes(), b'Synthetic ISO identity')

    def test_vm_lock_blocks_another_checkout_before_final_preflight(self):
        backend = SimpleNamespace(record={})
        other_checkout = self.runtime | {'work_dir': self.runtime['work_dir']/'another-work-dir'}
        with vm.vm_capture_lock(self.runtime), patch.object(vm, 'preflight') as preflight, \
             patch.object(vm, 'command') as command, patch.object(vm, 'cleanup') as cleanup:
            with self.assertRaisesRegex(ValueError, 'SCREENSHOT_VM_BUSY_OR_STALE_LOCK'):
                vm.render(backend, None, {}, self.job, other_checkout)
            preflight.assert_not_called()
            command.assert_not_called()
            cleanup.assert_not_called()
        with vm.vm_capture_lock(other_checkout):
            pass

    def test_restore_failure_does_not_take_control_of_another_session(self):
        cap = self.runtime['work_dir'] / 'capsule'
        cap.mkdir()
        (cap/'checksums.txt').write_bytes(b'Fixture checksums')
        backend = SimpleNamespace(record={}, binding=Mock(), measure=Mock(side_effect=lambda _: nullcontext()))
        with patch.object(vm, 'preflight', return_value=self.runtime), \
             patch.object(vm, 'prepare_disc', return_value=self.runtime['work_dir']/'task.viso'), \
             patch.object(vm, 'command', side_effect=vm.VBoxFailure('SCREENSHOT_VM_COMMAND')) as command, \
             patch.object(vm, 'cleanup') as cleanup:
            with self.assertRaisesRegex(vm.VBoxFailure, 'SCREENSHOT_VM_COMMAND'):
                vm.render(backend, cap, {}, self.job, self.runtime)
            command.assert_called_once_with(self.runtime, 'snapshot', self.runtime['vm_name'],
                                            'restore', self.runtime['snapshot_uuid'])
            cleanup.assert_not_called()
            backend.measure.assert_called_once_with('vm_prepare')

    def test_cleanup_detaches_only_its_own_media(self):
        viso = self.runtime['work_dir'] / 'task.viso'
        unrelated = self.configuration | {'OfflineOptical-1-0': str(self.runtime['work_dir']/'other.viso')}
        with patch.object(vm, 'vm_state', return_value=unrelated), \
             patch.object(vm, 'change_task_disc') as detach, patch.object(vm, 'command') as command:
            with self.assertRaisesRegex(ValueError, 'SCREENSHOT_TASK_MEDIA'):
                vm.cleanup(self.runtime, viso, started_vm=True)
            detach.assert_not_called()
            command.assert_not_called()
        attached = self.configuration | {'OfflineOptical-1-0': str(viso)}
        with patch.object(vm, 'vm_state', side_effect=[attached, self.configuration]), \
             patch.object(vm, 'change_task_disc') as detach:
            vm.cleanup(self.runtime, viso, started_vm=True)
            detach.assert_called_once_with(self.runtime, 'emptydrive')

    def test_cleanup_waits_through_stopping_before_detaching(self):
        viso = self.runtime['work_dir'] / 'task.viso'
        attached = self.configuration | {'OfflineOptical-1-0': str(viso)}
        states = [attached | {'VMState': 'running'}, attached | {'VMState': 'stopping'},
                  attached, self.configuration]
        with patch.object(vm, 'vm_state', side_effect=states), patch.object(vm.time, 'sleep'), \
             patch.object(vm, 'command') as command, patch.object(vm, 'change_task_disc') as detach:
            vm.cleanup(self.runtime, viso, started_vm=True)
            command.assert_not_called()
            detach.assert_called_once_with(self.runtime, 'emptydrive')

    def test_cleanup_never_turns_off_a_vm_it_did_not_start(self):
        viso = self.runtime['work_dir'] / 'task.viso'
        running = self.configuration | {'VMState': 'running'}
        with patch.object(vm, 'vm_state', return_value=running), patch.object(vm.time, 'sleep'), \
             patch.object(vm, 'command') as command, patch.object(vm, 'change_task_disc') as detach:
            with self.assertRaisesRegex(ValueError, 'SCREENSHOT_VM_OWNERSHIP'):
                vm.cleanup(self.runtime, viso, started_vm=False)
            command.assert_not_called()
            detach.assert_not_called()

    def test_cleanup_stopping_timeout_leaves_its_media_alone(self):
        viso = self.runtime['work_dir'] / 'task.viso'
        stopping = self.configuration | {'VMState': 'stopping', 'OfflineOptical-1-0': str(viso)}
        with patch.object(vm, 'vm_state', return_value=stopping) as state, \
             patch.object(vm.time, 'sleep'), patch.object(vm, 'command') as command, \
             patch.object(vm, 'change_task_disc') as detach:
            with self.assertRaisesRegex(ValueError, 'SCREENSHOT_VM_SHUTDOWN'):
                vm.cleanup(self.runtime, viso, started_vm=True)
            self.assertEqual(state.call_count, vm.SHUTDOWN_POLLS + 1)
            command.assert_not_called()
            detach.assert_not_called()

    def test_existing_valid_serial_is_cleared_and_missing_serial_is_created(self):
        serial = self.runtime['serial_path']
        vm.clear_serial(self.runtime)
        self.assertEqual(serial.read_bytes(), b'')
        serial.write_bytes(b'Previous frame')
        vm.clear_serial(self.runtime)
        self.assertEqual(serial.read_bytes(), b'')


if __name__ == '__main__':
    unittest.main()
