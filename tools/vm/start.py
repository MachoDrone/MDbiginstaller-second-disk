#!/usr/bin/env python3
"""Start only file-backed disposable guest disks from this test directory."""
from pathlib import Path
import os, subprocess, sys
p=Path(__file__).resolve().parent
pid=p/'vm.pid'
if pid.exists():
 try:
  os.kill(int(pid.read_text()),0)
  raise SystemExit('A VM is already running; refusing a duplicate launch.')
 except ProcessLookupError: pid.unlink()
root=(p/(sys.argv[1] if len(sys.argv)>1 else 'root.qcow2')).resolve()
extras=sys.argv[2:] if len(sys.argv)>2 else ['data.qcow2']
if extras == ['NONE']: extras=[]
for disk in [root]+[(p/x).resolve() for x in extras]:
 if disk.parent != p or not disk.is_file(): raise SystemExit('Only existing image files directly under test directory allowed')
q=p/'qemu-root'
port=os.environ.get('NOSANA_VM_PORT','22292')
if not port.isdecimal() or not 1024 <= int(port) <= 65535: raise SystemExit('Invalid localhost port')
env=dict(os.environ, LD_LIBRARY_PATH=str(q/'usr/lib/x86_64-linux-gnu'),QEMU_MODULE_DIR=str(q/'usr/lib/x86_64-linux-gnu/qemu'))
cmd=[str(q/'usr/bin/qemu-system-x86_64'),'-machine','q35,accel=kvm','-cpu','max','-m','1536','-smp','2','-display','none','-vga','none','-bios',str(q/'usr/share/seabios/bios-256k.bin'),'-L',str(q/'usr/share/qemu'),'-drive',f'file={root},format=qcow2,if=none,id=rootdisk,cache=writeback','-device','virtio-blk-pci,drive=rootdisk,serial=TESTROOT','-drive',f'file={p}/seed.iso,format=raw,media=cdrom,readonly=on','-netdev',f'user,id=n0,hostfwd=tcp:127.0.0.1:{port}-:22','-device','virtio-net-pci,netdev=n0,romfile=','-serial',f'file:{p}/serial.log','-monitor',f'unix:{p}/monitor.sock,server,nowait','-daemonize','-pidfile',str(pid)]
for i,disk in enumerate(extras):
 bus = 'nvme' if os.environ.get('NOSANA_TEST_DISK_BUS') == 'nvme' else 'virtio-blk-pci'
 cmd += ['-drive',f'file={p/disk},format=qcow2,if=none,id=data{i},cache=writeback','-device',f'{bus},drive=data{i},serial=TESTDATA{i+1}']
subprocess.run(cmd,env=env,check=True)
print('Started isolated QEMU PID '+pid.read_text().strip())
