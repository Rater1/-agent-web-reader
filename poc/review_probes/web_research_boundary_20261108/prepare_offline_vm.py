"""Use an explicitly provisioned VM; never install, download or create one."""
import importlib.util
from pathlib import Path
import subprocess

OUT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('vm_operator_config', OUT/'runtime_config.py')
config_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(config_module)
config = config_module.load_config()
TASK = Path(config['work_dir'])
VBOX = config['vbox_path']
NAME = config['vm_name']
OLD = config['baseline_vm']
ISO = TASK/'download/alpine-virt-3.24.4-x86_64.iso'
EXPECTED = '157d353f8e54252cad2d9812a0d56bb7fa2231fc154a110e1d180d7480ef8b4f'


def run(*args):
    raise RuntimeError('PIPELINE_COMMAND_WRAPPER_REQUIRED')


def info(name):
    raw = run('showvminfo', name, '--machinereadable')
    result = {}
    for line in raw.splitlines():
        if '=' in line:
            key, value = line.split('=', 1)
            result[key] = value.strip('"')
    return raw, result
