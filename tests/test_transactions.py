"""Failure and preservation tests; all devices/services are isolated fakes.

No test runs the storage program on the host, invokes sudo, or mounts a disk.
Only temporary test-directory files are created. Real subprocesses are forbidden.
"""

import contextlib
import copy
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import stat
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src import storage  # noqa: E402

FS_UUID = "22222222-2222-4222-8222-222222222222"
TOKEN = "test-owner-token"


def choice(mode="format"):
    disk = {
        "path": "/dev/sdb", "name": "/dev/sdb", "type": "disk",
        "size": 2_000_000_000_000, "serial": "TEST-DATA-002", "wwn": None,
        "model": "Disposable fixture", "ro": False, "rm": False,
        "tran": "sata", "fstype": None, "uuid": None,
        "mountpoints": [None], "maj:min": "8:16",
    }
    if mode == "reuse":
        disk.update(fstype="ext4", uuid=FS_UUID)
    return {"disk": disk, "device": disk, "mode": mode, "identity": "TEST-DATA-002"}


def completed(args, stdout="", returncode=0, stderr=""):
    return subprocess.CompletedProcess(args, returncode, stdout, stderr)


class ProbeTests(unittest.TestCase):
    def probe_with(self, selected, *, blk_status=2, blk_text="", signatures=None,
                   wipe_status=0, wipe_text=None, ext4_header="Filesystem state: clean\nFilesystem features: has_journal ext_attr extent\n"):
        calls = []
        if wipe_text is None:
            wipe_text = json.dumps({"signatures": [] if signatures is None else signatures})

        def fake_process(args, **kwargs):
            calls.append(args)
            self.assertEqual(args[-1], selected["device"]["path"])
            self.assertEqual(kwargs.get("stdin"), subprocess.DEVNULL)
            if args[0] == "blkid":
                self.assertIn("-p", args, "The probe must bypass cached filesystem observations")
                return completed(args, blk_text, blk_status, "probe failure" if blk_status not in (0, 2) else "")
            if args[0] == "wipefs":
                self.assertFalse(set(args) & {"-a", "--all", "-o", "--offset"}, "The probe must not erase signatures")
                return completed(args, wipe_text, wipe_status, "read error" if wipe_status else "")
            if args[0] == "dumpe2fs":
                self.assertIn("-h", args)
                return completed(args, ext4_header)
            self.fail("Unexpected command during a read-only probe: " + repr(args))

        with patch.object(storage.subprocess, "run", side_effect=fake_process):
            storage.probe(selected)
        return calls

    def test_blank_requires_both_probes_to_report_no_signature(self):
        self.probe_with(choice())

    def test_recognized_filesystem_is_never_format_eligible(self):
        with self.assertRaises(storage.Refusal):
            self.probe_with(choice(), blk_status=0, blk_text="TYPE=ext4\nUUID=" + FS_UUID + "\n")

    def test_low_level_ambiguous_or_failed_probe_refuses(self):
        for status in [1, 4, 8]:
            with self.subTest(status=status), self.assertRaises(storage.Refusal):
                self.probe_with(choice(), blk_status=status)

    def test_no_signature_exit_code_with_metadata_is_not_blank(self):
        with self.assertRaises(storage.Refusal):
            self.probe_with(choice(), blk_status=2, blk_text="UUID=unexpected\n")

    def test_stale_signature_detected_only_by_wipefs_blocks_format(self):
        for kind in ["ext4", "gpt", "dos", "linux_raid_member", "crypto_LUKS"]:
            with self.subTest(kind=kind), self.assertRaises(storage.Refusal):
                self.probe_with(choice(), signatures=[{"type": kind, "uuid": "old", "offset": "0x438"}])

    def test_wipefs_read_error_is_not_empty_disk_evidence(self):
        with self.assertRaises(storage.Refusal):
            self.probe_with(choice(), wipe_status=1)

    def test_missing_or_invalid_signature_inventory_fails_closed(self):
        for payload in ["not json", "{}", '{"signatures": null}', '{"signatures": ""}']:
            with self.subTest(payload=payload):
                with self.assertRaises((storage.Refusal, ValueError, KeyError, TypeError)):
                    self.probe_with(choice(), wipe_text=payload)

    def test_matching_existing_ext4_is_accepted_for_reuse(self):
        self.probe_with(choice("reuse"), blk_status=0, blk_text="TYPE=ext4\nUUID=" + FS_UUID + "\n",
                        signatures=[{"type": "ext4", "uuid": FS_UUID, "offset": "0x438"}])

    def test_changed_filesystem_identity_blocks_reuse(self):
        for text in ["TYPE=ext4\nUUID=other\n", "TYPE=xfs\nUUID=" + FS_UUID + "\n", "UUID=" + FS_UUID + "\n"]:
            with self.subTest(text=text), self.assertRaises(storage.Refusal):
                self.probe_with(choice("reuse"), blk_status=0, blk_text=text,
                                signatures=[{"type": "ext4", "uuid": FS_UUID, "offset": "0x438"}])

    def test_conflicting_or_missing_reuse_signature_blocks(self):
        for signatures in [[], [{"type": "gpt", "uuid": "old", "offset": "0x200"}],
                           [{"type": "ext4", "uuid": FS_UUID, "offset": "0x438"},
                            {"type": "linux_raid_member", "uuid": "old", "offset": "0xffff"}]]:
            with self.subTest(signatures=signatures), self.assertRaises(storage.Refusal):
                self.probe_with(choice("reuse"), blk_status=0, blk_text="TYPE=ext4\nUUID=" + FS_UUID + "\n",
                                signatures=signatures)

    def test_probes_disagreeing_on_reuse_uuid_block(self):
        with self.assertRaises(storage.Refusal):
            self.probe_with(choice("reuse"), blk_status=0, blk_text="TYPE=ext4\nUUID=" + FS_UUID + "\n",
                            signatures=[{"type": "ext4", "uuid": "another-filesystem", "offset": "0x438"}])

    def test_dirty_or_unknown_ext4_state_never_replays_or_repairs(self):
        for header in ["", "Filesystem state: not clean\n", "Filesystem state: clean with errors\n",
                       "Filesystem state: clean\nFilesystem features: has_journal needs_recovery\n"]:
            with self.subTest(header=header), self.assertRaises(storage.Refusal):
                self.probe_with(choice("reuse"), blk_status=0, blk_text="TYPE=ext4\nUUID=" + FS_UUID + "\n",
                                signatures=[{"type": "ext4", "uuid": FS_UUID, "offset": "0x438"}], ext4_header=header)


class RevalidationTests(unittest.TestCase):
    def test_changed_selection_never_reaches_probe_or_write(self):
        original = choice()
        for field, value in [("serial", "REPLACED"), ("path", "/dev/sdc"), ("size", 42),
                             ("maj:min", "8:32"), ("mountpoints", ["/mnt/new"]), ("uuid", FS_UUID)]:
            changed = copy.deepcopy(original)
            changed["disk"][field] = value
            with self.subTest(field=field), patch.object(storage, "inventory", return_value=([changed], [])), \
                    patch.object(storage, "probe") as probe, patch.object(storage, "run") as run:
                with self.assertRaises(storage.Refusal):
                    storage.revalidate(original)
                probe.assert_not_called()
                run.assert_not_called()

    def test_disappeared_or_now_busy_disk_is_refused(self):
        with patch.object(storage, "inventory", return_value=([], [("/dev/sdb", "now mounted")])), \
                patch.object(storage, "probe") as probe:
            with self.assertRaises(storage.Refusal):
                storage.revalidate(choice())
            probe.assert_not_called()

    def test_configure_revalidation_failure_precedes_journal_or_device_writes(self):
        with patch.object(storage, "revalidate", side_effect=storage.Refusal("changed disk")), \
                patch.object(storage, "preflight") as preflight, patch.object(storage, "save") as save, \
                patch.object(storage, "run") as run, patch("builtins.open") as opened:
            with self.assertRaises(storage.Refusal):
                storage.configure(choice())
            preflight.assert_not_called()
            save.assert_not_called()
            run.assert_not_called()
            opened.assert_not_called()


class ConfirmationTests(unittest.TestCase):
    @contextlib.contextmanager
    def isolated_main(self, *, tty=True, inputs=()):
        selected = choice()
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(storage.os, "geteuid", return_value=0))
            stack.enter_context(patch.object(storage.os, "open", return_value=987654))
            stack.enter_context(patch.object(storage.os, "close"))
            stack.enter_context(patch.object(storage.fcntl, "flock"))
            stack.enter_context(patch.dict(os.environ))
            stack.enter_context(patch.object(storage, "existing_state", return_value=None))
            stack.enter_context(patch.object(storage.adoption, "inspect", return_value=None))
            stack.enter_context(patch.object(storage, "preflight"))
            stack.enter_context(patch.object(storage, "inventory", return_value=([selected], [])))
            stack.enter_context(patch.object(storage, "probe"))
            stack.enter_context(patch.object(storage.sys.stdin, "isatty", return_value=tty))
            stack.enter_context(patch("builtins.input", side_effect=inputs))
            stack.enter_context(patch.object(storage, "run", side_effect=AssertionError("Host commands forbidden")))
            configure = stack.enter_context(patch.object(storage, "configure"))
            save = stack.enter_context(patch.object(storage, "save"))
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            yield configure, save, selected

    def test_cancel_and_invalid_menu_input_make_no_changes(self):
        for response in ["", " ", "0", "2", "-1", "all", "/dev/sdb", "1; reboot"]:
            with self.subTest(response=response), self.isolated_main(inputs=[response]) as (configure, save, _):
                with self.assertRaises(storage.Refusal):
                    storage.main([])
                configure.assert_not_called()
                save.assert_not_called()

    def test_missing_wrong_or_case_changed_confirmation_makes_no_changes(self):
        for response in ["", "yes", "ERASE /dev/sdb", "ERASE OS-DISK", "erase TEST-DATA-002", "ERASE TEST-DATA-002 "]:
            with self.subTest(response=response), self.isolated_main(inputs=["1", response]) as (configure, save, _):
                with self.assertRaises(storage.Refusal):
                    storage.main([])
                configure.assert_not_called()
                save.assert_not_called()

    def test_eof_and_interrupt_before_either_prompt_make_no_changes(self):
        for inputs in [[EOFError()], ["1", EOFError()], [KeyboardInterrupt()], ["1", KeyboardInterrupt()]]:
            with self.subTest(inputs=inputs), self.isolated_main(inputs=inputs) as (configure, save, _):
                with self.assertRaises((EOFError, KeyboardInterrupt)):
                    storage.main([])
                configure.assert_not_called()
                save.assert_not_called()

    def test_noninteractive_input_never_authorizes_changes(self):
        with self.isolated_main(tty=False, inputs=["1", "ERASE TEST-DATA-002"]) as (configure, save, _):
            with self.assertRaises(storage.Refusal):
                storage.main([])
            configure.assert_not_called()
            save.assert_not_called()

    def test_exact_confirmation_reaches_only_the_selected_candidate(self):
        with self.isolated_main(inputs=["1", "ERASE TEST-DATA-002"]) as (configure, save, selected):
            storage.main([])
            configure.assert_called_once_with(selected)
            save.assert_not_called()

    def test_list_mode_never_requests_or_applies_changes(self):
        with self.isolated_main(tty=False) as (configure, save, _):
            storage.main(["--list"])
            configure.assert_not_called()
            save.assert_not_called()


class TemporaryTransactionTests(unittest.TestCase):
    def setUp(self):
        self.sandbox = tempfile.TemporaryDirectory(prefix="nosana-transaction-test-")
        self.addCleanup(self.sandbox.cleanup)
        self.root = Path(self.sandbox.name)
        self.mount = self.root / "mount"
        self.mount.mkdir()
        self.data = self.mount / "nosana-runtime"
        self.journal = self.root / "state.json"
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        for name, value in [("MOUNT", self.mount), ("DATA", self.data), ("STATE", self.journal),
                            ("UNIT", self.root / "mount.unit"), ("GUARD", self.root / "guard")]:
            self.stack.enter_context(patch.object(storage, name, value))
        self.stack.enter_context(patch.object(storage, "safe_path", side_effect=self.safe_temporary_path))
        self.stack.enter_context(patch.object(storage.subprocess, "run", side_effect=AssertionError("Real commands forbidden")))
        self.stack.enter_context(contextlib.redirect_stdout(io.StringIO()))

    def safe_temporary_path(self, path):
        path.relative_to(self.root)
        for ancestor in [path, *path.parents]:
            if ancestor == self.root:
                return
            if ancestor.is_symlink():
                raise storage.Refusal("Test detected symlink: " + str(ancestor))
        self.fail("Path escaped temporary sandbox")

    def state(self, stage="mounting"):
        return {"version": 1, "stage": stage, "uuid": FS_UUID, "token": TOKEN, "changes": []}

    def owned(self, state, name="config", content=b"installer configuration\n"):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        state["changes"].append({"path": str(path), "sha256": hashlib.sha256(content).hexdigest()})
        return path

    def test_owned_file_never_overwrites_existing_configuration(self):
        state = self.state()
        path = self.root / "existing.conf"
        path.write_bytes(b"keep administrator settings\n")
        with self.assertRaises(storage.Refusal):
            storage.write_owned(state, path, "replacement")
        self.assertEqual(path.read_bytes(), b"keep administrator settings\n")
        self.assertEqual(state["changes"], [])
        self.assertFalse(self.journal.exists())

    def test_journal_is_durable_before_owned_file_appears(self):
        state = self.state()
        path = self.root / "new.conf"
        payload = "new configuration\n"
        real_atomic = storage.atomic
        witnessed = []

        def checking_atomic(target, data, mode=0o600, **kwargs):
            if target == path:
                journal = json.loads(self.journal.read_text())
                self.assertFalse(path.exists())
                self.assertIn({"path": str(path), "sha256": hashlib.sha256(payload.encode()).hexdigest()}, journal["changes"])
                witnessed.append(True)
            return real_atomic(target, data, mode, **kwargs)

        with patch.object(storage, "atomic", side_effect=checking_atomic):
            storage.write_owned(state, path, payload)
        self.assertEqual(witnessed, [True])
        self.assertEqual(path.read_text(), payload)

    def test_journal_write_failure_cannot_create_configuration(self):
        state = self.state()
        path = self.root / "new.conf"
        with patch.object(storage, "save", side_effect=OSError("simulated full OS disk")):
            with self.assertRaises(OSError):
                storage.write_owned(state, path, "configuration")
        self.assertFalse(path.exists())

    def test_config_write_failure_leaves_recoverable_intent(self):
        state = self.state()
        path = self.root / "new.conf"
        real_atomic = storage.atomic

        def fail_config(target, data, mode=0o600, **kwargs):
            if target == path:
                raise OSError("simulated config write failure")
            return real_atomic(target, data, mode, **kwargs)

        with patch.object(storage, "atomic", side_effect=fail_config), self.assertRaises(OSError):
            storage.write_owned(state, path, "configuration")
        self.assertFalse(path.exists())
        self.assertEqual(json.loads(self.journal.read_text())["changes"][0]["path"], str(path))

    def test_admin_file_created_after_intent_is_not_overwritten(self):
        state = self.state()
        path = self.root / "new.conf"
        real_save = storage.save

        def racing_save(value):
            real_save(value)
            path.write_bytes(b"administrator raced with setup\n")

        with patch.object(storage, "save", side_effect=racing_save):
            with self.assertRaises((storage.Refusal, FileExistsError)):
                storage.write_owned(state, path, "installer replacement")
        self.assertEqual(path.read_bytes(), b"administrator raced with setup\n")

    @contextlib.contextmanager
    def recovery_environment(self, mounted=True, fail_start=False):
        commands = []
        mount_state = {"mounted": mounted}

        def fake_run(args, allowed=(0,), **kwargs):
            commands.append(args)
            if args[0] == "findmnt":
                if not mount_state["mounted"]:
                    return completed(args, returncode=1)
                if "--json" in args:
                    return completed(args, json.dumps({"filesystems": [{"target": str(self.mount), "uuid": FS_UUID,
                                     "fstype": "ext4", "options": "rw,nosuid,nodev"}]}))
                return completed(args, str(self.mount))
            if args[:2] == ["systemctl", "start"]:
                if fail_start:
                    raise storage.Refusal("disk is not connected")
                mount_state["mounted"] = True
                return completed(args)
            if args[:2] == ["systemctl", "stop"]:
                mount_state["mounted"] = False
                return completed(args)
            if args[:2] == ["systemctl", "daemon-reload"]:
                return completed(args)
            self.fail("Unexpected recovery command: " + repr(args))

        with patch.object(storage.shutil, "which", return_value=None), patch.object(storage, "run", side_effect=fake_run):
            yield commands

    def test_recovery_preserves_all_config_if_any_owned_file_changed(self):
        state = self.state()
        first = self.owned(state, "first.conf")
        changed = self.owned(state, "changed.conf")
        changed.write_bytes(b"new administrator settings\n")
        with self.recovery_environment() as commands, self.assertRaises(storage.Refusal):
            storage.recover(state)
        self.assertTrue(first.exists())
        self.assertEqual(changed.read_bytes(), b"new administrator settings\n")
        self.assertEqual(commands, [])

    def test_recovery_removes_only_recorded_config_and_preserves_disk_files(self):
        state = self.state("mounting")
        owned = self.owned(state)
        unowned = self.root / "administrator.conf"
        unowned.write_bytes(b"do not touch")
        self.data.mkdir()
        sentinel = self.data / "preexisting-user-file"
        sentinel.write_bytes(b"irreplaceable file")
        with self.recovery_environment():
            storage.recover(state)
        self.assertFalse(owned.exists())
        self.assertEqual(unowned.read_bytes(), b"do not touch")
        self.assertEqual(sentinel.read_bytes(), b"irreplaceable file")
        self.assertEqual(json.loads(self.journal.read_text())["stage"], "recovered")

    def test_unmarked_runtime_data_is_preserved_and_recovery_stops(self):
        state = self.state("creating-data-directories")
        config = self.owned(state)
        self.data.mkdir()
        sentinel = self.data / "user-file"
        sentinel.write_bytes(b"unknown owner")
        with self.recovery_environment(), self.assertRaises(storage.Refusal):
            storage.recover(state)
        self.assertEqual(sentinel.read_bytes(), b"unknown owner")
        self.assertTrue(config.exists())

    def test_nonempty_runtime_data_is_never_recursively_deleted(self):
        state = self.state("creating-data-directories")
        config = self.owned(state)
        self.data.mkdir()
        (self.data / ".owner").write_text(TOKEN + "\n")
        (self.data / "docker").mkdir()
        (self.data / "containerd").mkdir()
        sentinel = self.data / "docker" / "volume-data"
        sentinel.write_bytes(b"live workload data")
        with self.recovery_environment(), self.assertRaises((storage.Refusal, OSError)):
            storage.recover(state)
        self.assertEqual(sentinel.read_bytes(), b"live workload data")
        self.assertEqual((self.data / ".owner").read_text(), TOKEN + "\n")
        self.assertTrue(config.exists())

    def test_symlinked_runtime_directory_is_preserved(self):
        state = self.state("creating-data-directories")
        config = self.owned(state)
        target = self.root / "unowned-data"
        target.mkdir()
        (target / ".owner").write_text(TOKEN + "\n")
        (target / "docker").mkdir()
        self.data.symlink_to(target, target_is_directory=True)
        with self.recovery_environment(), self.assertRaises(storage.Refusal):
            storage.recover(state)
        self.assertTrue(self.data.is_symlink())
        self.assertTrue((target / "docker").is_dir())
        self.assertTrue(config.exists())

    def test_completed_install_is_not_automatically_rolled_back(self):
        state = self.state("complete")
        config = self.owned(state)
        with self.recovery_environment() as commands, self.assertRaises(storage.Refusal):
            storage.recover(state)
        self.assertTrue(config.exists())
        self.assertEqual(commands, [])

    def test_installed_runtime_blocks_automatic_recovery(self):
        state = self.state()
        config = self.owned(state)
        with patch.object(storage.shutil, "which", return_value="/usr/bin/dockerd"), self.assertRaises(storage.Refusal):
            storage.recover(state)
        self.assertTrue(config.exists())

    def test_recovery_after_reboot_remounts_before_cleaning_owned_directories(self):
        state = self.state("creating-data-directories")
        unit = self.owned(state, "mount.unit")
        self.data.mkdir()
        (self.data / ".owner").write_text(TOKEN + "\n")
        (self.data / "docker").mkdir()
        (self.data / "containerd").mkdir()
        sentinel = self.mount / "preexisting-user-file"
        sentinel.write_bytes(b"preserve existing disk content")
        with self.recovery_environment(mounted=False) as commands:
            storage.recover(state)
        self.assertIn(["systemctl", "start", "mnt-nosana.mount"], commands)
        self.assertFalse(unit.exists())
        self.assertFalse(self.data.exists())
        self.assertEqual(sentinel.read_bytes(), b"preserve existing disk content")
        self.assertEqual(json.loads(self.journal.read_text())["stage"], "recovered")

    def test_missing_disk_during_recovery_preserves_retry_state_and_config(self):
        state = self.state("creating-data-directories")
        unit = self.owned(state, "mount.unit")
        storage.save(state)
        before = self.journal.read_bytes()
        with self.recovery_environment(mounted=False, fail_start=True), self.assertRaises(storage.Refusal):
            storage.recover(state)
        self.assertTrue(unit.exists())
        self.assertEqual(self.journal.read_bytes(), before)

    def test_recovery_killed_after_unit_removal_can_retry_without_remount(self):
        state = self.state("mounting")
        config = self.owned(state, "config")
        unit = self.owned(state, "mount.unit")
        real_unlink = Path.unlink

        def interrupt_unlink(path, *args, **kwargs):
            if path == config:
                raise KeyboardInterrupt("simulated termination during cleanup")
            return real_unlink(path, *args, **kwargs)

        with self.recovery_environment(mounted=False), patch.object(Path, "unlink", interrupt_unlink), \
                self.assertRaises(KeyboardInterrupt):
            storage.recover(state)
        self.assertFalse(unit.exists())
        self.assertTrue(config.exists())
        persisted = json.loads(self.journal.read_text())
        self.assertEqual(persisted["stage"], "recovering")
        with self.recovery_environment(mounted=False) as commands:
            storage.recover(persisted)
        self.assertNotIn(["systemctl", "start", "mnt-nosana.mount"], commands)
        self.assertFalse(config.exists())
        self.assertEqual(json.loads(self.journal.read_text())["stage"], "recovered")

    def test_retry_tolerates_already_removed_dropin_parent(self):
        state = self.state("recovering")
        vanished = self.root / "docker.service.d" / "20-nosana-storage.conf"
        state["changes"].append({"path": str(vanished), "sha256": hashlib.sha256(b"old config").hexdigest()})
        with self.recovery_environment(mounted=False):
            storage.recover(state)
        self.assertEqual(json.loads(self.journal.read_text())["stage"], "recovered")

    def test_recovered_transaction_is_a_noop(self):
        state = self.state("recovered")
        with self.recovery_environment(mounted=False) as commands:
            storage.recover(state)
        self.assertEqual(commands, [])


class ConfigurationVerificationTests(unittest.TestCase):
    def verify(self, *, docker=None, missing_guard=False, changed_guard=False):
        original_docker = json.dumps({"data-root": str(storage.DATA / "docker")})
        original_guard = b"original guard\n"
        files = {
            "/etc/docker/daemon.json": original_docker.encode() if docker is None else json.dumps(docker).encode(),
            "/usr/local/libexec/nosana-storage-check": b"changed guard\n" if changed_guard else original_guard,
        }
        if missing_guard:
            del files["/usr/local/libexec/nosana-storage-check"]
        state = {"changes": [
            {"path": "/etc/docker/daemon.json", "sha256": hashlib.sha256(original_docker.encode()).hexdigest()},
            {"path": "/usr/local/libexec/nosana-storage-check", "sha256": hashlib.sha256(original_guard).hexdigest()},
        ]}

        class FakePath:
            def __init__(self, value):
                self.value = value

            def __str__(self):
                return self.value

            def is_file(self):
                return self.value in files

            def read_bytes(self):
                if self.value not in files:
                    raise FileNotFoundError(self.value)
                return files[self.value]

            def read_text(self):
                return self.read_bytes().decode()

        with patch.object(storage, "Path", FakePath), patch.object(storage, "safe_path"), \
                patch.object(storage.shutil, "which", return_value=None), \
                patch.object(storage, "run", side_effect=AssertionError("Verification must not run host commands")):
            storage.verify_configuration(state)

    def test_unchanged_configuration_is_accepted(self):
        self.verify()

    def test_nvidia_additions_preserve_valid_docker_storage(self):
        self.verify(docker={"data-root": str(storage.DATA / "docker"),
                            "runtimes": {"nvidia": {"path": "nvidia-container-runtime", "runtimeArgs": []}}})

    def test_removed_or_changed_data_root_is_refused(self):
        for docker in [{}, {"data-root": "/var/lib/docker"}]:
            with self.subTest(docker=docker), self.assertRaises(storage.Refusal):
                self.verify(docker=docker)

    def test_changed_or_missing_guard_is_refused(self):
        for settings in [{"missing_guard": True}, {"changed_guard": True}]:
            with self.subTest(settings=settings), self.assertRaises(storage.Refusal):
                self.verify(**settings)


class GuardTests(unittest.TestCase):
    def execute_guard(self, *, mounts=None, mount_error=False, marker=TOKEN + "\n", overrides=None, optimize=0):
        if mounts is None:
            mounts = [{"target": "/mnt/nosana", "uuid": FS_UUID, "fstype": "ext4", "options": "rw,nosuid,nodev"}]
        overrides = overrides or {}
        checks = []

        class FakePath(PurePosixPath):
            def lstat(self):
                value = overrides.get(str(self))
                if isinstance(value, BaseException):
                    raise value
                if value is not None:
                    return value
                mode = stat.S_IFREG | 0o600 if self.name == ".owner" else stat.S_IFDIR | 0o700
                return SimpleNamespace(st_mode=mode, st_uid=0)

            def read_text(self):
                if self.name != ".owner":
                    raise AssertionError("Unexpected guard file read")
                return marker

        def fake_process(args, **kwargs):
            checks.append(args)
            self.assertEqual(args[0], "/usr/bin/findmnt")
            self.assertIn("--mountpoint", args)
            if mount_error:
                raise subprocess.CalledProcessError(1, args)
            return completed(args, json.dumps({"filesystems": mounts}))

        code = storage.guard_source(FS_UUID, TOKEN)
        with patch("subprocess.run", side_effect=fake_process), patch("pathlib.Path", FakePath), \
                contextlib.redirect_stderr(io.StringIO()):
            exec(compile(code, "<generated-storage-guard>", "exec", optimize=optimize), {})
        self.assertEqual(len(checks), 1)

    def assert_guard_refuses(self, **kwargs):
        with self.assertRaises(SystemExit) as caught:
            self.execute_guard(**kwargs)
        self.assertEqual(caught.exception.code, 1)

    def test_correct_mounted_owned_storage_is_accepted(self):
        self.execute_guard()

    def test_missing_or_ambiguous_mount_is_refused(self):
        self.assert_guard_refuses(mount_error=True)
        self.assert_guard_refuses(mounts=[])
        entry = {"target": "/mnt/nosana", "uuid": FS_UUID, "fstype": "ext4", "options": "rw"}
        self.assert_guard_refuses(mounts=[entry, entry])

    def test_wrong_identity_target_type_or_readonly_mount_is_refused(self):
        base = {"target": "/mnt/nosana", "uuid": FS_UUID, "fstype": "ext4", "options": "rw,nosuid,nodev"}
        for field, value in [("target", "/"), ("uuid", "wrong-disk"), ("fstype", "xfs"), ("options", "ro,nosuid,nodev")]:
            with self.subTest(field=field):
                self.assert_guard_refuses(mounts=[dict(base, **{field: value})])

    def test_missing_or_wrong_ownership_marker_is_refused(self):
        self.assert_guard_refuses(marker="another-installation\n")
        self.assert_guard_refuses(overrides={"/mnt/nosana/nosana-runtime/.owner": FileNotFoundError("missing marker")})

    def test_symlink_unowned_or_writable_storage_is_refused(self):
        for status in [SimpleNamespace(st_mode=stat.S_IFLNK | 0o777, st_uid=0),
                       SimpleNamespace(st_mode=stat.S_IFDIR | 0o700, st_uid=1000),
                       SimpleNamespace(st_mode=stat.S_IFDIR | 0o777, st_uid=0)]:
            with self.subTest(status=status):
                self.assert_guard_refuses(overrides={"/mnt/nosana/nosana-runtime/docker": status})

    def test_missing_runtime_directory_is_refused(self):
        self.assert_guard_refuses(overrides={"/mnt/nosana/nosana-runtime/containerd": FileNotFoundError("missing directory")})

    def test_python_optimization_cannot_disable_mount_identity_checks(self):
        for optimize in [1, 2]:
            with self.subTest(optimize=optimize):
                self.assert_guard_refuses(mounts=[{"target": "/mnt/nosana", "uuid": "wrong-disk",
                                                  "fstype": "ext4", "options": "ro"}], optimize=optimize)


if __name__ == "__main__":
    unittest.main()
