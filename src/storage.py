#!/usr/bin/env python3
"""Conservative, fresh-install storage wizard. No migration or force-format mode."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import uuid

MOUNT = Path('/mnt/nosana')
DATA = MOUNT / 'nosana-runtime'
STATE = Path('/var/lib/nosana-storage-setup/state.json')
UNIT = Path('/etc/systemd/system/mnt-nosana.mount')
GUARD = Path('/usr/local/libexec/nosana-storage-check')
COLUMNS = 'NAME,PATH,TYPE,SIZE,MODEL,SERIAL,WWN,RO,RM,TRAN,FSTYPE,UUID,LABEL,PARTUUID,PARTLABEL,MOUNTPOINTS,MAJ:MIN'


class Refusal(RuntimeError):
    pass


def run(args, allowed=(0,), **kwargs):
    result = subprocess.run(args, text=True, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, stdin=subprocess.DEVNULL, **kwargs)
    if result.returncode not in allowed:
        raise Refusal('{} failed: {}'.format(args[0], result.stderr.strip()))
    return result


def descendants(node):
    yield node
    for child in node.get('children', []):
        yield from descendants(child)


def references(node):
    refs = {node.get('path')}
    for key in ('uuid', 'label', 'partuuid', 'partlabel'):
        if node.get(key):
            refs.add(key.upper() + '=' + node[key])
    return refs


def candidates(tree, referenced=None, swap_paths=None, held=None):
    """Pure policy, independently tested. Any uncertainty means no eligibility."""
    referenced, swap_paths, held = map(lambda x: x or set(), (referenced, swap_paths, held))
    disks = tree.get('blockdevices', [])
    if not isinstance(disks, list):
        return [], [('*', 'Unreadable device inventory')]
    try:
        nodes = [n for disk in disks for n in descendants(disk)]
        if not any(d.get('type') == 'disk' and any('/' in (n.get('mountpoints') or [])
                   for n in descendants(d)) for d in disks):
            return [], [('*', 'Cannot establish the physical system disk')]
        identities = [d.get('wwn') or d.get('serial') for d in disks if d.get('type') == 'disk']
        uuids = [n['uuid'] for n in nodes if n.get('uuid')]
        good, bad = [], []
        for disk in disks:
            if disk.get('type') != 'disk':
                continue
            path = disk.get('path', '')
            group = list(descendants(disk))
            identity = disk.get('wwn') or disk.get('serial')
            reason = None
            if not re.fullmatch(r'/dev/(sd[a-z]+|vd[a-z]+|nvme\d+n\d+)', path):
                reason = 'Unsupported disk type'
            elif not identity or identities.count(identity) != 1 or not re.fullmatch(r'[A-Za-z0-9_.:+-]+', identity):
                reason = 'Missing, duplicate, or unsupported serial/WWN'
            elif any(not all(k in n for k in ('ro', 'rm', 'mountpoints', 'path', 'size', 'type', 'maj:min', 'fstype', 'uuid'))
                     or n['ro'] is None or n['rm'] is None
                     or n['ro'] not in (True, False, 0, 1) or n['rm'] not in (True, False, 0, 1)
                     or not isinstance(n['mountpoints'], list)
                     or not isinstance(n['size'], int) or n['size'] <= 0 for n in group):
                reason = 'Incomplete device safety observations'
            elif any(n.get('ro') or n.get('rm') for n in group) or disk.get('tran') == 'usb':
                reason = 'Read-only or removable/USB disk'
            elif any(n.get('mountpoints') for n in group if any(n.get('mountpoints') or [])):
                reason = 'Contains a mounted filesystem or active swap'
            elif any(n.get('path') in swap_paths or n.get('path') in held for n in group):
                reason = 'Disk is in use by swap or another block device'
            elif any(references(n) & referenced for n in group):
                reason = 'Referenced by fstab or crypttab'
            elif any(n.get('uuid') and uuids.count(n['uuid']) != 1 for n in group):
                reason = 'Duplicate filesystem UUID'
            elif any(n.get('type') != 'part' for n in group[1:]) or len(group) > 2:
                reason = 'Complex or multiple-partition layout'
            device = group[-1]
            if not reason:
                if len(group) == 1 and not disk.get('fstype') and not disk.get('uuid'):
                    mode = 'format'
                elif device.get('fstype') == 'ext4' and device.get('uuid') and device['uuid'].strip():
                    mode = 'reuse'
                else:
                    reason = 'Only an unused disk or a single ext4 filesystem is supported'
            if not reason and (not disk.get('size') or not disk.get('maj:min') or not device.get('path')):
                reason = 'Incomplete device identity'
            if reason:
                bad.append((path, reason))
            else:
                good.append(dict(disk=disk, device=device, mode=mode, identity=identity))
        return good, bad
    except (TypeError, KeyError, AttributeError):
        return [], [('*', 'Malformed device inventory')]


def inventory():
    run(['udevadm', 'settle', '--timeout=15'])
    tree = json.loads(run(['lsblk', '--json', '--bytes', '--paths', '--tree', '--output', COLUMNS]).stdout)
    refs, held = set(), set()
    for filename in ('/etc/fstab', '/etc/crypttab'):
        path = Path(filename)
        if path.exists():
            for line in path.read_text().splitlines():
                if line.lstrip().startswith('#'):
                    continue
                fields = line.split()
                if fields:
                    value = fields[1] if filename.endswith('crypttab') and len(fields) > 1 else fields[0]
                    # fstab escapes bytes; systemd's crypttab parser does not.
                    # Decode fstab once, then remove one tag-value quote pair.
                    if filename.endswith('fstab'):
                        value = os.fsdecode(re.sub(rb'\\([0-7]{3})', lambda match: bytes([int(match[1], 8)]), os.fsencode(value)))
                    if '=' in value:
                        key, val = value.split('=', 1)
                        if len(val) >= 2 and val[0] in ('"', "'") and val[-1] == val[0]:
                            val = val[1:-1]
                        value = key + '=' + val
                    refs.add(value)
                    if value.startswith('/dev/'):
                        refs.add(os.path.realpath(value))
    for node in (n for d in tree['blockdevices'] for n in descendants(d)):
        holder_dir = Path('/sys/class/block') / Path(node['path']).name / 'holders'
        if not holder_dir.is_dir():
            raise Refusal('Cannot inspect block-device holders')
        if any(holder_dir.iterdir()):
            held.add(node['path'])
    swap = run(['swapon', '--show', '--noheadings', '--raw', '--output', 'NAME']).stdout
    swaps = {os.path.realpath(re.sub(r'\\x([0-9a-fA-F]{2})', lambda m: chr(int(m[1], 16)), line))
             for line in swap.splitlines() if line}
    return candidates(tree, refs, swaps, held)


def probe(choice):
    device = choice['device']['path']
    result = run(['blkid', '-p', '-o', 'export', device], allowed=(0, 2))
    values = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
    signatures = json.loads(run(['wipefs', '--json', '--output', 'TYPE,UUID,OFFSET', device]).stdout)['signatures']
    if not isinstance(signatures, list) or any(not isinstance(s, dict) for s in signatures):
        raise Refusal('Unreadable filesystem signature inventory')
    if choice['mode'] == 'format':
        if result.returncode != 2 or values or signatures:
            raise Refusal('Disk has existing or ambiguous signatures; formatting refused')
    elif result.returncode != 0 or values.get('TYPE') != 'ext4' or values.get('UUID') != choice['device']['uuid']:
        raise Refusal('Filesystem identity changed or is not ext4')
    elif not signatures or any(s['type'] != 'ext4' for s in signatures):
        raise Refusal('Conflicting filesystem signatures')
    elif any(s.get('uuid') != values['UUID'] for s in signatures):
        raise Refusal('Filesystem probes disagree about UUID')
    if choice['mode'] == 'reuse':
        header = run(['dumpe2fs', '-h', device]).stdout
        fields = dict(line.split(':', 1) for line in header.splitlines() if ':' in line)
        if fields.get('Filesystem state', '').strip() != 'clean' or 'needs_recovery' in fields.get('Filesystem features', ''):
            raise Refusal('Existing ext4 filesystem is not clean. This wizard will not repair or replay it.')


def revalidate(choice):
    current, _ = inventory()
    if choice not in current:
        raise Refusal('The selected disk changed or became busy. Nothing more will be written.')
    probe(choice)


def safe_path(path):
    """Reject links and writable ancestors before root writes."""
    for item in [path] + list(path.parents):
        if item.is_symlink():
            raise Refusal('Refusing symlink: ' + str(item))
        if item.exists():
            info = item.stat()
            if info.st_uid != 0 or info.st_mode & 0o022:
                raise Refusal('Path is not exclusively root-managed: ' + str(item))


def atomic(path, data, mode=0o600, replace=True):
    safe_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix='.nosana-', dir=str(path.parent))
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if replace:
            os.replace(temp, path)
        else:
            # link() is atomic and fails if another process created the target.
            os.link(temp, path)
            os.unlink(temp)
        directory = os.open(str(path.parent), os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def save(state):
    atomic(STATE, (json.dumps(state, indent=2) + '\n').encode())


def stage(state, value):
    state['stage'] = value
    save(state)


def write_owned(state, path, content, mode=0o644):
    safe_path(path)
    if path.exists():
        raise Refusal('Existing configuration will not be overwritten: ' + str(path))
    data = content.encode()
    # Write intent before file creation. A kill between these operations is recoverable.
    state['changes'].append({'path': str(path), 'sha256': hashlib.sha256(data).hexdigest()})
    save(state)
    atomic(path, data, mode, replace=False)


def preflight():
    for command in ('lsblk', 'blkid', 'wipefs', 'udevadm', 'swapon', 'mkfs.ext4', 'dumpe2fs', 'systemctl', 'findmnt'):
        if not shutil.which(command):
            raise Refusal('Required Ubuntu tool missing: ' + command)
    if not Path('/run/systemd/system').is_dir():
        raise Refusal('This requires a native systemd-based Ubuntu installation')
    for name in ('docker', 'dockerd', 'containerd', 'podman', 'ctr', 'runc', 'nerdctl'):
        if shutil.which(name) or any((Path(base) / name).exists() for base in ('/usr/local/bin', '/usr/local/sbin', '/snap/bin')):
            raise Refusal('Existing container runtime detected. This wizard does not migrate existing installations.')
    if Path('/var/snap/docker').exists():
        raise Refusal('Existing Snap Docker data detected; migration is unsupported')
    for path in (Path('/var/lib/docker'), Path('/var/lib/containerd')):
        if path.is_symlink() or (path.exists() and any(path.iterdir())):
            raise Refusal('Existing runtime data found at ' + str(path))
    for path in (Path('/etc/docker/daemon.json'), Path('/etc/containerd/config.toml'), UNIT, GUARD):
        safe_path(path)
        if path.exists():
            raise Refusal('Existing configuration needs a reviewed migration: ' + str(path))
    for service in ('docker', 'containerd', 'docker.socket'):
        name = service if '.' in service else service + '.service'
        for base in ('/etc/systemd/system', '/run/systemd/system'):
            if (Path(base) / name).exists() or (Path(base) / (name + '.d')).exists():
                raise Refusal('Existing runtime systemd configuration: ' + name)
    safe_path(MOUNT)
    if MOUNT.exists() and (not MOUNT.is_dir() or any(MOUNT.iterdir())):
        raise Refusal('The managed mount directory must be empty: ' + str(MOUNT))
    mounts = json.loads(run(['findmnt', '--json', '--output', 'TARGET']).stdout)
    def targets(items):
        for item in items:
            yield item['target']
            yield from targets(item.get('children', []))
    if str(MOUNT) in set(targets(mounts.get('filesystems', []))):
        raise Refusal('The managed mount directory is already mounted')


def check_mounted(fs_uuid, token=None):
    result = run(['findmnt', '--json', '--mountpoint', str(MOUNT), '--output', 'TARGET,UUID,FSTYPE,OPTIONS'])
    mounts = json.loads(result.stdout).get('filesystems', [])
    if len(mounts) != 1:
        raise Refusal('Expected exactly one storage mount')
    mount = mounts[0]
    if (mount['target'] != str(MOUNT) or mount['uuid'] != fs_uuid or mount['fstype'] != 'ext4'
            or 'rw' not in mount['options'].split(',')):
        raise Refusal('Storage is missing, wrong, or read-only')
    if token is not None:
        safe_path(DATA)
        marker = DATA / '.owner'
        safe_path(marker)
        if marker.read_text() != token + '\n':
            raise Refusal('Storage ownership marker does not match')
        for name in ('docker', 'containerd'):
            safe_path(DATA / name)
            if not (DATA / name).is_dir():
                raise Refusal('Storage directory missing: ' + name)


def guard_source(fs_uuid, token):
    return '''#!/usr/bin/python3
import json, os, stat, subprocess, sys
from pathlib import Path
try:
    result = subprocess.run(['/usr/bin/findmnt', '--json', '--mountpoint', '/mnt/nosana',
                             '--output', 'TARGET,UUID,FSTYPE,OPTIONS'], check=True,
                            capture_output=True, text=True)
    mounts = json.loads(result.stdout)['filesystems']
    if len(mounts) != 1: raise RuntimeError('mount count')
    m = mounts[0]
    if m['target'] != '/mnt/nosana' or m['uuid'] != %r: raise RuntimeError('mount identity')
    if m['fstype'] != 'ext4' or 'rw' not in m['options'].split(','): raise RuntimeError('mount type/options')
    base = Path('/mnt/nosana/nosana-runtime')
    for p in [base, base / '.owner', base / 'docker', base / 'containerd']:
        s = p.lstat()
        if stat.S_ISLNK(s.st_mode) or s.st_uid != 0 or s.st_mode & 0o022: raise RuntimeError('unsafe storage path')
    if (base / '.owner').read_text() != %r: raise RuntimeError('storage marker')
except Exception as error:
    print('Nosana storage unavailable; refusing to write on the OS disk:', error, file=sys.stderr)
    sys.exit(1)
''' % (fs_uuid, token + '\n')


def configure(choice):
    revalidate(choice)
    preflight()
    state = dict(version=1, stage='approved', choice=choice, token=uuid.uuid4().hex, changes=[])
    save(state)
    try:
        device = choice['device']['path']
        if choice['mode'] == 'format':
            stage(state, 'formatting')
            # Pin the approved block device, not a pathname that could be replaced.
            with open(device, 'rb') as disk:
                fcntl.flock(disk, fcntl.LOCK_EX | fcntl.LOCK_NB)
                revalidate(choice)
                info = os.fstat(disk.fileno())
                expected = choice['device']['maj:min']
                if not stat.S_ISBLK(info.st_mode) or '{}:{}'.format(os.major(info.st_rdev), os.minor(info.st_rdev)) != expected:
                    raise Refusal('Pinned block-device identity mismatch')
                run(['mkfs.ext4', '-L', 'nosana-data', '/proc/self/fd/' + str(disk.fileno())],
                    pass_fds=(disk.fileno(),))
            run(['udevadm', 'settle', '--timeout=15'])
        else:
            revalidate(choice)
        fs_uuid = run(['blkid', '-p', '-s', 'UUID', '-o', 'value', device]).stdout.strip()
        if not re.fullmatch(r'[0-9a-fA-F-]{36}', fs_uuid):
            raise Refusal('Could not verify the prepared filesystem UUID')
        matches = run(['blkid', '-c', '/dev/null', '-t', 'UUID=' + fs_uuid, '-o', 'device']).stdout.splitlines()
        if matches != [device]:
            raise Refusal('Prepared filesystem UUID is not unique')
        state['uuid'] = fs_uuid
        stage(state, 'filesystem-ready')
        MOUNT.mkdir(mode=0o755, exist_ok=True)
        mount_unit = ('[Unit]\nDescription=Nosana dedicated Docker storage\nJobTimeoutSec=20s\n'
                      '[Mount]\nWhat=/dev/disk/by-uuid/' + fs_uuid + '\nWhere=' + str(MOUNT) +
                      '\nType=ext4\nOptions=defaults,nosuid,nodev\nTimeoutSec=15s\n')
        write_owned(state, UNIT, mount_unit)
        run(['systemctl', 'daemon-reload'])
        stage(state, 'mounting')
        run(['systemctl', 'start', 'mnt-nosana.mount'])
        check_mounted(fs_uuid)
        safe_path(MOUNT)
        if DATA.exists() or DATA.is_symlink():
            raise Refusal('This disk already contains nosana-runtime; it will not be replaced')
        # Record before mkdir so interruption can never lead to blind overwriting.
        stage(state, 'creating-data-directories')
        DATA.mkdir(mode=0o700)
        atomic(DATA / '.owner', (state['token'] + '\n').encode(), replace=False)
        for name in ('docker', 'containerd'):
            (DATA / name).mkdir(mode=0o700)
        write_owned(state, GUARD, guard_source(fs_uuid, state['token']), 0o755)
        for service in ('docker', 'containerd'):
            write_owned(state, Path('/etc/systemd/system') / (service + '.service.d/20-nosana-storage.conf'),
                        '[Unit]\nRequiresMountsFor=/mnt/nosana\nBindsTo=mnt-nosana.mount\n'
                        'After=mnt-nosana.mount\n[Service]\nExecStartPre=' + str(GUARD) + '\n')
        write_owned(state, Path('/etc/docker/daemon.json'),
                    json.dumps({'data-root': str(DATA / 'docker')}, indent=2) + '\n')
        write_owned(state, Path('/etc/containerd/config.toml'),
                    'version = 2\nroot = "' + str(DATA / 'containerd') + '"\nstate = "/run/containerd"\n')
        run(['systemctl', 'daemon-reload'])
        check_mounted(fs_uuid, state['token'])
        run([str(GUARD)])
        stage(state, 'complete')
        if choice['mode'] == 'reuse':
            print('Storage configured and verified. Existing files on the disk were preserved.')
        else:
            print('Selected disk formatted as requested; Docker and containerd storage configured and verified.')
    except BaseException:
        print('\nSetup did not complete. The OS boot configuration was not changed.', file=sys.stderr)
        print('Run this same installer with --recover-storage before trying again.', file=sys.stderr)
        print('Recovery removes only recorded configuration; it cannot undo formatting.', file=sys.stderr)
        raise


def recover(state):
    if state['stage'] == 'recovered':
        print('This interrupted transaction was already recovered. No changes made.')
        return
    if state['stage'] == 'complete':
        raise Refusal('Completed storage may contain live data. Automatic rollback is not allowed.')
    if any(shutil.which(name) for name in ('dockerd', 'containerd')):
        raise Refusal('A runtime is installed; recovery needs review to protect possible live data.')
    # Check all files first, before removing anything. Never overwrite later admin edits.
    for change in state['changes']:
        path = Path(change['path'])
        safe_path(path)
        if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest() != change['sha256']:
            raise Refusal('Configuration changed since setup; preserving it: ' + str(path))
    mounted = run(['findmnt', '--mountpoint', str(MOUNT)], allowed=(0, 1)).returncode == 0
    if not mounted and state['stage'] == 'creating-data-directories':
        # After reboot the dependency-only mount may be inactive. Preserve the
        # journal on a missing disk so recovery can be retried when it returns.
        if not UNIT.exists():
            raise Refusal('The recorded mount unit is missing; recovery needs review')
        run(['systemctl', 'daemon-reload'])
        run(['systemctl', 'start', 'mnt-nosana.mount'])
        mounted = True
    if mounted:
        check_mounted(state['uuid'])
        marker = DATA / '.owner'
        if state['stage'] in ('approved', 'formatting', 'filesystem-ready', 'mounting'):
            pass  # We did not create a data directory. Preserve anything already there.
        elif marker.is_file() and not marker.is_symlink() and marker.read_text() == state['token'] + '\n':
            # rmdir never removes nonempty directories. Only our exact marker is unlinked.
            for name in ('docker', 'containerd'):
                directory = DATA / name
                safe_path(directory)
                if directory.exists():
                    directory.rmdir()
            if set(p.name for p in DATA.iterdir()) != {'.owner'}:
                raise Refusal('Unexpected data appeared; leaving it intact')
            marker.unlink()
            DATA.rmdir()
        elif DATA.is_dir() and not DATA.is_symlink() and not any(DATA.iterdir()):
            DATA.rmdir()  # Interrupted between our mkdir and marker creation.
        elif DATA.exists() or DATA.is_symlink():
            raise Refusal('Unmarked data directory needs review; leaving it intact')
        run(['systemctl', 'stop', 'mnt-nosana.mount'])
    stage(state, 'recovering')
    for change in reversed(state['changes']):
        path = Path(change['path'])
        if path.exists():
            path.unlink()
        if path.parent.name.endswith('.service.d') and path.parent.exists() and not any(path.parent.iterdir()):
            path.parent.rmdir()
    run(['systemctl', 'daemon-reload'])
    stage(state, 'recovered')
    print('Recorded configuration removed. Disk filesystems and existing files were not erased.')
    print('A filesystem created during the previous attempt remains on the selected disk.')


def existing_state():
    safe_path(STATE)
    if STATE.exists():
        state = json.loads(STATE.read_text())
        if state.get('version') != 1:
            raise Refusal('Unknown storage journal version')
        return state
    return None


def verify_configuration(state):
    for change in state['changes']:
        path = Path(change['path'])
        safe_path(path)
        if str(path) == '/etc/docker/daemon.json':
            # NVIDIA Toolkit adds its runtime here; keep and accept that change.
            if json.loads(path.read_text()).get('data-root') != str(DATA / 'docker'):
                raise Refusal('Docker storage configuration changed')
        elif not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != change['sha256']:
            raise Refusal('Managed storage configuration changed or is missing: ' + str(path))


def verify_runtime():
    if shutil.which('containerd'):
        dump = run(['containerd', 'config', 'dump']).stdout
        dump = re.split(r'(?m)^\s*\[', dump, maxsplit=1)[0]  # Only the TOML root table.
        match = re.search(r'''(?m)^root\s*=\s*(["'])([^\n]+)\1\s*$''', dump)
        if not match or match[2] != str(DATA / 'containerd'):
            raise Refusal('containerd effective configuration does not use the selected disk')
    if shutil.which('docker'):
        root = run(['docker', 'info', '--format', '{{.DockerRootDir}}']).stdout.strip()
        if root != str(DATA / 'docker'):
            raise Refusal('The running Docker daemon does not use the selected disk')


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv not in ([], ['--list'], ['--recover'], ['--verify'], ['--check-fresh']):
        raise Refusal('Usage: storage.py [--list | --recover | --verify | --check-fresh]')
    if os.geteuid() != 0:
        raise Refusal('Storage setup needs sudo privileges')
    os.environ['PATH'] = '/usr/sbin:/usr/bin:/sbin:/bin'
    os.environ['LC_ALL'] = 'C'
    lock = os.open('/run/lock/nosana-storage.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise Refusal('Another storage wizard is already running')
    try:
        state = existing_state()
        if argv == ['--check-fresh']:
            if state and state['stage'] != 'recovered':
                raise Refusal('A second-disk transaction already exists. Verify or recover it before changing storage choice.')
            preflight()
            print('Fresh runtime installation preflight passed.')
            return
        if argv == ['--recover']:
            if not state:
                raise Refusal('No storage transaction to recover')
            return recover(state)
        if state and state['stage'] == 'complete':
            verify_configuration(state)
            if argv == ['--list']:
                print('Second-disk storage is already configured for UUID ' + state['uuid'])
                return
            run(['systemctl', 'start', 'mnt-nosana.mount'])
            check_mounted(state['uuid'], state['token'])
            run([str(GUARD)])
            verify_runtime()
            print('Previously configured second-disk storage verified; no formatting or migration performed.')
            return
        if state and state['stage'] != 'recovered':
            raise Refusal('An interrupted setup exists. Run the installer with --recover-storage first.')
        if argv == ['--verify']:
            raise Refusal('No completed second-disk setup found')
        preflight()
        choices, rejected = inventory()
        eligible = []
        for choice in choices:
            try:
                probe(choice)
                eligible.append(choice)
            except Refusal as error:
                rejected.append((choice['disk']['path'], str(error)))
        print('\nAvailable secondary disks (system disks and busy disks are excluded):')
        for number, choice in enumerate(eligible, 1):
            disk = choice['disk']
            action = 'ERASE and create ext4' if choice['mode'] == 'format' else 'Reuse ext4; preserve existing files'
            print('{}: {} | {:.1f} GiB | {} | ID {} | {}'.format(
                number, disk['path'], int(disk['size']) / 2**30, disk.get('model') or 'unknown model', choice['identity'], action))
        for path, reason in rejected:
            print('Excluded {}: {}'.format(path, reason))
        if argv == ['--list']:
            return
        if not eligible:
            raise Refusal('No safe supported secondary disk is available. Nothing was changed.')
        if not sys.stdin.isatty():
            raise Refusal('An interactive terminal is required. Download the script, then run it with bash.')
        selected = input('Choose a disk number, or press Enter to cancel: ').strip()
        if not selected:
            raise Refusal('Cancelled; nothing was changed')
        if not selected.isdecimal() or not 1 <= int(selected) <= len(eligible):
            raise Refusal('Invalid selection; nothing was changed')
        choice = eligible[int(selected) - 1]
        if choice['mode'] == 'format':
            print('WARNING: formatting destroys any data on this selected disk, including data our probes cannot recognize.')
            phrase = 'ERASE ' + choice['identity']
        else:
            print('This will add dedicated runtime directories; it will not format the filesystem.')
            phrase = 'USE ' + choice['identity']
        if input('Type exactly "{}" to continue: '.format(phrase)) != phrase:
            raise Refusal('Confirmation did not match; nothing was changed')
        configure(choice)
    finally:
        os.close(lock)


if __name__ == '__main__':
    try:
        main()
    except (Refusal, OSError, ValueError, KeyError, EOFError) as error:
        print('STOP: ' + str(error), file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print('\nCancelled. If setup had started, use --recover-storage before rerunning.', file=sys.stderr)
        sys.exit(130)
