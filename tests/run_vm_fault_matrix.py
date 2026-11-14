#!/usr/bin/env python3
"""Real crash/recovery tests in the pre-created disposable QEMU VM only.

Requires /tmp/nosana-installer-fault-vm helpers and stopped golden snapshots
fault-clean-root.qcow2 and fault-clean-data.qcow2. No host block device, sudo,
package manager, or service is invoked. Every guest mutation verifies its
TESTROOT / TESTDATA1 serials. Evidence is written into the VM scratch directory.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pexpect

VM = Path('/tmp/nosana-installer-fault-vm')
EVIDENCE = VM / 'evidence'
SOURCE_SHA = 'f63aeb92c00cbcddc955ec29213f95f3e264ca9e788bfd8c8ac70d65ea20fd50'
POINTS = [
    'stage:approved', 'stage:formatting', 'after-mkfs', 'stage:filesystem-ready',
    'journal-before-unit', 'after:unit', 'stage:mounting', 'after-mount',
    'stage:creating-data-directories', 'after:marker', 'after:guard',
    'after:docker-dropin', 'after:containerd-dropin', 'after:docker-config',
    'after:containerd-config',
]
REBOOT_POINTS = {'after:marker', 'after:containerd-config'}

GUEST_FIXTURE = r'''#!/usr/bin/env python3
import hashlib, json, pathlib, subprocess, sys
def run(args, allowed=(0,)):
    r=subprocess.run(args,capture_output=True,text=True)
    if r.returncode not in allowed: raise RuntimeError(repr(args)+': '+r.stderr)
    return r
if run(['lsblk','-dn','-o','SERIAL','/dev/vda']).stdout.strip()!='TESTROOT':
    raise SystemExit('Wrong guest root identity')
if run(['lsblk','-dn','-o','SERIAL','/dev/vdb']).stdout.strip()!='TESTDATA1':
    raise SystemExit('Wrong guest test disk identity')
mode=sys.argv[1]
if mode=='os-proof':
    print(run(['sha256sum','/etc/fstab','/root/fault-os-preserved']).stdout,end='')
    print(run(['sfdisk','--dump','/dev/vda']).stdout,end='')
    print('ROOT_UUID='+run(['blkid','-p','-s','UUID','-o','value','/dev/vda1']).stdout.strip())
elif mode=='prepare-sentinel':
    probe=run(['blkid','-p','-o','export','/dev/vdb'],(0,2))
    if probe.returncode==2:
        print(json.dumps({'prepared_uuid':None,'sentinel':False}))
        raise SystemExit(0)
    info=dict(line.split('=',1) for line in probe.stdout.splitlines() if '=' in line)
    if info.get('TYPE')!='ext4': raise SystemExit('Unexpected test disk filesystem')
    mounted=run(['findmnt','-rn','-S','/dev/vdb','-o','TARGET'],(0,1)).stdout.strip()
    owned=False
    if not mounted:
        mounted='/mnt/fault-fixture'
        pathlib.Path(mounted).mkdir(exist_ok=True)
        run(['mount','/dev/vdb',mounted])
        owned=True
    if mounted not in ('/mnt/nosana','/mnt/fault-fixture'): raise SystemExit('Unexpected mount')
    p=pathlib.Path(mounted)/'fault-existing-file'
    p.write_bytes(b'Preserve this file across recovery and reuse.\n')
    run(['sync'])
    if owned: run(['umount',mounted])
    print(json.dumps({'prepared_uuid':info['UUID'],'sentinel':True}))
elif mode=='journal-summary':
    state=json.loads(pathlib.Path('/var/lib/nosana-storage-setup/state.json').read_text())
    print(json.dumps({'stage':state['stage'],'uuid':state.get('uuid'),
                      'recorded_paths':[x['path'] for x in state['changes']]}))
elif mode=='assert-recovered':
    state=json.loads(pathlib.Path('/var/lib/nosana-storage-setup/state.json').read_text())
    assert state['stage']=='recovered'
    for change in state['changes']:
        p=pathlib.Path(change['path'])
        assert not p.exists() and not p.is_symlink(),str(p)
    assert not pathlib.Path('/etc/docker/daemon.json').exists()
    assert not pathlib.Path('/etc/containerd/config.toml').exists()
    print('PASS recovered; all recorded configuration absent')
elif mode=='assert-complete':
    state=json.loads(pathlib.Path('/var/lib/nosana-storage-setup/state.json').read_text())
    assert state['stage']=='complete'
    actual=run(['blkid','-p','-s','UUID','-o','value','/dev/vdb']).stdout.strip()
    assert actual==state['uuid']
    expected=sys.argv[2]
    if expected!='NONE':
        assert actual==expected,'Existing prepared filesystem was reformatted'
        assert pathlib.Path('/mnt/nosana/fault-existing-file').read_bytes()==b'Preserve this file across recovery and reuse.\n'
    print('PASS complete; UUID='+actual+'; existing_filesystem_preserved='+str(expected!='NONE'))
else:
    raise SystemExit('Unknown fixture mode')
'''


def command(args, *, check=True, timeout=180):
    result = subprocess.run([str(a) for a in args], capture_output=True, text=True, timeout=timeout)
    if check and result.returncode:
        raise RuntimeError(repr(args) + '\n' + result.stdout + result.stderr)
    return result


def ssh(text, **kwargs):
    return command([VM / 'ssh', text], **kwargs)


def fixture(mode):
    return ssh('sudo python3 /home/tester/fault_fixture.py ' + mode).stdout


def stopped():
    pidfile = VM / 'vm.pid'
    if pidfile.exists():
        try:
            os.kill(int(pidfile.read_text()), 0)
        except ProcessLookupError:
            return
        raise RuntimeError('Refusing to reset images while QEMU is running')


def interactive(point, *, rerun=False, reuse=False):
    name = point.replace(':', '-') + ('-rerun' if rerun else '-crash')
    remote = 'sudo python3 /home/tester/storage.py' if rerun else (
        'sudo python3 /home/tester/vm_fault_inject.py ' + point + ' /home/tester/storage.py')
    with (EVIDENCE / ('fault-' + name + '.txt')).open('w') as log:
        child = pexpect.spawn(str(VM / 'ssh'), ['-tt', remote], encoding='utf-8', timeout=120)
        child.logfile = log
        child.expect('Choose a disk number, or press Enter to cancel: ')
        child.sendline('1')
        child.expect('to continue: ')
        child.sendline(('USE ' if reuse else 'ERASE ') + 'TESTDATA1')
        child.expect(pexpect.EOF)
        child.close()
        expected = 0 if rerun else 86
        if child.exitstatus != expected:
            raise RuntimeError(name + ' returned ' + str(child.exitstatus) + ', expected ' + str(expected))
    return child.exitstatus


def reboot():
    before = ssh('cat /proc/sys/kernel/random/boot_id').stdout.strip()
    ssh('sudo reboot', check=False)
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        result = ssh('cat /proc/sys/kernel/random/boot_id', check=False, timeout=15)
        if result.returncode == 0 and result.stdout.strip() and result.stdout.strip() != before:
            return
        time.sleep(2)
    raise RuntimeError('Guest did not reboot')


def main():
    if len(sys.argv) > 2 or (len(sys.argv) == 2 and sys.argv[1] not in POINTS + ['--remaining']):
        raise SystemExit('Usage: run_vm_fault_matrix.py [one-point-name | --remaining]')
    points = [sys.argv[1]] if len(sys.argv) == 2 else POINTS
    EVIDENCE.mkdir(exist_ok=True)
    report = []
    if points == ['--remaining']:
        report = json.loads((EVIDENCE / 'fault-matrix-results.json').read_text())
        if any(item['source_sha256'] != SOURCE_SHA for item in report):
            raise RuntimeError('Existing evidence belongs to a different candidate')
        completed_points = {item['point'] for item in report}
        points = [point for point in POINTS if point not in completed_points]
    started = time.monotonic()
    for point in points:
        stopped()
        for kind in ('root', 'data'):
            source = VM / ('fault-clean-' + kind + '.qcow2')
            target = VM / (kind + '.qcow2')
            if not source.is_file() or source.is_symlink() or target.is_symlink():
                raise RuntimeError('Unexpected snapshot path')
            command(['cp', '--reflink=auto', str(source), str(target)])
        command([VM / 'start.py', 'root.qcow2', 'data.qcow2'])
        try:
            command([VM / 'wait_guest.py'])
            actual_sha = ssh('sha256sum /home/tester/storage.py').stdout.split()[0]
            if actual_sha != SOURCE_SHA:
                raise RuntimeError('Candidate source differs from frozen hash')
            baseline = fixture('os-proof')
            if baseline != (EVIDENCE / 'fault-os-baseline.txt').read_text():
                raise RuntimeError('Golden OS proof differs')
            status = interactive(point)
            journal = json.loads(fixture('journal-summary'))
            data = json.loads(fixture('prepare-sentinel'))
            if point in REBOOT_POINTS:
                reboot()
            recovery = ssh('sudo python3 /home/tester/storage.py --recover').stdout
            repeated = ssh('sudo python3 /home/tester/storage.py --recover').stdout
            checks = fixture('assert-recovered')
            if fixture('os-proof') != baseline:
                raise RuntimeError('OS proof changed during failure/recovery')
            interactive(point, rerun=True, reuse=data['prepared_uuid'] is not None)
            verify = ssh('sudo python3 /home/tester/storage.py --verify').stdout
            complete = fixture('assert-complete ' + (data['prepared_uuid'] or 'NONE'))
            if fixture('os-proof') != baseline:
                raise RuntimeError('OS proof changed after successful rerun')
            item = dict(point=point, crash_exit=status, journal_stage=journal['stage'],
                        prepared_uuid=data['prepared_uuid'], rebooted_before_recovery=point in REBOOT_POINTS,
                        repeated_recovery='pass', os_preserved=True, rerun='pass', source_sha256=SOURCE_SHA)
            report.append(item)
            (EVIDENCE / ('fault-' + point.replace(':', '-') + '-summary.txt')).write_text(
                json.dumps(item, indent=2) + '\n' + recovery + repeated + checks + verify + complete)
            print('PASS ' + point + ' | OS unchanged | recovery twice | rerun | prepared UUID retained=' +
                  str(data['prepared_uuid'] is not None), flush=True)
        finally:
            command([VM / 'stop.py'])
        (EVIDENCE / 'fault-matrix-results.json').write_text(json.dumps(report, indent=2) + '\n')
    elapsed = time.monotonic() - started
    print('PASS ' + str(len(points)) + ' new fault boundaries in ' + str(round(elapsed, 1)) +
          ' seconds; ' + str(len(report)) + ' total recorded', flush=True)


if __name__ == '__main__':
    main()
