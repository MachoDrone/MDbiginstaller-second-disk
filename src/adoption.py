"""Adopt an existing dedicated ext4 mount; never format or unmount it."""
import base64
import copy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import uuid

MOUNT = Path('/var/lib/docker')
DATA = MOUNT / 'nosana-runtime'
FSTAB = Path('/etc/fstab')
MOUNT_NAME = 'var-lib-docker.mount'
MOUNT_DROPIN = Path('/etc/systemd/system/var-lib-docker.mount.d/20-nosana-storage.conf')
STORE_NAMES = {'containers', 'image', 'volumes', 'overlay2', 'overlay', 'aufs', 'btrfs',
               'zfs', 'vfs', 'devicemapper', 'buildkit', 'plugins', 'network', 'runtimes',
               'swarm', 'engine-id', 'key.json', 'containerd', 'graph', 'trust'}


def decode(value, fstab=True):
    if fstab:
        value = os.fsdecode(re.sub(rb'\\([0-7]{3})', lambda m: bytes([int(m[1], 8)]), os.fsencode(value)))
    if '=' in value:
        key, val = value.split('=', 1)
        if len(val) >= 2 and val[0] in ('"', "'") and val[-1] == val[0]:
            val = val[1:-1]
        value = key + '=' + val
    return value


def source_matches(source, refs):
    return source in refs or (source.startswith('/dev/') and os.path.realpath(source) in refs)


def plan_fstab(text, refs, fs_uuid):
    if not re.fullmatch(r'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}', fs_uuid):
        raise ValueError('Invalid filesystem UUID')
    lines = text.splitlines(keepends=True)
    selected = []
    for i, line in enumerate(lines):
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        fields = line.split()
        if len(fields) < 2:
            raise ValueError('Cannot safely interpret an fstab entry')
        source, target = decode(fields[0]), decode(fields[1])
        normal_target = os.path.normpath(target)
        if normal_target == str(MOUNT) and target != str(MOUNT):
            raise ValueError('Noncanonical fstab target conflicts with Docker storage')
        if normal_target.startswith(str(MOUNT) + '/'):
            raise ValueError('Nested fstab mount conflicts with Docker storage')
        if source_matches(source, refs) or target == str(MOUNT):
            if target != str(MOUNT) or not source_matches(source, refs):
                raise ValueError('Conflicting fstab source or target')
            selected.append(i)
    if len(selected) > 1:
        raise ValueError('Duplicate fstab references to the selected disk')
    required = ['nofail', 'x-systemd.device-timeout=10s', 'x-systemd.mount-timeout=15s']
    if not selected:
        if text and not text.endswith('\n'):
            text += '\n'
        return text + 'UUID=' + fs_uuid + ' ' + str(MOUNT) + ' ext4 defaults,' + ','.join(required) + ' 0 2\n'
    i = selected[0]
    match = re.match(r'^(\s*)(\S+)(\s+)(\S+)(\s+)(\S+)(\s+)(\S+)([\s\S]*)$', lines[i])
    if not match or match[6] != 'ext4':
        raise ValueError('Only a simple ext4 fstab entry can be adopted')
    tail = match[9].split('#', 1)[0].split()
    if len(tail) > 2 or any(not re.fullmatch(r'[0-2]', field) for field in tail):
        raise ValueError('Unsupported fstab dump or filesystem-check fields')
    options = match[8].split(',')
    allowed = {'defaults', 'rw', 'relatime', 'noatime', 'nodiratime', 'lazytime', 'nosuid',
               'nodev', 'discard', 'nodiscard', 'auto', 'noauto', 'nofail', 'async', 'sync',
               'dirsync', 'errors=remount-ro'}
    for option in options:
        if option not in allowed and not option.startswith(('x-systemd.device-timeout=', 'x-systemd.mount-timeout=')):
            raise ValueError('Unsupported mount option: ' + option)
    # Keep existing option order; only replace the two bounded timeouts.
    options = [o for o in options if not o.startswith(('x-systemd.device-timeout=', 'x-systemd.mount-timeout='))]
    if 'nofail' not in options:
        options.append('nofail')
    options += required[1:]
    lines[i] = (match[1] + 'UUID=' + fs_uuid + match[3] + match[4] + match[5] +
                match[6] + match[7] + ','.join(options) + match[9])
    return ''.join(lines)


def assess(snapshot, policy):
    mount = snapshot['mount']
    if (mount.get('target') != str(MOUNT) or mount.get('fstype') != 'ext4'
            or mount.get('fsroot') != '/' or 'rw' not in mount.get('options', '').split(',')
            or any(o in mount.get('options', '').split(',') for o in ('ro', 'bind', 'rbind'))):
        raise ValueError('Adoption requires an exact writable whole ext4 mount at /var/lib/docker')
    if snapshot.get('nested_mounts'):
        raise ValueError('Nested mounts are unsupported')
    tree = copy.deepcopy(snapshot['tree'])
    found = []
    parents = []
    def walk(node):
        yield node
        for child in node.get('children', []):
            yield from walk(child)
    for disk in tree['blockdevices']:
        for node in walk(disk):
            if str(MOUNT) in (node.get('mountpoints') or []):
                found.append(node)
                parents.append(disk)
    if len(found) != 1:
        raise ValueError('Cannot identify exactly one mounted block device')
    node = found[0]
    if (node.get('uuid') != mount.get('uuid') or node.get('maj:min') != mount.get('maj:min')
            or os.path.realpath(mount.get('source', '')) != node.get('path')
            or [p for p in node['mountpoints'] if p] != [str(MOUNT)]):
        raise ValueError('Mount device, UUID, or mount usage is ambiguous')
    refs = {node['path']}
    for key in ('uuid', 'label', 'partuuid', 'partlabel'):
        if node.get(key):
            refs.add(key.upper() + '=' + node[key])
    parent_refs = {parents[0]['path']} - refs
    for key in ('uuid', 'label', 'partuuid', 'partlabel'):
        if parents[0].get(key):
            parent_refs.add(key.upper() + '=' + parents[0][key])
    parent_refs -= refs
    for line in snapshot['fstab'].splitlines():
        fields = line.split()
        if fields and not line.lstrip().startswith('#') and source_matches(decode(fields[0]), parent_refs):
            raise ValueError('Physical parent disk is referenced by fstab')
    for line in snapshot.get('crypttab', '').splitlines():
        fields = line.split()
        if fields and not line.lstrip().startswith('#') and len(fields) > 1 and source_matches(decode(fields[1], False), refs | parent_refs):
            raise ValueError('Selected disk is referenced by crypttab')
    after = plan_fstab(snapshot['fstab'], refs, node['uuid'])
    # Reuse rc1's physical ancestry, uniqueness and unsupported-layout policy.
    # Only this exact, independently verified mount is temporarily cleared.
    node['mountpoints'] = [None]
    choices, rejected = policy(tree, referenced=set(), swap_paths=snapshot.get('swaps', set()), held=snapshot.get('held', set()))
    matches = [c for c in choices if c['device']['path'] == node['path'] and c['mode'] == 'reuse']
    if len(matches) != 1:
        raise ValueError('Prepared mount is not a safe separate disk: ' + str(rejected))
    choice = matches[0]
    choice.update(mode='adopt', uuid=node['uuid'], fstab_before=snapshot['fstab'], fstab_after=after, mount=mount)
    return choice


def active_mount(s):
    result = s.run(['findmnt', '--json', '--mountpoint', str(MOUNT), '--output',
                    'TARGET,SOURCE,UUID,FSTYPE,OPTIONS,FSROOT,MAJ:MIN'], allowed=(0, 1))
    if result.returncode == 1:
        return None
    mounts = json.loads(result.stdout).get('filesystems', [])
    if len(mounts) != 1:
        raise ValueError('Ambiguous Docker mount')
    return mounts[0]


def inspect(s):
    mount = active_mount(s)
    if mount is None:
        return None
    s.safe_path(MOUNT)
    s.safe_path(FSTAB)
    s.preflight(adopting=True)
    for base in ('/etc/systemd/system', '/run/systemd/system', '/usr/lib/systemd/system', '/lib/systemd/system'):
        for name in (MOUNT_NAME, MOUNT_NAME + '.d', 'var-lib-docker.automount', 'var-lib-docker.automount.d'):
            path = Path(base) / name
            if path.exists() or path.is_symlink():
                raise ValueError('Existing custom mount configuration is unsupported: ' + str(path))
    if DATA.exists() or DATA.is_symlink():
        raise ValueError('Existing nosana-runtime directory will not be replaced')
    for path in MOUNT.iterdir():
        if path.name in STORE_NAMES or path.name.startswith('io.containerd.'):
            raise ValueError('Existing runtime store found: ' + str(path))
    s.run(['udevadm', 'settle', '--timeout=15'])
    tree = json.loads(s.run(['lsblk', '--json', '--bytes', '--paths', '--tree', '--output', s.COLUMNS]).stdout)
    held = set()
    for node in (n for d in tree['blockdevices'] for n in s.descendants(d)):
        path = Path('/sys/class/block') / Path(node['path']).name / 'holders'
        if not path.is_dir():
            raise ValueError('Cannot inspect disk holders')
        if any(path.iterdir()):
            held.add(node['path'])
    swap = s.run(['swapon', '--show', '--noheadings', '--raw', '--output', 'NAME']).stdout
    swaps = {os.path.realpath(line) for line in swap.splitlines() if line}
    all_mounts = json.loads(s.run(['findmnt', '--json', '--list', '--output', 'TARGET']).stdout)['filesystems']
    snapshot = dict(tree=tree, mount=mount, fstab=FSTAB.read_bytes().decode('utf-8'),
                    crypttab=Path('/etc/crypttab').read_text() if Path('/etc/crypttab').exists() else '',
                    held=held, swaps=swaps, nested_mounts=[m for m in all_mounts if m['target'].startswith(str(MOUNT) + '/')])
    choice = assess(snapshot, s.candidates)
    s.probe(choice)  # Identity/signatures only; a mounted ext4 journal normally needs_recovery.
    header = s.run(['dumpe2fs', '-h', choice['device']['path']]).stdout
    fields = dict(line.split(':', 1) for line in header.splitlines() if ':' in line)
    if fields.get('Filesystem state', '').strip() != 'clean':
        raise ValueError('Mounted filesystem reports an error state')
    return choice


def replace_fstab(s, state, restore=False):
    record = state['fstab']
    before, after = base64.b64decode(record['before']), base64.b64decode(record['after'])
    expected, wanted = (after, before) if restore else (before, after)
    s.safe_path(FSTAB)
    with FSTAB.open('rb') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        current = stream.read()
        info = os.fstat(stream.fileno())
        current_info = FSTAB.stat()
        if (current not in (expected, wanted) or info.st_ino != current_info.st_ino
                or stat.S_IMODE(info.st_mode) != record['mode'] or info.st_gid != record['gid']):
            raise ValueError('fstab changed since planning; preserving administrator edits')
        if current == wanted:
            return
        s.atomic(FSTAB, wanted, record['mode'])
        os.chown(FSTAB, 0, record['gid'])


def guard_source(s, fs_uuid, token):
    source = s.guard_source(fs_uuid, token).replace('/mnt/nosana', str(MOUNT))
    source = source.replace('TARGET,UUID,FSTYPE,OPTIONS', 'TARGET,UUID,FSTYPE,OPTIONS,FSROOT')
    return source.replace("if m['fstype'] != 'ext4'", "if m.get('fsroot') != '/' or m['fstype'] != 'ext4'")


def check(s, state, owned=True):
    mount = active_mount(s)
    if (not mount or mount.get('uuid') != state['uuid'] or mount.get('fstype') != 'ext4'
            or mount.get('fsroot') != '/' or 'rw' not in mount.get('options', '').split(',')):
        raise ValueError('Adopted storage is missing, wrong, or read-only')
    if owned:
        for path in (DATA, DATA / '.owner', DATA / 'docker', DATA / 'containerd'):
            s.safe_path(path)
        for path in (DATA, DATA / 'docker', DATA / 'containerd'):
            if not path.is_dir():
                raise ValueError('Required adopted storage directory is missing')
        if (DATA / '.owner').read_text() != state['token'] + '\n':
            raise ValueError('Adopted storage marker changed')


def configure(s, choice):
    if inspect(s) != choice:
        raise ValueError('Prepared disk or mount configuration changed; nothing was written')
    info = FSTAB.stat()
    before, after = choice['fstab_before'].encode(), choice['fstab_after'].encode()
    state = dict(version=2, mode='adopt', stage='adopt-approved', choice=choice, uuid=choice['uuid'],
                 token=uuid.uuid4().hex, changes=[], fstab=dict(before=base64.b64encode(before).decode(),
                 after=base64.b64encode(after).decode(), before_sha256=hashlib.sha256(before).hexdigest(),
                 after_sha256=hashlib.sha256(after).hexdigest(), mode=stat.S_IMODE(info.st_mode), gid=info.st_gid))
    s.save(state)
    try:
        replace_fstab(s, state)
        s.stage(state, 'adopt-fstab-written')
        s.write_owned(state, MOUNT_DROPIN, '[Unit]\nJobTimeoutSec=20s\n[Mount]\nTimeoutSec=15s\n')
        s.run(['systemctl', 'daemon-reload'])
        check(s, state, owned=False)
        s.stage(state, 'adopt-creating-data')
        DATA.mkdir(mode=0o700)
        s.atomic(DATA / '.owner', (state['token'] + '\n').encode(), replace=False)
        for name in ('docker', 'containerd'):
            (DATA / name).mkdir(mode=0o700)
        s.write_owned(state, s.GUARD, guard_source(s, state['uuid'], state['token']), 0o755)
        for name in ('docker', 'containerd'):
            s.write_owned(state, Path('/etc/systemd/system') / (name + '.service.d/20-nosana-storage.conf'),
                          '[Unit]\nRequiresMountsFor=/var/lib/docker\nBindsTo=' + MOUNT_NAME + '\nAfter=' +
                          MOUNT_NAME + '\n[Service]\nExecStartPre=' + str(s.GUARD) + '\n')
        s.write_owned(state, Path('/etc/docker/daemon.json'), json.dumps({'data-root': str(DATA / 'docker')}, indent=2) + '\n')
        s.write_owned(state, Path('/etc/containerd/config.toml'), 'version = 2\nroot = "' + str(DATA / 'containerd') + '"\nstate = "/run/containerd"\n')
        s.run(['systemctl', 'daemon-reload'])
        check(s, state)
        s.run([str(s.GUARD)])
        s.stage(state, 'complete')
        print('Existing Docker mount adopted. No formatting or unmounting; existing files preserved.')
    except BaseException:
        print('Adoption interrupted. Use --recover-storage before retrying; existing disk files were not erased.', file=sys.stderr)
        raise


def verify(s, state, start=True):
    # Configuration hashes protect our service dependencies. Permit NVIDIA's
    # additive JSON runtime configuration while requiring the selected data-root.
    for change in state['changes']:
        path = Path(change['path'])
        s.safe_path(path)
        if str(path) == '/etc/docker/daemon.json':
            if json.loads(path.read_text()).get('data-root') != str(DATA / 'docker'):
                raise ValueError('Docker data-root changed')
        elif not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != change['sha256']:
            raise ValueError('Managed configuration changed: ' + str(path))
    text = FSTAB.read_bytes().decode()
    normalized = plan_fstab(text, {'UUID=' + state['uuid'], state['choice']['device']['path']}, state['uuid'])
    if normalized != text:
        raise ValueError('Adopted mount persistence configuration changed')
    if not start:
        return
    s.run(['systemctl', 'start', MOUNT_NAME])
    check(s, state)
    s.run([str(s.GUARD)])
    if shutil.which('containerd'):
        dump = s.run(['containerd', 'config', 'dump']).stdout
        dump = re.split(r'(?m)^\s*\[', dump, maxsplit=1)[0]
        match = re.search(r'''(?m)^root\s*=\s*(["'])([^\n]+)\1\s*$''', dump)
        if not match or match[2] != str(DATA / 'containerd'):
            raise ValueError('containerd effective root is not on the adopted disk')
    if shutil.which('docker') and s.run(['docker', 'info', '--format', '{{.DockerRootDir}}']).stdout.strip() != str(DATA / 'docker'):
        raise ValueError('Running Docker root is not on the adopted disk')
    print('Adopted Docker storage verified; no formatting or migration performed.')


def recover(s, state):
    if state['stage'] == 'recovered':
        print('Adoption was already recovered; no changes made.')
        return
    if state['stage'] == 'complete' or any(shutil.which(n) for n in ('dockerd', 'containerd')):
        raise ValueError('Completed or installed runtime may hold data; automatic rollback refused')
    for change in state['changes']:
        path = Path(change['path'])
        s.safe_path(path)
        if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest() != change['sha256']:
            raise ValueError('Administrator configuration changed; nothing will be removed')
    if hashlib.sha256(FSTAB.read_bytes()).hexdigest() not in (state['fstab']['before_sha256'], state['fstab']['after_sha256']):
        raise ValueError('fstab changed since adoption; preserving administrator edits')
    s.safe_path(FSTAB)
    info = FSTAB.stat()
    if stat.S_IMODE(info.st_mode) != state['fstab']['mode'] or info.st_gid != state['fstab']['gid']:
        raise ValueError('fstab permissions changed since adoption; preserving administrator edits')
    if state['stage'] == 'adopt-creating-data':
        if not active_mount(s):
            s.run(['systemctl', 'daemon-reload'])
            s.run(['systemctl', 'start', MOUNT_NAME])
        check(s, state, owned=False)
        s.safe_path(DATA)
        marker = DATA / '.owner'
        if DATA.is_dir() and not any(DATA.iterdir()):
            DATA.rmdir()
        elif DATA.exists():
            s.safe_path(marker)
            if not marker.is_file() or marker.read_text() != state['token'] + '\n':
                raise ValueError('Unowned data will not be removed')
            for name in ('docker', 'containerd'):
                path = DATA / name
                s.safe_path(path)
                if path.exists():
                    path.rmdir()
            if set(p.name for p in DATA.iterdir()) != {'.owner'}:
                raise ValueError('Unexpected data will not be removed')
            marker.unlink()
            DATA.rmdir()
    s.stage(state, 'recovering')
    replace_fstab(s, state, restore=True)
    for change in reversed(state['changes']):
        path = Path(change['path'])
        if path.exists():
            path.unlink()
        if path.parent.name.endswith(('.service.d', '.mount.d')) and path.parent.exists() and not any(path.parent.iterdir()):
            path.parent.rmdir()
    s.run(['systemctl', 'daemon-reload'])
    s.stage(state, 'recovered')
    print('Original fstab restored; adopted disk remains mounted and existing files were preserved.')
    print('Rollback restores the original boot behavior, including any previously required data-disk mount.')


def prompt(s, choice, list_only=False):
    print('Prepared disk at /var/lib/docker: {} | {:.1f} GiB | {} | ID {}'.format(
        choice['device']['path'], int(choice['disk']['size']) / 2**30,
        choice['disk'].get('model') or 'unknown model', choice['identity']))
    print('Adopt this existing mount without formatting or moving files.')
    print('Its fstab entry will use the verified UUID and allow Ubuntu to boot if the disk is absent.')
    if list_only:
        return
    if not sys.stdin.isatty():
        raise ValueError('An interactive terminal is required')
    phrase = 'USE ' + choice['identity']
    if input('Type exactly "{}" to adopt it, or Enter to cancel: '.format(phrase)) != phrase:
        raise ValueError('Cancelled; nothing was changed')
    configure(s, choice)
