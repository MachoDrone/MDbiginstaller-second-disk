"""Public artifact and measured Docker/containerd output contracts."""
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src import storage


class RuntimeVerificationTests(unittest.TestCase):
    def verify(self, dump, docker_root='/mnt/nosana/nosana-runtime/docker'):
        def command(args):
            if args == ['containerd', 'config', 'dump']:
                return subprocess.CompletedProcess(args, 0, dump, '')
            if args == ['docker', 'info', '--format', '{{.DockerRootDir}}']:
                return subprocess.CompletedProcess(args, 0, docker_root + '\n', '')
            self.fail('Unexpected runtime command: ' + repr(args))
        with patch.object(storage.shutil, 'which', return_value='/usr/bin/fake'), patch.object(storage, 'run', side_effect=command):
            storage.verify_runtime()

    def test_containerd_2_4_measured_single_quote_output(self):
        self.verify("version = 4\nroot = '/mnt/nosana/nosana-runtime/containerd'\nstate = '/run/containerd'\n[grpc]\n")

    def test_double_quote_toml_output(self):
        self.verify('version = 2\nroot = "/mnt/nosana/nosana-runtime/containerd"\n')

    def test_default_containerd_root_is_refused(self):
        with self.assertRaises(storage.Refusal):
            self.verify("version = 4\nroot = '/var/lib/containerd'\n")

    def test_plugin_root_does_not_substitute_for_runtime_root(self):
        with self.assertRaises(storage.Refusal):
            self.verify("version = 4\n[plugins.example]\nroot = '/mnt/nosana/nosana-runtime/containerd'\n")

    def test_running_docker_root_must_match(self):
        with self.assertRaises(storage.Refusal):
            self.verify("root = '/mnt/nosana/nosana-runtime/containerd'\n", '/var/lib/docker')


class BundleTests(unittest.TestCase):
    def test_generated_artifact_is_current(self):
        subprocess.run([sys.executable, str(ROOT / 'tools/build.py'), '--check'], check=True)

    def test_shell_syntax(self):
        subprocess.run(['bash', '-n', str(ROOT / 'installer-beta.sh')], check=True)

    def test_help_does_not_require_privilege_or_a_terminal(self):
        result = subprocess.run(['bash', str(ROOT / 'installer-beta.sh'), '--help'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0)
        self.assertIn('--recover-storage', result.stdout)

    def test_noninteractive_invocation_refuses_before_installation(self):
        result = subprocess.run(['bash', str(ROOT / 'installer-beta.sh')], input='', capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('STOP:', result.stderr)
        self.assertNotIn('package installation', result.stdout)

    def test_unknown_arguments_refuse(self):
        result = subprocess.run(['bash', str(ROOT / 'installer-beta.sh'), '--force'], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)


if __name__ == '__main__':
    unittest.main()
