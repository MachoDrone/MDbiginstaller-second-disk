"""Journal compatibility and routing: no host commands or files are touched."""
import contextlib
import io
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src import storage


class RoutingTests(unittest.TestCase):
    @contextlib.contextmanager
    def isolated(self, journal):
        with contextlib.ExitStack() as stack:
            for name, result in [('geteuid', 0), ('open', 123456), ('close', None)]:
                stack.enter_context(patch.object(storage.os, name, return_value=result))
            stack.enter_context(patch.dict(os.environ))
            stack.enter_context(patch.object(storage.fcntl, 'flock'))
            stack.enter_context(patch.object(storage, 'existing_state', return_value=journal))
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            stack.enter_context(patch.object(storage, 'run', side_effect=AssertionError('No host commands')))
            yield stack

    def test_completed_v1_keeps_original_verification(self):
        with self.isolated(dict(version=1, stage='complete', uuid='fixture')) as stack:
            checked = stack.enter_context(patch.object(storage, 'verify_configuration'))
            adopted = stack.enter_context(patch.object(storage.adoption, 'verify'))
            storage.main(['--list'])
            checked.assert_called_once()
            adopted.assert_not_called()

    def test_completed_adoption_uses_its_own_paths(self):
        for argument, starts in [('--list', False), ('--verify', True)]:
            with self.subTest(argument=argument), self.isolated(dict(version=2, mode='adopt', stage='complete')) as stack:
                checked = stack.enter_context(patch.object(storage.adoption, 'verify'))
                original = stack.enter_context(patch.object(storage, 'verify_configuration'))
                storage.main([argument])
                self.assertEqual(checked.call_args.kwargs, {'start': starts})
                original.assert_not_called()

    def test_adoption_recovery_never_enters_original_unmounting_recovery(self):
        with self.isolated(dict(version=2, mode='adopt', stage='adopt-fstab-written')) as stack:
            adopted = stack.enter_context(patch.object(storage.adoption, 'recover'))
            original = stack.enter_context(patch.object(storage, 'recover'))
            storage.main(['--recover'])
            adopted.assert_called_once()
            original.assert_not_called()

    def test_incomplete_adoption_cannot_bypass_journal_by_choosing_default(self):
        with self.isolated(dict(version=2, mode='adopt', stage='adopt-approved')) as stack:
            preflight = stack.enter_context(patch.object(storage, 'preflight'))
            with self.assertRaises(storage.Refusal):
                storage.main(['--check-fresh'])
            preflight.assert_not_called()

    def test_prepared_mount_cannot_leave_containerd_on_os_via_default_choice(self):
        with self.isolated(None) as stack:
            stack.enter_context(patch.object(storage, 'preflight'))
            stack.enter_context(patch.object(storage.adoption, 'active_mount', return_value={'target': '/var/lib/docker'}))
            with self.assertRaises(storage.Refusal):
                storage.main(['--check-fresh'])

    def test_prepared_mount_is_offered_before_empty_default_directory_preflight(self):
        with self.isolated(None) as stack:
            choice = {'mode': 'adopt'}
            stack.enter_context(patch.object(storage.adoption, 'inspect', return_value=choice))
            prompt = stack.enter_context(patch.object(storage.adoption, 'prompt'))
            preflight = stack.enter_context(patch.object(storage, 'preflight'))
            storage.main(['--list'])
            prompt.assert_called_once_with(storage, choice, list_only=True)
            preflight.assert_not_called()


if __name__ == '__main__':
    unittest.main()
