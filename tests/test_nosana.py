"""Independent installation/activation requirements; every process is a test double.

No Docker daemon, network, NVIDIA device, Nosana wallet or host service is used.
The only real writes are private temporary fixture files.
"""
import contextlib
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src import nosana  # noqa: E402

TOKEN = 'a' * 32
UUID = '22222222-2222-4222-8222-222222222222'
PODMAN_ID = 'sha256:' + '1' * 64
BASE_ID = 'sha256:' + '2' * 64
NODE_ID = 'sha256:' + '3' * 64


def completed(stdout='', code=0, stderr=''):
    return subprocess.CompletedProcess([], code, stdout, stderr)


def image_record(ref, image_id):
    return {'Id': image_id, 'Architecture': 'amd64', 'Os': 'linux',
            'RepoDigests': [ref], 'Config': {'Labels': {}, 'Env': []}}


class PreparationFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.setup = object.__new__(nosana.Setup)
        self.setup.mount = self.root
        self.setup.data = self.root / 'nosana-runtime'
        self.setup.data.mkdir(mode=0o700)
        self.setup.storage_token = TOKEN
        self.setup.uuid = UUID
        self.setup.state_dir = self.setup.data / 'nosana-state'
        self.setup.marker = self.setup.data / 'nosana-setup.json'
        self.setup.launcher = self.setup.data / 'nosana-launch.py'
        self.events = []
        self.objects = {}
        self.fail_build_once = False
        self.patches = contextlib.ExitStack()
        self.addCleanup(self.patches.close)
        # Root ownership/mounts are covered independently by the real-VM gate.
        self.patches.enter_context(patch.object(nosana, 'secure'))
        real_exists, real_symlink = Path.exists, Path.is_symlink
        self.patches.enter_context(patch.object(Path, 'exists', lambda path: real_exists(path) if path.is_relative_to(self.root) else False))
        self.patches.enter_context(patch.object(Path, 'is_symlink', lambda path: real_symlink(path) if path.is_relative_to(self.root) else False))
        self.patches.enter_context(patch.object(nosana, 'inspect', side_effect=self.inspect))
        self.patches.enter_context(patch.object(nosana, 'run', side_effect=self.command))
        self.patches.enter_context(patch.object(nosana.pwd, 'getpwall', return_value=[]))
        self.patches.enter_context(patch.object(self.setup, 'guard', side_effect=lambda: self.events.append(('guard',))))
        self.patches.enter_context(contextlib.redirect_stdout(io.StringIO()))

    def inspect(self, kind, name):
        self.events.append(('inspect', kind, name))
        return copy.deepcopy(self.objects.get((kind, name)))

    def command(self, args, **kwargs):
        # Unknown commands are rejected, never sent to the host.
        self.assertEqual(args[:3], ['/usr/bin/docker', '--host', 'unix:///var/run/docker.sock'])
        self.assertEqual(self.events[-1], ('guard',), 'Mutation lacks a fresh disk guard')
        self.events.append(('command', list(args), kwargs))
        args = args[3:]
        if args[0] == 'pull':
            ref = args[-1]
            self.assertIn(ref, [nosana.PODMAN_IMAGE, nosana.NODE_BASE])
            self.assertEqual(args[1:3], ['--platform', 'linux/amd64'])
            self.objects['image', ref] = image_record(ref, PODMAN_ID if ref == nosana.PODMAN_IMAGE else BASE_ID)
        elif args[0] == 'build':
            if self.fail_build_once:
                self.fail_build_once = False
                raise nosana.Refusal('injected build/network interruption')
            tag = args[args.index('--tag') + 1]
            recipe_label = args[args.index('--label') + 1].split('=', 1)[1]
            record = image_record(tag, NODE_ID)
            record['Config'] = {'Labels': {nosana.OWNER_LABEL: TOKEN,
                                           nosana.VERSION_LABEL: '1.1.91',
                                           nosana.RECIPE_LABEL: recipe_label},
                                'Env': ['NOSANA_NODE_VERSION=1.1.91', 'npm_config_offline=true']}
            self.objects['image', tag] = record
        else:
            self.fail('Preparation attempted an unauthorized process: ' + repr(args))
        return completed()

    def prepare(self):
        self.setup.prepare()
        return json.loads(self.setup.marker.read_text())

    def commands(self):
        return [entry[1][3:] for entry in self.events if entry[0] == 'command']

    def test_prepare_installs_on_selected_disk_without_starting_anything(self):
        marker = self.prepare()
        self.assertEqual(marker['stage'], 'complete')
        self.assertEqual([cmd[0] for cmd in self.commands()], ['pull', 'pull', 'build'])
        self.assertEqual(set(self.setup.state_dir.iterdir()), set())
        self.assertEqual(self.setup.state_dir.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.setup.marker.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.setup.launcher.stat().st_mode & 0o777, 0o700)
        self.assertEqual(marker['node_id'], NODE_ID)
        self.assertFalse(any(p.name == 'nosana_key.json' for p in self.root.rglob('*')))

    def test_finished_rerun_and_verify_do_not_pull_build_or_start(self):
        first = self.prepare()
        self.events.clear()
        self.assertEqual(self.setup.verify(), first)
        self.setup.prepare()
        self.assertEqual(self.commands(), [])
        self.assertEqual(json.loads(self.setup.marker.read_text()), first)

    def test_build_interruption_is_resumable_without_repulling_existing_images(self):
        self.fail_build_once = True
        with self.assertRaisesRegex(nosana.Refusal, 'interruption'):
            self.prepare()
        self.assertEqual(json.loads(self.setup.marker.read_text())['stage'], 'preparing')
        self.events.clear()
        self.prepare()
        self.assertEqual([cmd[0] for cmd in self.commands()], ['build'])

    def test_completed_build_before_journal_commit_is_adopted_only_with_matching_labels(self):
        self.fail_build_once = True
        with self.assertRaises(nosana.Refusal):
            self.prepare()
        marker = json.loads(self.setup.marker.read_text())
        good = image_record(marker['node_tag'], NODE_ID)
        good['Config'] = {'Labels': {nosana.OWNER_LABEL: TOKEN, nosana.VERSION_LABEL: '1.1.91',
                                    nosana.RECIPE_LABEL: marker['recipe_sha256']},
                          'Env': ['NOSANA_NODE_VERSION=1.1.91', 'npm_config_offline=true']}
        self.objects['image', marker['node_tag']] = good
        self.events.clear()
        self.prepare()
        self.assertEqual(self.commands(), [])

    def test_unmanaged_state_is_not_overwritten(self):
        self.setup.state_dir.mkdir()
        private = self.setup.state_dir / 'nosana_key.json'
        private.write_text('fixture sentinel, not a key')
        with self.assertRaisesRegex(nosana.Refusal, 'Unmanaged'):
            self.prepare()
        self.assertEqual(private.read_text(), 'fixture sentinel, not a key')
        self.assertFalse(self.setup.marker.exists())
        self.assertEqual(self.commands(), [])

    def test_operator_existing_home_is_refused_without_reading_wallet(self):
        home = self.root / 'operator'
        (home / '.nosana').mkdir(parents=True)
        with patch.object(nosana.pwd, 'getpwall', return_value=[types.SimpleNamespace(pw_uid=1000, pw_dir=str(home))]):
            with self.assertRaisesRegex(nosana.Refusal, 'Existing Nosana state'):
                self.prepare()
        self.assertEqual(self.commands(), [])

    def test_foreign_outer_container_or_cache_is_never_replaced(self):
        for kind, name, info in [('container', 'podman', {'Config': {'Labels': {}}}),
                                 ('volume', 'podman-cache', {'Labels': {}})]:
            with self.subTest(kind=kind):
                self.objects[kind, name] = info
                with self.assertRaisesRegex(nosana.Refusal, 'unmanaged'):
                    self.prepare()
                del self.objects[kind, name]
        self.assertEqual(self.commands(), [])

    def test_owned_cache_must_still_be_local_and_on_selected_disk(self):
        marker = self.prepare()
        good = {'Labels': {nosana.OWNER_LABEL: TOKEN}, 'Driver': 'local', 'Options': None,
                'Mountpoint': str(self.setup.data / 'docker/volumes/podman-cache/_data')}
        self.objects['volume', 'podman-cache'] = good
        self.setup.conflicts(marker)
        for field, value in [('Driver', 'nfs'), ('Options', {'device': '/var/lib/other'}),
                             ('Mountpoint', '/var/lib/docker/volumes/podman-cache/_data')]:
            with self.subTest(field=field):
                self.objects['volume', 'podman-cache'] = {**good, field: value}
                with self.assertRaisesRegex(nosana.Refusal, 'ordinary volume'):
                    self.setup.verify()

    def test_missing_disk_prevents_mutation_even_during_resume(self):
        self.fail_build_once = True
        with self.assertRaises(nosana.Refusal):
            self.prepare()
        self.events.clear()
        with patch.object(self.setup, 'guard', side_effect=nosana.Refusal('disk absent')):
            with self.assertRaisesRegex(nosana.Refusal, 'disk absent'):
                self.setup.prepare()
        self.assertEqual(self.commands(), [])

    def test_wrong_architecture_and_digest_are_refused(self):
        for field, value in [('Architecture', 'arm64'), ('RepoDigests', ['nosana/podman@sha256:bad'])]:
            with self.subTest(field=field):
                info = image_record(nosana.PODMAN_IMAGE, PODMAN_ID)
                info[field] = value
                self.objects['image', nosana.PODMAN_IMAGE] = info
                with self.assertRaises(nosana.Refusal):
                    self.setup.image(nosana.PODMAN_IMAGE)

    def test_stale_derived_image_label_is_not_rebuilt_over(self):
        marker = self.prepare()
        self.objects['image', marker['node_tag']]['Config']['Labels'][nosana.RECIPE_LABEL] = 'old-build'
        self.events.clear()
        with self.assertRaisesRegex(nosana.Refusal, 'build labels'):
            self.setup.prepare()
        self.assertEqual(self.commands(), [])

    def test_completed_previous_recipe_cannot_be_accepted_by_a_new_installer(self):
        marker = self.prepare()
        marker['recipe_sha256'] = 'f' * 64
        self.setup.marker.write_text(json.dumps(marker))
        self.objects['image', marker['node_tag']]['Config']['Labels'][nosana.RECIPE_LABEL] = 'f' * 64
        self.events.clear()
        with self.assertRaisesRegex(nosana.Refusal, 'use that same installer'):
            self.setup.prepare()
        self.assertEqual(self.commands(), [])

    def test_completed_previous_helper_cannot_be_used_by_new_source(self):
        self.prepare()
        newer = self.root / 'newer-helper.py'
        newer.write_bytes(Path(nosana.__file__).read_bytes() + b'\n# new candidate\n')
        self.events.clear()
        with patch.object(nosana, '__file__', str(newer)):
            with self.assertRaisesRegex(nosana.Refusal, 'use that same installer'):
                self.setup.verify()
        self.assertEqual(self.commands(), [])

    def test_changed_launcher_and_private_permissions_are_refused(self):
        self.prepare()
        self.setup.launcher.write_text('edited launcher')
        with self.assertRaisesRegex(nosana.Refusal, 'launcher was changed'):
            self.setup.verify()
        self.setup.launcher.write_bytes(Path(nosana.__file__).read_bytes())
        self.setup.state_dir.chmod(0o755)
        with self.assertRaisesRegex(nosana.Refusal, 'permissions changed'):
            self.setup.verify()

    def test_declined_activation_never_checks_gpu_or_starts_processes(self):
        self.prepare()
        self.events.clear()
        with patch.object(sys.stdin, 'isatty', return_value=True), patch.object(sys.stdout, 'isatty', return_value=True), \
                patch('builtins.input', return_value='no'), patch.object(self.setup, 'gpu_ready') as gpu:
            with self.assertRaisesRegex(nosana.Refusal, 'not started'):
                self.setup.start()
            gpu.assert_not_called()
        self.assertEqual(self.commands(), [])

    def test_noninteractive_activation_is_refused(self):
        self.prepare()
        self.events.clear()
        with patch.object(sys.stdin, 'isatty', return_value=False):
            with self.assertRaisesRegex(nosana.Refusal, 'interactive'):
                self.setup.start()
        self.assertEqual(self.commands(), [])

    def test_gpu_failure_after_explicit_consent_never_starts_nosana(self):
        self.prepare()
        self.events.clear()
        with patch.object(sys.stdin, 'isatty', return_value=True), patch.object(sys.stdout, 'isatty', return_value=True), \
                patch('builtins.input', return_value='START NOSANA'), \
                patch.object(self.setup, 'gpu_ready', side_effect=nosana.Refusal('NVIDIA not ready')):
            with self.assertRaisesRegex(nosana.Refusal, 'NVIDIA not ready'):
                self.setup.start()
        self.assertEqual(self.commands(), [])

    def test_volume_create_race_is_rechecked_before_container_creation(self):
        self.prepare()
        self.events.clear()

        def race(args, **kwargs):
            self.assertEqual(args[3:5], ['volume', 'create'])
            self.events.append(('command', args, kwargs))
            self.objects['volume', 'podman-cache'] = {
                'Labels': {nosana.OWNER_LABEL: TOKEN}, 'Driver': 'local',
                'Options': {'device': '/os-data', 'type': 'none', 'o': 'bind'},
                'Mountpoint': str(self.setup.data / 'docker/volumes/podman-cache/_data')}
            return completed('podman-cache')

        with patch.object(sys.stdin, 'isatty', return_value=True), patch.object(sys.stdout, 'isatty', return_value=True), \
                patch('builtins.input', return_value='START NOSANA'), patch.object(self.setup, 'gpu_ready'), \
                patch.object(nosana, 'run', side_effect=race):
            with self.assertRaisesRegex(nosana.Refusal, 'ordinary volume'):
                self.setup.start()
        self.assertEqual([command[:2] for command in self.commands()], [['volume', 'create']])


class ExternalContractTests(unittest.TestCase):
    def test_docker29_manifest_identity_resolves_to_inspected_podman_config(self):
        setup = object.__new__(nosana.Setup)
        setup.storage_token = TOKEN
        manifest_id = 'sha256:' + '4' * 64
        config_id = '5' * 64
        recipe = '6' * 64
        record = {'Id': config_id, 'Digest': manifest_id,
                  'Config': {'Labels': {nosana.OWNER_LABEL: TOKEN, nosana.VERSION_LABEL: '1.1.91',
                                        nosana.RECIPE_LABEL: recipe},
                             'Env': ['NOSANA_NODE_VERSION=1.1.91', 'npm_config_offline=true']}}
        marker = {'node_id': manifest_id, 'recipe_sha256': recipe}
        with patch.object(setup, 'podman', side_effect=[completed(json.dumps([record])), completed(json.dumps([record]))]) as command:
            self.assertEqual(setup.inner_image(marker), 'sha256:' + config_id)
        self.assertEqual(command.call_args_list[1].args[0], ['image', 'inspect', 'sha256:' + config_id])
        # Older Docker exposes the config identity itself; the same fixed image
        # must be accepted without mistaking its manifest digest for its ID.
        marker['node_id'] = 'sha256:' + config_id
        with patch.object(setup, 'podman', side_effect=[completed(json.dumps([record])), completed(json.dumps([record]))]):
            self.assertEqual(setup.inner_image(marker), 'sha256:' + config_id)

    def test_podman_identity_refuses_ambiguity_or_changed_import(self):
        setup = object.__new__(nosana.Setup)
        setup.storage_token = TOKEN
        marker = {'node_id': 'sha256:' + '4' * 64, 'recipe_sha256': '6' * 64}
        entries = [{'Id': '5' * 64, 'Digest': marker['node_id']}, {'Id': '7' * 64, 'Digest': marker['node_id']}]
        with patch.object(setup, 'podman', return_value=completed(json.dumps(entries))):
            with self.assertRaisesRegex(nosana.Refusal, 'ambiguous'):
                setup.inner_image(marker)
        with patch.object(setup, 'podman', return_value=completed('[]')):
            self.assertIsNone(setup.inner_image(marker))
        with patch.object(setup, 'podman', side_effect=[completed(json.dumps(entries[:1])),
                                                       completed(json.dumps([{'Id': '5' * 64, 'Digest': 'wrong'}]))]):
            with self.assertRaisesRegex(nosana.Refusal, 'differs'):
                setup.inner_image(marker)

    def test_image_declared_rootless_volume_must_stay_on_selected_disk(self):
        setup = object.__new__(nosana.Setup)
        setup.storage_token = TOKEN
        setup.data = Path('/var/lib/docker/nosana-runtime')
        setup.state_dir = setup.data / 'nosana-state'
        anonymous = 'b' * 64
        mountpoint = str(setup.data / 'docker/volumes' / anonymous / '_data')
        volume = {'Driver': 'local', 'Options': None, 'Mountpoint': mountpoint}
        info = {'Config': {'Image': nosana.PODMAN_IMAGE, 'Cmd': ['unix:/podman.sock'],
                           'Labels': {nosana.OWNER_LABEL: TOKEN},
                           'Env': ['ENABLE_GPU=true', 'NVIDIA_DRIVER_CAPABILITIES=all']},
                'HostConfig': {'Privileged': True, 'RestartPolicy': {'Name': 'no'}},
                'Mounts': [
                    {'Destination': '/root/.nosana', 'Type': 'bind', 'Source': str(setup.state_dir), 'RW': True},
                    {'Destination': '/run/nvidia-persistenced', 'Type': 'bind', 'Source': '/run/nvidia-persistenced', 'RW': True},
                    {'Destination': '/var/lib/containers', 'Type': 'volume', 'Name': 'podman-cache', 'RW': True},
                    {'Destination': '/home/podman/.local/share/containers', 'Type': 'volume', 'Name': anonymous,
                     'Source': mountpoint, 'RW': True}]}
        with patch.object(nosana, 'inspect', return_value=volume) as inspect:
            setup.verify_outer(info)
        inspect.assert_called_once_with('volume', anonymous)
        for field, value in [('Driver', 'nfs'), ('Options', {'device': '/os-state'}),
                             ('Mountpoint', '/var/lib/other/_data')]:
            with self.subTest(field=field), patch.object(nosana, 'inspect', return_value={**volume, field: value}):
                with self.assertRaisesRegex(nosana.Refusal, 'not on the selected disk'):
                    setup.verify_outer(info)
        bad = copy.deepcopy(info)
        bad['Mounts'].append({'Destination': '/unexpected', 'Type': 'bind', 'Source': '/tmp', 'RW': True})
        with patch.object(nosana, 'inspect', return_value=volume):
            with self.assertRaises(nosana.Refusal):
                setup.verify_outer(bad)

    def test_absent_docker29_volume_is_distinguished_from_daemon_errors(self):
        for error in ['Error response from daemon: get podman-cache: no such volume',
                      'Error: No such image: nosana/image', 'Error: container not found']:
            with self.subTest(error=error), patch.object(nosana, 'run', return_value=completed(code=1, stderr=error)):
                self.assertIsNone(nosana.inspect('volume', 'podman-cache'))
        for error in ['Cannot connect to the Docker daemon', 'permission denied while trying to connect',
                      'dial unix /var/run/docker.sock: connect: no such file or directory',
                      'Error response from daemon: internal server error']:
            with self.subTest(error=error), patch.object(nosana, 'run', return_value=completed(code=1, stderr=error)):
                with self.assertRaisesRegex(nosana.Refusal, 'Could not inspect'):
                    nosana.inspect('volume', 'podman-cache')

    def test_inner_container_contract_rejects_storage_or_launch_drift(self):
        setup = object.__new__(nosana.Setup)
        setup.storage_token = TOKEN
        info = {'Image': NODE_ID, 'Config': {'Labels': {nosana.OWNER_LABEL: TOKEN},
                'Cmd': ['start', '--network', 'mainnet'],
                'Env': ['NOSANA_NODE_VERSION=1.1.91', 'npm_config_offline=true']},
                'HostConfig': {'RestartPolicy': {'Name': 'no'}},
                'Mounts': [{'Destination': '/root/.nosana', 'Source': '/root/.nosana', 'Type': 'bind', 'RW': True},
                           {'Destination': '/root/.nosana/podman/podman.sock', 'Source': '/podman.sock', 'Type': 'bind', 'RW': True}],
                'NetworkSettings': {'Networks': {'NOSANA_GATEWAY': {}}}}
        marker = {'node_id': NODE_ID}
        setup.verify_inner(info, marker)
        altered = []
        for index, source in [(0, '/tmp/os-state'), (1, '/other.sock')]:
            bad = copy.deepcopy(info)
            bad['Mounts'][index]['Source'] = source
            altered.append(bad)
        bad = copy.deepcopy(info)
        bad['Config']['Cmd'] = ['start', '--network', 'devnet']
        altered.append(bad)
        bad = copy.deepcopy(info)
        bad['NetworkSettings']['Networks'] = {'other': {}}
        altered.append(bad)
        bad = copy.deepcopy(info)
        bad['HostConfig']['RestartPolicy']['Name'] = 'always'
        altered.append(bad)
        for extra in ['SOLANA_WALLET=fixture-not-a-secret', 'npm_config_offline=false', 'NOSANA_NODE_VERSION=latest']:
            bad = copy.deepcopy(info)
            bad['Config']['Env'].append(extra)
            altered.append(bad)
        bad = copy.deepcopy(info)
        bad['Mounts'].append({'Destination': '/extra', 'Source': '/os/extra', 'Type': 'bind', 'RW': True})
        altered.append(bad)
        for number, bad in enumerate(altered):
            with self.subTest(number=number), self.assertRaisesRegex(nosana.Refusal, 'configuration differs'):
                setup.verify_inner(bad, marker)

    def test_recipe_pins_source_integrity_and_proves_offline_install_without_node_execution(self):
        text = nosana.recipe(TOKEN)
        self.assertIn('FROM docker.io/nosana/nosana-node@sha256:2bc2f2a0a559f54abdc21505f491a1ab23b9e2e4e8bf74164eddfccca91a64d3', text)
        self.assertIn('npm view @nosana/node@1.1.91 dist.integrity', text)
        self.assertIn('npm --offline install --global --prefix /tmp/nosana-offline-check', text)
        self.assertIn('\nRUN --network=none npm --offline install', text)
        self.assertIn('NOSANA_NODE_VERSION=1.1.91 npm_config_offline=true', text)
        for forbidden in ['nosana-node start', 'nosana-node --version', 'SOLANA_WALLET', '--network mainnet', 'latest', 'curl', 'wget']:
            self.assertNotIn(forbidden, text)

    def test_both_storage_journal_versions_map_to_fixed_locations(self):
        for version, mode, expected in [(1, None, '/mnt/nosana/nosana-runtime'),
                                        (2, 'adopt', '/var/lib/docker/nosana-runtime')]:
            with self.subTest(version=version), tempfile.TemporaryDirectory() as temp:
                path = Path(temp) / 'state.json'
                path.write_text(json.dumps(dict(version=version, mode=mode, stage='complete', token=TOKEN, uuid=UUID)))
                with patch.object(nosana, 'STORAGE_STATE', path), patch.object(nosana, 'secure'), \
                        patch.object(nosana.Setup, 'guard') as guard:
                    setup = nosana.Setup()
                self.assertEqual(str(setup.data), expected)
                guard.assert_called_once()

    def test_incomplete_unknown_or_wrong_identity_storage_is_refused_before_commands(self):
        for change in [{'stage': 'formatting'}, {'version': 99}, {'version': 2, 'mode': 'other'}, {'token': 'bad'}]:
            with self.subTest(change=change), tempfile.TemporaryDirectory() as temp:
                path = Path(temp) / 'state.json'
                state = dict(version=1, stage='complete', token=TOKEN, uuid=UUID)
                state.update(change)
                path.write_text(json.dumps(state))
                with patch.object(nosana, 'STORAGE_STATE', path), patch.object(nosana, 'secure'), \
                        patch.object(nosana, 'run') as command:
                    with self.assertRaises(nosana.Refusal):
                        nosana.Setup()
                    command.assert_not_called()

    def test_guard_rejects_os_docker_or_containerd_before_mutation(self):
        setup = object.__new__(nosana.Setup)
        setup.data = Path('/var/lib/docker/nosana-runtime')
        for root, containerd in [('/var/lib/docker', '/var/lib/docker/nosana-runtime/containerd'),
                                 ('/var/lib/docker/nosana-runtime/docker', '/var/lib/containerd')]:
            outputs = [completed(), completed(root), completed('root = ' + repr(containerd) + '\n')]
            with self.subTest(root=root, containerd=containerd), patch.object(nosana, 'secure'), \
                    patch.object(nosana, 'run', side_effect=outputs) as command:
                with self.assertRaises(nosana.Refusal):
                    setup.guard()
                self.assertEqual(command.call_args_list[0].args[0], ['/usr/local/libexec/nosana-storage-check'])

    def test_command_runner_does_not_forward_wallet_or_remote_docker_environment(self):
        with patch.dict(os.environ, {'SOLANA_WALLET': 'fixture-not-a-secret', 'DOCKER_HOST': 'tcp://wrong', 'NPM_TOKEN': 'fixture'}), \
                patch.object(nosana.subprocess, 'run', return_value=completed()) as process:
            nosana.run(['/usr/bin/true'])
        environment = process.call_args.kwargs['env']
        for forbidden in ['SOLANA_WALLET', 'DOCKER_HOST', 'NPM_TOKEN']:
            self.assertNotIn(forbidden, environment)


if __name__ == '__main__':
    unittest.main()
