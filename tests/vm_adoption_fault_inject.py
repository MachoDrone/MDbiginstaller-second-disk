#!/usr/bin/env python3
"""Abort real adoption at a durable write, only inside the disposable SATA VM.

Copy this next to storage.py/adoption.py or pass the storage.py path. Normal
interactive selection, identity checks and typed confirmation still apply.
This harness is not embedded in the production installer.
"""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys

if len(sys.argv) != 3:
    raise SystemExit('Usage: vm_adoption_fault_inject.py POINT /path/to/storage.py')
point, source = sys.argv[1:]
boundaries = {
    'after-fstab': Path('/etc/fstab'),
    'after-marker': Path('/var/lib/docker/nosana-runtime/.owner'),
    'after-containerd-config': Path('/etc/containerd/config.toml'),
}
if point not in boundaries or os.geteuid() != 0:
    raise SystemExit('Root and a known fault boundary are required in the test VM')
for command, expected in ((['hostname'], 'installer-lab'),
                          (['lsblk', '-dno', 'SERIAL', '/dev/sda'], 'TESTROOT')):
    value = subprocess.run(command, capture_output=True, text=True, check=True).stdout.strip()
    if value != expected:
        raise SystemExit('Refusing: this is not the designated disposable SATA VM')

sys.path.insert(0, str(Path(source).resolve().parent))
spec = importlib.util.spec_from_file_location('storage_adoption_fault', source)
storage = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = storage
spec.loader.exec_module(storage)
real_atomic = storage.atomic


def atomic(path, *args, **kwargs):
    result = real_atomic(path, *args, **kwargs)
    if Path(path) == boundaries[point]:
        print('TEST: forced process death at ' + point, flush=True)
        os._exit(86)
    return result


storage.atomic = atomic
storage.main([])
raise SystemExit('Fault boundary was not reached: ' + point)
