#!/usr/bin/env python3
"""Kill real setup at a durable boundary, ONLY in the disposable test VM.

This harness lives outside the shipped installer. It adds no production bypass.
Invoke as root with a point name and storage.py path, with a real PTY so the
normal disk-selection and typed confirmation are still required.
"""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys

if len(sys.argv) != 3:
    raise SystemExit('Usage: vm_fault_inject.py POINT /path/to/storage.py')
point, source = sys.argv[1:]
if os.geteuid() != 0:
    raise SystemExit('This harness runs only as root inside a disposable VM')
serial = subprocess.run(['lsblk', '-dn', '-o', 'SERIAL', '/dev/vda'],
                        capture_output=True, text=True, check=True).stdout.strip()
if serial != 'TESTROOT':
    raise SystemExit('Refusing: /dev/vda is not the explicitly designated disposable test root')
spec = importlib.util.spec_from_file_location('storage_fault_candidate', source)
storage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(storage)


def crash():
    print('TEST: forced process death at ' + point, flush=True)
    os._exit(86)


real_save = storage.save
def save(state):
    real_save(state)
    if point == 'stage:' + state['stage']:
        crash()
    if point == 'journal-before-unit' and state['changes'] and not storage.UNIT.exists():
        crash()
storage.save = save

real_run = storage.run
def run(args, *rest, **kwargs):
    result = real_run(args, *rest, **kwargs)
    if point == 'after-mkfs' and args[0] == 'mkfs.ext4':
        crash()
    if point == 'after-mount' and args == ['systemctl', 'start', 'mnt-nosana.mount']:
        crash()
    return result
storage.run = run

real_atomic = storage.atomic
def atomic(path, *args, **kwargs):
    result = real_atomic(path, *args, **kwargs)
    labels = {
        storage.UNIT: 'unit', storage.GUARD: 'guard', storage.DATA / '.owner': 'marker',
        Path('/etc/docker/daemon.json'): 'docker-config',
        Path('/etc/containerd/config.toml'): 'containerd-config',
        Path('/etc/systemd/system/docker.service.d/20-nosana-storage.conf'): 'docker-dropin',
        Path('/etc/systemd/system/containerd.service.d/20-nosana-storage.conf'): 'containerd-dropin',
    }
    if path in labels and point == 'after:' + labels[path]:
        crash()
    return result
storage.atomic = atomic
storage.main([])
raise SystemExit('Fault point was not reached: ' + point)
