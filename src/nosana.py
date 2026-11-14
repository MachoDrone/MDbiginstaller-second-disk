#!/usr/bin/env python3
"""Prepare classic Nosana on verified second-disk storage; activation is explicit."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import stat
import subprocess
import sys
import time

STORAGE_STATE = Path('/var/lib/nosana-storage-setup/state.json')
GUARD = Path('/usr/local/libexec/nosana-storage-check')
DOCKER = ['/usr/bin/docker', '--host', 'unix:///var/run/docker.sock']
PODMAN_IMAGE = 'docker.io/nosana/podman@sha256:1ea1474e0ee2e909246180e2598c55ef40d4cf30e5c310db77c4636f89e2c3d6'
NODE_BASE = 'docker.io/nosana/nosana-node@sha256:2bc2f2a0a559f54abdc21505f491a1ab23b9e2e4e8bf74164eddfccca91a64d3'
NODE_VERSION = '1.1.91'
NPM_INTEGRITY = 'sha512-C8b0dAbMI5y3zevbcOtN5OIuPEmSU+PcbZUD3zsrIjG6orENymsnanheNywGvBxpQOV5C8VKdoEbIF2jY1TOZw=='
OWNER_LABEL = 'io.machodrone.nosana-storage.owner'
RECIPE_LABEL = 'io.machodrone.nosana-storage.recipe'
VERSION_LABEL = 'io.machodrone.nosana-storage.node-version'
OUTER = 'podman'
INNER = 'nosana-node'
CACHE = 'podman-cache'


class Refusal(RuntimeError):
    pass


def run(args, *, allowed=(0,), interactive=False, input=None, timeout=120):
    # Never inherit a remote Docker context, npm credentials, or a wallet override.
    env = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LC_ALL': 'C', 'HOME': '/root'}
    if 'TERM' in os.environ:
        env['TERM'] = os.environ['TERM']
    result = subprocess.run(args, input=input, text=True, env=env, timeout=timeout,
                            stdin=None if interactive or input is not None else subprocess.DEVNULL,
                            stdout=None if interactive else subprocess.PIPE,
                            stderr=None if interactive else subprocess.PIPE)
    if result.returncode not in allowed:
        raise Refusal('{} failed: {}'.format(args[0], (result.stderr or 'see terminal output').strip()[-3000:]))
    return result


def secure(path):
    """Reject symlinked or writable configuration, including its ancestors."""
    for item in [path, *path.parents]:
        if item.exists() or item.is_symlink():
            info = item.lstat()
            if stat.S_ISLNK(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
                raise Refusal('Unsafe managed path: ' + str(item))


def inspect(kind, name):
    result = run(DOCKER + [kind, 'inspect', name], allowed=(0, 1))
    if result.returncode:
        # A missing object is different from an unreachable or broken daemon.
        missing = re.search(r'no such (?:image|container|volume|object)\b|(?:image|container|volume|object) [^\n]*not found', result.stderr.lower())
        if not missing:
            raise Refusal('Could not inspect Docker ' + kind + ': ' + result.stderr.strip())
        return None
    values = json.loads(result.stdout)
    if len(values) != 1:
        raise Refusal('Ambiguous Docker object: ' + name)
    return values[0]


def recipe(token):
    # npm registry installs honor npm-shrinkwrap.json. The offline second install
    # proves that all package bytes needed by the unchanged wrapper are cached.
    return f'''FROM {NODE_BASE}
RUN test "$(npm view @nosana/node@{NODE_VERSION} dist.integrity)" = "{NPM_INTEGRITY}" \\
 && npm install --global --prefix /root/.nosana-node/{NODE_VERSION} --no-audit --no-fund @nosana/node@{NODE_VERSION}
RUN --network=none npm --offline install --global --prefix /tmp/nosana-offline-check --no-audit --no-fund @nosana/node@{NODE_VERSION} \\
 && node -e 'for (const p of ["/root/.nosana-node/{NODE_VERSION}","/tmp/nosana-offline-check"]) {{ if (require(p+"/lib/node_modules/@nosana/node/package.json").version !== "{NODE_VERSION}") process.exit(1); }}' \\
 && rm -rf /tmp/nosana-offline-check
ENV NOSANA_NODE_VERSION={NODE_VERSION} npm_config_offline=true npm_config_audit=false npm_config_fund=false
LABEL {OWNER_LABEL}="{token}" {VERSION_LABEL}="{NODE_VERSION}"
'''


class Setup:
    def __init__(self):
        secure(STORAGE_STATE)
        storage = json.loads(STORAGE_STATE.read_text())
        if storage.get('stage') != 'complete':
            raise Refusal('Complete and verify second-disk storage before preparing Nosana')
        if storage.get('version') == 1:
            self.mount = Path('/mnt/nosana')
        elif storage.get('version') == 2 and storage.get('mode') == 'adopt':
            self.mount = Path('/var/lib/docker')
        else:
            raise Refusal('Unsupported storage journal')
        self.data = self.mount / 'nosana-runtime'
        self.storage_token = storage.get('token', '')
        self.uuid = storage.get('uuid', '')
        if not re.fullmatch('[0-9a-f]{32}', self.storage_token) or not re.fullmatch('[0-9a-fA-F-]{36}', self.uuid):
            raise Refusal('Invalid completed storage identity')
        self.state_dir = self.data / 'nosana-state'
        self.marker = self.data / 'nosana-setup.json'
        self.launcher = self.data / 'nosana-launch.py'
        self.guard()

    def guard(self):
        secure(GUARD)
        run([str(GUARD)])
        # Explicit local daemon; never write images through a user's remote context.
        root = run(DOCKER + ['info', '--format', '{{.DockerRootDir}}']).stdout.strip()
        if root != str(self.data / 'docker'):
            raise Refusal('Docker is not using the selected second disk')
        dump = run(['/usr/bin/containerd', 'config', 'dump']).stdout
        table = re.split(r'(?m)^\s*\[', dump, maxsplit=1)[0]
        match = re.search(r'''(?m)^root\s*=\s*(["'])([^\n]+)\1\s*$''', table)
        if not match or match[2] != str(self.data / 'containerd'):
            raise Refusal('containerd is not using the selected second disk')

    def mutate(self, args, **kwargs):
        self.guard()
        return run(args, **kwargs)

    def write(self, path, data, mode=0o600):
        self.guard()
        secure(path)
        temporary = path.with_name(path.name + '.tmp')
        secure(temporary)
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, mode)
        try:
            os.fchmod(fd, mode)
            with os.fdopen(fd, 'wb', closefd=False) as stream:
                stream.write(data)
                stream.flush()
                os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(temporary, path)
        fd = os.open(path.parent, os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def save(self, marker):
        self.write(self.marker, (json.dumps(marker, indent=2) + '\n').encode())

    def load(self):
        secure(self.marker)
        if not self.marker.exists():
            return None
        marker = json.loads(self.marker.read_text())
        expected = {'version': 1, 'storage_token': self.storage_token, 'uuid': self.uuid,
                    'podman_image': PODMAN_IMAGE, 'node_base': NODE_BASE, 'node_version': NODE_VERSION}
        if any(marker.get(k) != value for k, value in expected.items()):
            raise Refusal('Existing Nosana preparation has a different storage or software identity')
        if marker.get('stage') not in ('preparing', 'complete'):
            raise Refusal('Unknown Nosana preparation stage')
        return marker

    def conflicts(self, marker):
        # Only path existence is inspected: never read a key or print its contents.
        homes = {Path('/root')}
        homes.update(Path(user.pw_dir) for user in pwd.getpwall() if user.pw_uid >= 1000 and user.pw_dir.startswith('/'))
        paths = [home / '.nosana' for home in homes]
        paths += [Path('/var/lib/nosana'), Path('/etc/nosana'), Path('/usr/local/bin/nosana-host'),
                  Path('/etc/systemd/system/nosana-host.service'), Path('/etc/systemd/system/nosana-podman.service')]
        if any(path.exists() or path.is_symlink() for path in paths):
            raise Refusal('Existing Nosana state or host-agent installation found; migration is unsupported')
        outer = inspect('container', OUTER)
        cache = inspect('volume', CACHE)
        for obj in (outer, cache):
            labels = (obj.get('Config', {}).get('Labels') or obj.get('Labels') or {}) if obj else {}
            if obj and (not marker or labels.get(OWNER_LABEL) != self.storage_token):
                raise Refusal('Existing unmanaged Nosana container or cache; leaving it untouched')
        if cache and (cache.get('Driver') != 'local' or cache.get('Options')
                      or cache.get('Mountpoint') != str(self.data / 'docker/volumes' / CACHE / '_data')):
            raise Refusal('Existing Podman cache is not an ordinary volume on the selected disk')
        if inspect('container', INNER) or inspect('volume', 'podman-socket'):
            raise Refusal('Existing Nosana resources found; migration is unsupported')
        if marker is None and any(p.exists() or p.is_symlink() for p in (self.state_dir, self.launcher)):
            raise Refusal('Unmanaged Nosana files already exist on the selected disk')
        if outer:
            self.verify_outer(outer)

    def image(self, ref, recorded=None):
        info = inspect('image', ref)
        if not info:
            raise Refusal('Prepared image is missing; rerun --prepare-nosana: ' + ref)
        if info.get('Architecture') != 'amd64' or info.get('Os') != 'linux':
            raise Refusal('This Nosana image supports Linux x86-64 only')
        if recorded and info.get('Id') != recorded:
            raise Refusal('Prepared image identity changed')
        if '@sha256:' in ref:
            digest = ref.split('@', 1)[1]
            if not any(item.endswith('@' + digest) for item in info.get('RepoDigests', [])):
                raise Refusal('Image digest does not match the reviewed release')
        return info

    def derived(self, marker):
        info = self.image(marker['node_tag'], marker.get('node_id'))
        labels = info.get('Config', {}).get('Labels') or {}
        if (labels.get(OWNER_LABEL) != self.storage_token or labels.get(VERSION_LABEL) != NODE_VERSION
                or labels.get(RECIPE_LABEL) != marker['recipe_sha256']):
            raise Refusal('Prepared node image has different build labels')
        env = dict(item.split('=', 1) for item in info['Config'].get('Env', []) if '=' in item)
        if env.get('NOSANA_NODE_VERSION') != NODE_VERSION or env.get('npm_config_offline') != 'true':
            raise Refusal('Node image is not pinned and ready for offline initialization')
        return info

    def prepare(self):
        marker = self.load()
        self.conflicts(marker)
        if marker and marker.get('stage') == 'complete':
            self.verify()
            print('Nosana software already prepared; no services or node were started.')
            return
        content = Path(__file__).read_bytes()
        dockerfile = recipe(self.storage_token)
        recipe_hash = hashlib.sha256(dockerfile.encode()).hexdigest()
        if marker is None:
            marker = dict(version=1, storage_token=self.storage_token, uuid=self.uuid, stage='preparing',
                          podman_image=PODMAN_IMAGE, node_base=NODE_BASE, node_version=NODE_VERSION,
                          node_tag='nosana-second-disk-node:' + self.storage_token,
                          recipe_sha256=recipe_hash, launcher_sha256=hashlib.sha256(content).hexdigest())
            self.save(marker)  # intent precedes mkdir, file installation and downloads
        elif marker.get('recipe_sha256') != recipe_hash or marker.get('launcher_sha256') != hashlib.sha256(content).hexdigest():
            raise Refusal('Interrupted preparation belongs to a different installer; use that same installer to resume')
        secure(self.state_dir)
        if self.state_dir.exists() and (not self.state_dir.is_dir() or any(self.state_dir.iterdir())):
            raise Refusal('State appeared during interrupted preparation; preserving it for review')
        self.guard()
        self.state_dir.mkdir(mode=0o700, exist_ok=True)
        if self.state_dir.stat().st_mode & 0o077:
            raise Refusal('Nosana state directory must be private (mode 0700); leaving it untouched')
        if self.launcher.exists() and self.launcher.read_bytes() != content:
            raise Refusal('Prepared launcher was edited; leaving it untouched')
        self.write(self.launcher, content, 0o700)
        for ref, key in ((PODMAN_IMAGE, 'podman_id'), (NODE_BASE, 'node_base_id')):
            if not inspect('image', ref):
                print('Downloading the reviewed Nosana container image; this can take several minutes...', flush=True)
                self.mutate(DOCKER + ['pull', '--platform', 'linux/amd64', ref], timeout=1800)
            marker[key] = self.image(ref, marker.get(key))['Id']
            self.save(marker)
        if not inspect('image', marker['node_tag']):
            print('Installing the pinned Nosana package and checking its offline cache; this can take several minutes...', flush=True)
            self.mutate(DOCKER + ['build', '--pull=false', '--tag', marker['node_tag'],
                                 '--label', RECIPE_LABEL + '=' + recipe_hash, '-'],
                        input=dockerfile, timeout=2400)
        marker['node_id'] = self.derived(marker)['Id']
        marker['stage'] = 'complete'
        self.save(marker)
        self.verify()
        print('Nosana images, pinned node package and offline package cache are installed on the second disk.')
        print('No wallet was created and no Nosana node was started. Reboot after finishing GPU installation, then use --start-nosana.')

    def verify(self):
        self.guard()
        marker = self.load()
        if not marker or marker.get('stage') != 'complete':
            raise Refusal('Nosana preparation is incomplete; rerun --prepare-nosana')
        if (marker.get('recipe_sha256') != hashlib.sha256(recipe(self.storage_token).encode()).hexdigest()
                or marker.get('launcher_sha256') != hashlib.sha256(Path(__file__).read_bytes()).hexdigest()):
            raise Refusal('Prepared Nosana belongs to a different installer; use that same installer to verify or start it')
        self.conflicts(marker)
        for path in (self.launcher, self.state_dir):
            secure(path)
        if not self.state_dir.is_dir() or not self.launcher.is_file():
            raise Refusal('Prepared Nosana files are missing')
        if self.state_dir.stat().st_mode & 0o077:
            raise Refusal('Nosana state directory permissions changed')
        if hashlib.sha256(self.launcher.read_bytes()).hexdigest() != marker['launcher_sha256']:
            raise Refusal('Prepared launcher was changed')
        self.image(PODMAN_IMAGE, marker['podman_id'])
        self.image(NODE_BASE, marker['node_base_id'])
        self.derived(marker)
        return marker

    def verify_outer(self, info):
        config, host = info.get('Config', {}), info.get('HostConfig', {})
        mounts = {m.get('Destination'): m for m in info.get('Mounts', [])}
        expected = {'/root/.nosana': ('bind', str(self.state_dir)),
                    '/run/nvidia-persistenced': ('bind', '/run/nvidia-persistenced'),
                    '/var/lib/containers': ('volume', CACHE)}
        # This pinned upstream image declares a second (rootless) VOLUME. Docker
        # creates it even though the classic host uses rootful Podman storage.
        rootless = mounts.get('/home/podman/.local/share/containers', {})
        anonymous = rootless.get('Name', '')
        if not re.fullmatch('[0-9a-f]{64}', anonymous):
            raise Refusal('The image-declared Podman volume is missing or changed')
        volume = inspect('volume', anonymous)
        location = str(self.data / 'docker/volumes' / anonymous / '_data')
        if (not volume or volume.get('Driver') != 'local' or volume.get('Options')
                or volume.get('Mountpoint') != location or rootless.get('Source') != location):
            raise Refusal('The image-declared Podman volume is not on the selected disk')
        expected['/home/podman/.local/share/containers'] = ('volume', anonymous)
        good_mounts = set(mounts) == set(expected) and all(
            mounts[dest].get('Type') == kind and mounts[dest].get('Name' if kind == 'volume' else 'Source') == source
            and mounts[dest].get('RW') is True for dest, (kind, source) in expected.items())
        env = dict(item.split('=', 1) for item in config.get('Env', []) if '=' in item)
        if (config.get('Image') != PODMAN_IMAGE or config.get('Cmd') != ['unix:/podman.sock']
                or (config.get('Labels') or {}).get(OWNER_LABEL) != self.storage_token
                or not host.get('Privileged') or host.get('RestartPolicy', {}).get('Name') not in ('', 'no')
                or not good_mounts or env.get('ENABLE_GPU') != 'true' or env.get('NVIDIA_DRIVER_CAPABILITIES') != 'all'
                or 'SOLANA_WALLET' in env):
            raise Refusal('Existing managed Podman container configuration changed; leaving it untouched')

    def gpu_ready(self, marker):
        try:
            output = run(['/usr/bin/nvidia-smi', '-L']).stdout.strip()
        except (OSError, Refusal) as error:
            raise Refusal('NVIDIA is not ready; finish driver installation and reboot before starting Nosana') from error
        if not output:
            raise Refusal('No working NVIDIA GPU detected; finish driver installation and reboot first')
        run(['/usr/bin/nvidia-ctk', '--version'])
        if run(['/usr/bin/systemctl', 'show', 'nvidia-persistenced.service', '--property=LoadState', '--value']).stdout.strip() != 'loaded':
            raise Refusal('The NVIDIA persistence service is not installed; finish NVIDIA setup first')
        self.mutate(['/usr/bin/systemctl', 'start', 'nvidia-persistenced.service'])
        if not Path('/run/nvidia-persistenced').is_dir() or not Path('/dev/fuse').exists():
            raise Refusal('NVIDIA persistence or /dev/fuse is unavailable')
        self.mutate(DOCKER + ['run', '--rm', '--pull=never', '--gpus=all',
                             '-e', 'NVIDIA_DRIVER_CAPABILITIES=compute,utility',
                             '--entrypoint', 'nvidia-smi', marker['node_id'], '-L'])

    def podman(self, args, **kwargs):
        return run(DOCKER + ['exec', OUTER, 'podman'] + args, **kwargs)

    def inner_image(self, marker):
        # Docker's containerd image store exposes a manifest/index Id; Podman 4.5
        # exposes the imported config Id plus the original manifest as Digest.
        # Older Docker exposes the config Id directly. Match either explicitly.
        images = json.loads(self.podman(['images', '--format', 'json']).stdout)
        expected = marker['node_id']
        matches = {item.get('Id', '').removeprefix('sha256:') for item in images
                   if item.get('Id', '').removeprefix('sha256:') == expected.removeprefix('sha256:')
                   or item.get('Digest') == expected}
        if not matches:
            return None
        if len(matches) != 1 or not re.fullmatch('[0-9a-f]{64}', next(iter(matches))):
            raise Refusal('The prepared image has an ambiguous Podman identity')
        image_id = 'sha256:' + next(iter(matches))
        records = json.loads(self.podman(['image', 'inspect', image_id]).stdout)
        if len(records) != 1:
            raise Refusal('Cannot verify the imported node image')
        info = records[0]
        labels = info.get('Config', {}).get('Labels') or info.get('Labels') or {}
        env = dict(item.split('=', 1) for item in info.get('Config', {}).get('Env', []) if '=' in item)
        if (info.get('Id', '').removeprefix('sha256:') != image_id.removeprefix('sha256:')
                or not (image_id == expected or info.get('Digest') == expected)
                or labels.get(OWNER_LABEL) != self.storage_token or labels.get(VERSION_LABEL) != NODE_VERSION
                or labels.get(RECIPE_LABEL) != marker['recipe_sha256']
                or env.get('NOSANA_NODE_VERSION') != NODE_VERSION or env.get('npm_config_offline') != 'true'):
            raise Refusal('The imported node image differs from the prepared image')
        return image_id

    def verify_inner(self, info, marker, image_id=None):
        config = info.get('Config', {})
        env = dict(item.split('=', 1) for item in config.get('Env', []) if '=' in item)
        mounts = {item.get('Destination'): item for item in info.get('Mounts', [])}
        expected = {'/root/.nosana': '/root/.nosana', '/root/.nosana/podman/podman.sock': '/podman.sock'}
        if ((config.get('Labels') or {}).get(OWNER_LABEL) != self.storage_token
                or info.get('Image', '').removeprefix('sha256:') != (image_id or marker['node_id']).removeprefix('sha256:')
                or config.get('Cmd') != ['start', '--network', 'mainnet']
                or env.get('NOSANA_NODE_VERSION') != NODE_VERSION or env.get('npm_config_offline') != 'true'
                or 'SOLANA_WALLET' in env or set(mounts) != set(expected)
                or any(mounts[dest].get('Source') != source or mounts[dest].get('Type') != 'bind'
                       or mounts[dest].get('RW') is not True for dest, source in expected.items())
                or info.get('HostConfig', {}).get('RestartPolicy', {}).get('Name') not in ('', 'no')
                or set(info.get('NetworkSettings', {}).get('Networks', {})) != {'NOSANA_GATEWAY'}):
            raise Refusal('Existing inner Nosana configuration differs from this installer; leaving it untouched')

    def load_node_image(self, image_id):
        self.guard()
        # Stream between local runtimes: no tar archive or package cache on the OS disk.
        save = subprocess.Popen(DOCKER + ['image', 'save', image_id], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        load = None
        try:
            load = subprocess.Popen(DOCKER + ['exec', '-i', OUTER, 'podman', 'load'],
                                    stdin=save.stdout, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            save.stdout.close()
            _, error = load.communicate(timeout=900)
            save.wait(timeout=30)
            if save.returncode or load.returncode:
                raise Refusal('Could not import the prepared node image into Podman: ' + error.decode(errors='replace')[-1000:])
        finally:
            for process in (save, load):
                if process and process.poll() is None:
                    process.kill()
                    process.wait()
            save.stderr.close()

    def start(self):
        marker = self.verify()
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            raise Refusal('Starting Nosana requires an interactive operator terminal')
        print('Starting Nosana creates/uses its wallet on the second disk and connects to mainnet.')
        print('Nosana will ask for registration/terms, run qualification and may submit transactions using wallet funds,')
        print('including token/staking account creation and job participation. A NOS stake top-up has its own Nosana prompt.')
        if input('Type START NOSANA to continue: ') != 'START NOSANA':
            raise Refusal('Nosana was not started')
        self.gpu_ready(marker)
        self.conflicts(marker)
        if not inspect('volume', CACHE):
            self.mutate(DOCKER + ['volume', 'create', '--label', OWNER_LABEL + '=' + self.storage_token, CACHE])
        self.conflicts(marker)  # volume create may return an object created concurrently
        if not inspect('container', OUTER):
            self.mutate(DOCKER + ['create', '--pull=never', '--restart=no', '--name', OUTER,
                                 '--label', OWNER_LABEL + '=' + self.storage_token,
                                 '--gpus=all', '--device', '/dev/fuse', '--privileged',
                                 '--mount', 'type=bind,source=/run/nvidia-persistenced,target=/run/nvidia-persistenced,bind-propagation=rslave',
                                 '--mount', 'type=volume,source=' + CACHE + ',target=/var/lib/containers',
                                 '--mount', 'type=bind,source=' + str(self.state_dir) + ',target=/root/.nosana',
                                 '-e', 'ENABLE_GPU=true', '-e', 'NVIDIA_DRIVER_CAPABILITIES=all',
                                 PODMAN_IMAGE, 'unix:/podman.sock'])
        outer = inspect('container', OUTER)
        self.verify_outer(outer)
        if not outer.get('State', {}).get('Running'):
            self.mutate(DOCKER + ['start', OUTER])
        for attempt in range(20):
            result = self.podman(['info', '--format', 'json'], allowed=tuple(range(256)))
            if result.returncode == 0:
                store = json.loads(result.stdout).get('store', {})
                if (store.get('graphRoot') != '/var/lib/containers/storage'
                        or store.get('volumePath') != '/var/lib/containers/storage/volumes'):
                    raise Refusal('Podman heavy data is not using the second-disk cache mount')
                break
            if attempt == 19:
                raise Refusal('Podman did not become ready; no Nosana node was started')
            time.sleep(1)
        inner_image = self.inner_image(marker)
        if inner_image is None:
            self.load_node_image(marker['node_id'])
            inner_image = self.inner_image(marker)
        if inner_image is None:
            raise Refusal('The prepared node image was not imported into Podman')
        network = self.podman(['network', 'inspect', 'NOSANA_GATEWAY'], allowed=(0, 125))
        if network.returncode:
            self.mutate(DOCKER + ['exec', OUTER, 'podman', 'network', 'create', '--driver', 'bridge',
                                 '--subnet=192.168.101.0/24', '--gateway=192.168.101.1',
                                 '--label', OWNER_LABEL + '=' + self.storage_token, 'NOSANA_GATEWAY'])
            network = self.podman(['network', 'inspect', 'NOSANA_GATEWAY'])
        networks = json.loads(network.stdout)
        if (len(networks) != 1 or (networks[0].get('labels') or {}).get(OWNER_LABEL) != self.storage_token
                or networks[0].get('subnets') != [{'subnet': '192.168.101.0/24', 'gateway': '192.168.101.1'}]):
            raise Refusal('Existing Podman network differs from this installer; leaving it untouched')
        node = self.podman(['container', 'inspect', INNER], allowed=(0, 125))
        if node.returncode:
            self.mutate(DOCKER + ['exec', OUTER, 'podman', 'create', '--pull=never', '--restart=no',
                                 '--name', INNER, '--network', 'NOSANA_GATEWAY', '--interactive', '--tty',
                                 '--label', OWNER_LABEL + '=' + self.storage_token,
                                 '--volume', '/root/.nosana:/root/.nosana',
                                 '--mount', 'type=bind,source=/podman.sock,target=/root/.nosana/podman/podman.sock',
                                 '-e', 'NOSANA_NODE_VERSION=' + NODE_VERSION,
                                 '-e', 'NOSANA_START_SCRIPT_VERSION=1.1.2-3',
                                 inner_image, 'start', '--network', 'mainnet'])
            node = self.podman(['container', 'inspect', INNER])
        nodes = json.loads(node.stdout)
        if len(nodes) != 1:
            raise Refusal('Existing inner Nosana container is not this prepared image; leaving it untouched')
        self.verify_inner(nodes[0], marker, inner_image)
        if nodes[0].get('State', {}).get('Running'):
            print('The managed Nosana node is already running; it was not restarted.')
            return
        self.mutate(DOCKER + ['exec', '-it', OUTER, 'podman', 'start', '--attach', '--interactive', INNER],
                    interactive=True, timeout=None)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv not in (['prepare'], ['verify'], ['start']):
        raise Refusal('Usage: nosana.py prepare|verify|start')
    if os.geteuid() != 0:
        raise Refusal('Use the installer as your normal user; it obtains sudo privileges when needed')
    setup = Setup()  # guard runs before even creating the installer lock
    lock = os.open('/run/lock/nosana-install.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        getattr(setup, argv[0])()
        if argv == ['verify']:
            print('Prepared Nosana software and second-disk placement verified; no node was started.')
    finally:
        os.close(lock)


if __name__ == '__main__':
    try:
        main()
    except (Refusal, OSError, ValueError, subprocess.SubprocessError) as error:
        print('STOP:', error, file=sys.stderr)
        sys.exit(1)
