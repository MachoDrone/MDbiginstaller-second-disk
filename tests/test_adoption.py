"""Independent RC2 requirements for adopting prepared /var/lib/docker storage.

Tests use synthetic inventories and temporary filesystem fixtures. They never
mount/unmount/format host devices or execute the real installer on the host.
"""
import base64
import contextlib
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src import adoption, storage  # noqa: E402

FS_UUID = "22222222-2222-4222-8222-222222222222"
ROOT_UUID = "11111111-1111-4111-8111-111111111111"
TARGET = "/var/lib/docker"
ROOT_LINE = "UUID=" + ROOT_UUID + " / ext4 defaults 0 1\n"
DATA_LINE = "UUID=" + FS_UUID + " /var/lib/docker ext4 defaults 0 2\n"


def device(path, kind="disk", **changes):
    row = {
        "name": path, "path": path, "type": kind, "size": 128 * 2**30,
        "model": "Fixture data disk", "serial": "ADOPTION-DATA" if kind == "disk" else None,
        "wwn": None, "ro": False, "rm": False, "tran": "sata",
        "fstype": "ext4", "uuid": FS_UUID, "label": "Prepared Data",
        "partuuid": None, "partlabel": None, "mountpoints": [TARGET], "maj:min": "8:16",
    }
    row.update(changes)
    return row


def snapshot():
    root = device("/dev/sda", serial="ADOPTION-OS", fstype=None, uuid=None,
                  label=None, mountpoints=[None], **{"maj:min": "8:0"})
    root["children"] = [device("/dev/sda1", "part", uuid=ROOT_UUID, label="root",
                               mountpoints=["/"], **{"maj:min": "8:1"})]
    return {
        "tree": {"blockdevices": [root, device("/dev/sdb")]},
        "mount": {"target": TARGET, "source": "/dev/sdb", "uuid": FS_UUID,
                  "fstype": "ext4", "options": "rw,relatime", "fsroot": "/", "maj:min": "8:16"},
        "fstab": ROOT_LINE + DATA_LINE,
        "crypttab": "", "held": set(), "swaps": set(), "nested_mounts": [],
    }


class FstabPlanningTests(unittest.TestCase):
    refs = {"/dev/sdb", "UUID=" + FS_UUID, "LABEL=Prepared Data"}

    def plan(self, text):
        return adoption.plan_fstab(text, self.refs, FS_UUID)

    def selected_fields(self, text):
        lines = [line.split() for line in text.splitlines()
                 if line.strip() and not line.lstrip().startswith("#")]
        entries = [fields for fields in lines if len(fields) >= 2 and fields[1] == TARGET]
        self.assertEqual(len(entries), 1)
        return entries[0]

    def assert_protected(self, output):
        fields = self.selected_fields(output)
        self.assertEqual(fields[0], "UUID=" + FS_UUID)
        self.assertEqual(fields[2], "ext4")
        options = fields[3].split(",")
        for option in ["nofail", "x-systemd.device-timeout=10s", "x-systemd.mount-timeout=15s"]:
            self.assertEqual(options.count(option), 1)
        return options

    def test_adds_persistence_when_mount_has_no_fstab_entry(self):
        before = "# existing boot layout\n" + ROOT_LINE
        result = self.plan(before)
        self.assertTrue(result.startswith(before))
        self.assert_protected(result)

    def test_existing_selected_entry_is_normalized_and_safe_options_preserved(self):
        line = "/dev/sdb /var/lib/docker ext4 defaults,nosuid,nodev,noatime 0 2\n"
        result = self.plan(ROOT_LINE + line)
        options = self.assert_protected(result)
        for option in ["nosuid", "nodev", "noatime"]:
            self.assertIn(option, options)

    def test_unrelated_bytes_comments_and_whitespace_are_preserved(self):
        before = "# café: keep this spacing\r\n\t" + ROOT_LINE + "\n"
        after = "# unrelated archive\nUUID=3333 /srv/archive ext4 noauto,nodev 0 2\n\n"
        result = self.plan(before + DATA_LINE + after)
        self.assertTrue(result.startswith(before))
        self.assertTrue(result.endswith(after))
        self.assert_protected(result)

    def test_escaped_label_source_matches_prepared_filesystem(self):
        result = self.plan(ROOT_LINE + r"LABEL=Prepared\040Data /var/lib/docker ext4 defaults 0 2" + "\n")
        self.assert_protected(result)

    def test_normalizing_an_already_protected_entry_is_idempotent(self):
        once = self.plan(ROOT_LINE + DATA_LINE)
        self.assertEqual(self.plan(once), once)

    def test_foreign_source_at_adoption_target_is_refused(self):
        with self.assertRaises(ValueError):
            self.plan(ROOT_LINE + "UUID=foreign /var/lib/docker ext4 defaults 0 2\n")

    def test_selected_source_configured_at_another_target_is_refused(self):
        with self.assertRaises(ValueError):
            self.plan(ROOT_LINE + "UUID=" + FS_UUID + " /srv/archive ext4 defaults 0 2\n")

    def test_duplicate_selected_source_or_target_is_refused(self):
        for extra in [DATA_LINE, "/dev/sdb /srv/archive ext4 defaults 0 2\n",
                      "UUID=foreign /var/lib/docker ext4 defaults 0 2\n"]:
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                self.plan(ROOT_LINE + DATA_LINE + extra)

    def test_non_ext4_or_exotic_options_are_refused(self):
        for fstype, options in [("xfs", "defaults"), ("auto", "defaults"), ("ext4", "bind"),
                               ("ext4", "defaults,ro"),
                               ("ext4", "defaults,_netdev"), ("ext4", "defaults,x-systemd.automount"),
                               ("ext4", "defaults,x-systemd.requires=other.service")]:
            with self.subTest(fstype=fstype, options=options), self.assertRaises(ValueError):
                self.plan(ROOT_LINE + "UUID=" + FS_UUID + " /var/lib/docker " + fstype + " " + options + " 0 2\n")

    def test_noncanonical_or_nested_mount_targets_are_refused(self):
        for target in ["/var/lib/docker/", "/var/lib/./docker", "/var/lib/docker/../docker",
                       "/var/lib/docker/cache", "/var/lib/other/../docker/cache"]:
            with self.subTest(target=target), self.assertRaises(ValueError):
                self.plan(ROOT_LINE + "UUID=" + FS_UUID + " " + target + " ext4 defaults 0 2\n")

    def test_malformed_selected_fstab_tail_is_refused(self):
        for tail in ["0 3", "-1 2", "zero 2", "0 2 extra", "0 2 1"]:
            with self.subTest(tail=tail), self.assertRaises(ValueError):
                self.plan(ROOT_LINE + "UUID=" + FS_UUID + " /var/lib/docker ext4 defaults " + tail + "\n")

    def test_non_uuid_identity_cannot_be_written_into_fstab(self):
        for value in ["-" * 36, "a" * 36, "222222222222-4222-8222-222222222222"]:
            with self.subTest(uuid=value), self.assertRaises(ValueError):
                adoption.plan_fstab(ROOT_LINE, self.refs, value)


class AdoptionAssessmentTests(unittest.TestCase):
    def test_exact_distinct_whole_disk_mount_is_adopted(self):
        observed = snapshot()
        result = adoption.assess(observed, storage.candidates)
        self.assertEqual(result["mode"], "adopt")
        self.assertEqual(result["device"]["path"], "/dev/sdb")
        self.assertEqual(result["disk"]["path"], "/dev/sdb")
        self.assertEqual(result["uuid"], FS_UUID)
        self.assertEqual(result["fstab_before"], observed["fstab"])
        self.assertIn("nofail", result["fstab_after"])

    def test_exact_distinct_single_partition_mount_is_adopted(self):
        observed = snapshot()
        part = device("/dev/sdb1", "part", **{"maj:min": "8:17"})
        disk = device("/dev/sdb", fstype=None, uuid=None, label=None, mountpoints=[None], children=[part])
        observed["tree"]["blockdevices"][1] = disk
        observed["mount"].update(source="/dev/sdb1", **{"maj:min": "8:17"})
        result = adoption.assess(observed, storage.candidates)
        self.assertEqual(result["disk"]["path"], "/dev/sdb")
        self.assertEqual(result["device"]["path"], "/dev/sdb1")

    def test_parent_disk_configuration_is_not_ignored_when_adopting_partition(self):
        for owner in ["fstab", "crypttab"]:
            with self.subTest(owner=owner):
                observed = snapshot()
                part = device("/dev/sdb1", "part", **{"maj:min": "8:17"})
                observed["tree"]["blockdevices"][1] = device("/dev/sdb", fstype=None, uuid=None,
                                                              label=None, mountpoints=[None], children=[part])
                observed["mount"].update(source="/dev/sdb1", **{"maj:min": "8:17"})
                if owner == "fstab":
                    observed["fstab"] += "/dev/sdb /srv/other ext4 defaults 0 2\n"
                else:
                    observed["crypttab"] = "other /dev/sdb none luks\n"
                with self.assertRaises(ValueError):
                    adoption.assess(observed, storage.candidates)

    def test_assessment_does_not_mutate_original_inventory(self):
        observed = snapshot()
        original = copy.deepcopy(observed)
        adoption.assess(observed, storage.candidates)
        self.assertEqual(observed, original)

    def test_plain_os_directory_is_not_a_prepared_mount(self):
        observed = snapshot()
        observed["mount"].update(target="/", source="/dev/sda1", uuid=ROOT_UUID, **{"maj:min": "8:1"})
        with self.assertRaises(ValueError):
            adoption.assess(observed, storage.candidates)

    def test_spare_partition_on_os_disk_is_not_adopted(self):
        observed = snapshot()
        observed["tree"]["blockdevices"] = [observed["tree"]["blockdevices"][0]]
        observed["tree"]["blockdevices"][0]["children"].append(device("/dev/sda2", "part", **{"maj:min": "8:2"}))
        observed["mount"].update(source="/dev/sda2", **{"maj:min": "8:2"})
        with self.assertRaises(ValueError):
            adoption.assess(observed, storage.candidates)

    def test_wrong_mount_identity_type_subdirectory_or_readonly_is_refused(self):
        for field, value in [("target", "/mnt/archive"), ("source", "/dev/sdc"),
                             ("uuid", "other-uuid"), ("fstype", "xfs"), ("options", "ro,relatime"),
                             ("fsroot", "/subdirectory"), ("maj:min", "8:32")]:
            with self.subTest(field=field):
                observed = snapshot()
                observed["mount"][field] = value
                with self.assertRaises(ValueError):
                    adoption.assess(observed, storage.candidates)

    def test_other_mountpoint_on_same_filesystem_is_not_cleared(self):
        observed = snapshot()
        observed["tree"]["blockdevices"][1]["mountpoints"].append("/srv/also-mounted")
        with self.assertRaises(ValueError):
            adoption.assess(observed, storage.candidates)

    def test_nested_mounts_are_not_hidden_by_adoption(self):
        observed = snapshot()
        observed["nested_mounts"] = [TARGET + "/some-other-filesystem"]
        with self.assertRaises(ValueError):
            adoption.assess(observed, storage.candidates)

    def test_swap_holders_and_crypttab_references_still_block_adoption(self):
        for field, value in [("held", {"/dev/sdb"}), ("swaps", {"/dev/sdb"}),
                             ("crypttab", "encrypted UUID=" + FS_UUID + " none luks\n")]:
            with self.subTest(field=field):
                observed = snapshot()
                observed[field] = value
                with self.assertRaises(ValueError):
                    adoption.assess(observed, storage.candidates)

    def test_competing_fstab_mount_is_not_discarded_during_policy_check(self):
        observed = snapshot()
        observed["fstab"] += "/dev/sdb /srv/archive ext4 defaults 0 2\n"
        with self.assertRaises(ValueError):
            adoption.assess(observed, storage.candidates)

    def test_duplicate_uuid_or_unresolved_system_ancestry_refuses(self):
        for ambiguous in [True, False]:
            with self.subTest(duplicate_uuid=ambiguous):
                observed = snapshot()
                if ambiguous:
                    observed["tree"]["blockdevices"].append(device("/dev/sdc", serial="OTHER-DISK", mountpoints=[None]))
                else:
                    observed["tree"]["blockdevices"] = observed["tree"]["blockdevices"][1:]
                with self.assertRaises(ValueError):
                    adoption.assess(observed, storage.candidates)


class AdoptionFilesystemTests(unittest.TestCase):
    """Actual temp-file transactions with an independently simulated mount."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="nosana-adoption-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.mount = self.root / "var/lib/docker"
        self.mount.mkdir(parents=True)
        self.data = self.mount / "nosana-runtime"
        self.fstab = self.root / "etc/fstab"
        self.fstab.parent.mkdir(parents=True)
        self.before = ("# original café boot configuration\r\n" + ROOT_LINE + "UUID=" + FS_UUID + " " +
                       str(self.mount) + " ext4 defaults,nodev 0 2\n# preserve this final comment\n").encode()
        self.fstab.write_bytes(self.before)
        self.fstab.chmod(0o640)
        self.journal = self.root / "var/lib/nosana-storage-setup/state.json"
        self.guard = self.root / "usr/local/libexec/nosana-storage-check"
        self.sentinel = self.mount / "operator-notes.txt"
        self.sentinel.write_bytes(b"Existing user files stay exactly where they are.\n")
        (self.mount / "lost+found").mkdir()
        self.sentinel_status = self.sentinel.stat()
        self.mounted = True
        self.commands = []
        self.chowns = []
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        for name, value in [("MOUNT", self.mount), ("DATA", self.data), ("FSTAB", self.fstab),
                            ("MOUNT_DROPIN", self.root / "etc/systemd/system/var-lib-docker.mount.d/20-nosana-storage.conf")]:
            self.stack.enter_context(patch.object(adoption, name, value))
        self.stack.enter_context(patch.object(adoption, "Path", self.virtual_path))
        self.stack.enter_context(patch.object(storage, "STATE", self.journal))
        self.stack.enter_context(patch.object(storage, "GUARD", self.guard))
        self.stack.enter_context(patch.object(storage, "safe_path", side_effect=self.safe_path))
        self.stack.enter_context(patch.object(storage, "run", side_effect=self.fake_run))
        self.real_preflight = storage.preflight
        self.stack.enter_context(patch.object(storage, "preflight"))
        self.stack.enter_context(patch.object(storage, "probe"))
        self.stack.enter_context(patch.object(adoption.shutil, "which", return_value=None))
        self.stack.enter_context(patch.object(adoption.os, "chown", side_effect=self.chown))
        self.stack.enter_context(patch.object(storage.subprocess, "run", side_effect=AssertionError("Host processes forbidden")))
        self.stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        self.stack.enter_context(contextlib.redirect_stderr(io.StringIO()))
        self.observed = snapshot()
        self.observed["tree"]["blockdevices"][1]["mountpoints"] = [str(self.mount)]
        self.observed["mount"]["target"] = str(self.mount)
        self.observed["fstab"] = self.before.decode()
        self.selected = adoption.assess(self.observed, storage.candidates)
        for name in ("sda", "sda1", "sdb"):
            (self.root / "sys/class/block" / name / "holders").mkdir(parents=True)

    def virtual_path(self, value):
        path = Path(value)
        if path == self.root or self.root in path.parents or not path.is_absolute():
            return path
        return self.root.joinpath(*path.parts[1:])

    def safe_path(self, path):
        path.relative_to(self.root)
        for item in [path, *path.parents]:
            if item == self.root:
                return
            if item.is_symlink():
                raise storage.Refusal("Symlink rejected by test sandbox: " + str(item))
        self.fail("Unsafe path outside temporary test tree")

    def chown(self, path, uid, gid):
        self.assertEqual(Path(path), self.fstab, "Existing disk files must never be chowned")
        self.assertEqual(uid, 0)
        self.assertEqual(gid, self.fstab.stat().st_gid)
        self.chowns.append((str(path), uid, gid))

    def fake_run(self, args, allowed=(0,), **kwargs):
        self.commands.append(args)
        if args[0] in ("mkfs", "mkfs.ext4", "mount", "umount", "mv", "rsync", "chown") or args[:2] == ["systemctl", "stop"]:
            self.fail("Adoption must not format, unmount, move, or chown existing storage: " + repr(args))
        stdout, status = "", 0
        if args[0] == "findmnt":
            if "--mountpoint" in args:
                status = 0 if self.mounted else 1
                stdout = json.dumps({"filesystems": [self.observed["mount"]] if self.mounted else []})
            else:
                stdout = json.dumps({"filesystems": [{"target": str(self.mount)}, {"target": "/"}]})
        elif args[0] == "lsblk":
            stdout = json.dumps(self.observed["tree"])
        elif args[0] == "dumpe2fs":
            stdout = "Filesystem state: clean\nFilesystem features: has_journal needs_recovery\n"
        elif args[0] in ("udevadm", "swapon"):
            pass
        elif args[:2] == ["systemctl", "daemon-reload"]:
            pass
        elif args[:2] == ["systemctl", "start"]:
            self.assertFalse(self.mounted, "Existing adopted mount must not be restarted during setup/recovery")
            self.mounted = True
        elif args == [str(self.guard)]:
            pass
        else:
            self.fail("Unexpected external action: " + repr(args))
        if status not in allowed:
            raise storage.Refusal("Fixture command failure")
        return subprocess.CompletedProcess(args, status, stdout, "")

    def assert_preserved(self):
        self.assertEqual(self.sentinel.read_bytes(), b"Existing user files stay exactly where they are.\n")
        current = self.sentinel.stat()
        self.assertEqual((current.st_ino, current.st_uid, current.st_gid, stat.S_IMODE(current.st_mode)),
                         (self.sentinel_status.st_ino, self.sentinel_status.st_uid, self.sentinel_status.st_gid,
                          stat.S_IMODE(self.sentinel_status.st_mode)))
        self.assertTrue((self.mount / "lost+found").is_dir())
        self.assertTrue(self.mounted)

    def fstab_state(self):
        after = self.selected["fstab_after"].encode()
        return {"version": 2, "mode": "adopt", "stage": "adopt-approved", "uuid": FS_UUID,
                "token": "test-adoption-owner", "choice": self.selected, "changes": [],
                "fstab": {"before": base64.b64encode(self.before).decode(), "after": base64.b64encode(after).decode(),
                          "before_sha256": hashlib.sha256(self.before).hexdigest(),
                          "after_sha256": hashlib.sha256(after).hexdigest(),
                          "mode": stat.S_IMODE(self.fstab.stat().st_mode), "gid": self.fstab.stat().st_gid}}

    def interrupted(self, stage="adopt-creating-data", after_marker=False):
        real_stage, real_atomic = storage.stage, storage.atomic

        def interrupt_stage(state, value):
            real_stage(state, value)
            if not after_marker and value == stage:
                raise KeyboardInterrupt("deliberate stage interruption")

        def interrupt_atomic(path, *args, **kwargs):
            value = real_atomic(path, *args, **kwargs)
            if after_marker and path == self.data / ".owner":
                raise KeyboardInterrupt("deliberate marker interruption")
            return value

        with patch.object(adoption, "inspect", return_value=self.selected), \
                patch.object(storage, "stage", side_effect=interrupt_stage), \
                patch.object(storage, "atomic", side_effect=interrupt_atomic), self.assertRaises(KeyboardInterrupt):
            adoption.configure(storage, self.selected)
        return json.loads(self.journal.read_text())

    def test_inspection_allows_prepared_mount_with_unrelated_files(self):
        self.assertEqual(adoption.inspect(storage), self.selected)
        storage.preflight.assert_called_once_with(adopting=True)
        self.assert_preserved()
        self.assertFalse(self.journal.exists())

    def test_inspection_refuses_existing_runtime_store_markers(self):
        for name in ["containers", "volumes", "overlay2", "image", "engine-id", "io.containerd.content.v1.content"]:
            with self.subTest(name=name):
                marker = self.mount / name
                marker.mkdir()
                try:
                    with self.assertRaises(ValueError):
                        adoption.inspect(storage)
                    self.assertFalse(self.journal.exists())
                finally:
                    marker.rmdir()

    def test_inspection_refuses_own_namespace_collision(self):
        self.data.mkdir()
        (self.data / "someone-elses-file").write_bytes(b"unowned")
        with self.assertRaises(ValueError):
            adoption.inspect(storage)
        self.assertEqual((self.data / "someone-elses-file").read_bytes(), b"unowned")

    def test_inspection_propagates_fresh_runtime_refusal_before_writes(self):
        storage.preflight.side_effect = storage.Refusal("existing containerd data/config/runtime")
        with self.assertRaises(storage.Refusal):
            adoption.inspect(storage)
        self.assertEqual(self.fstab.read_bytes(), self.before)
        self.assertFalse(self.data.exists())
        self.assertFalse(self.journal.exists())

    def test_inspection_refuses_symlinked_mount_without_touching_target(self):
        moved = self.mount.with_name("existing-target")
        self.mount.rename(moved)
        self.mount.symlink_to(moved, target_is_directory=True)
        with self.assertRaises(storage.Refusal):
            adoption.inspect(storage)
        self.assertTrue(self.mount.is_symlink())
        self.assertEqual((moved / "operator-notes.txt").read_bytes(), b"Existing user files stay exactly where they are.\n")

    def test_success_creates_only_owned_runtime_namespace_and_preserves_mount(self):
        with patch.object(adoption, "inspect", return_value=self.selected):
            adoption.configure(storage, self.selected)
        state = json.loads(self.journal.read_text())
        self.assertEqual(state["version"], 2)
        self.assertEqual(state["mode"], "adopt")
        self.assertEqual(state["stage"], "complete")
        self.assertEqual(base64.b64decode(state["fstab"]["before"]), self.before)
        self.assertEqual(self.fstab.read_bytes(), self.selected["fstab_after"].encode())
        self.assertEqual(stat.S_IMODE(self.fstab.stat().st_mode), 0o640)
        docker = json.loads((self.root / "etc/docker/daemon.json").read_text())
        self.assertEqual(docker["data-root"], str(self.data / "docker"))
        self.assertIn(str(self.data / "containerd"), (self.root / "etc/containerd/config.toml").read_text())
        self.assert_preserved()

    def test_fstab_journal_precedes_replacement(self):
        real_replace = adoption.replace_fstab
        witnessed = []

        def observe(s, state, restore=False):
            self.assertFalse(restore)
            persisted = json.loads(self.journal.read_text())
            self.assertEqual(base64.b64decode(persisted["fstab"]["before"]), self.fstab.read_bytes())
            self.assertEqual(persisted["fstab"]["before_sha256"], hashlib.sha256(self.before).hexdigest())
            witnessed.append(True)
            return real_replace(s, state, restore)

        with patch.object(adoption, "inspect", return_value=self.selected), patch.object(adoption, "replace_fstab", side_effect=observe):
            adoption.configure(storage, self.selected)
        self.assertEqual(witnessed, [True])

    def test_exact_fstab_bytes_and_mode_round_trip(self):
        state = self.fstab_state()
        adoption.replace_fstab(storage, state)
        self.assertEqual(self.fstab.read_bytes(), self.selected["fstab_after"].encode())
        adoption.replace_fstab(storage, state, restore=True)
        self.assertEqual(self.fstab.read_bytes(), self.before)
        self.assertEqual(stat.S_IMODE(self.fstab.stat().st_mode), 0o640)
        adoption.replace_fstab(storage, state, restore=True)
        self.assertEqual(self.fstab.read_bytes(), self.before)

    def test_fstab_changed_after_plan_is_not_overwritten(self):
        state = self.fstab_state()
        changed = self.before + b"# later administrator entry\n"
        self.fstab.write_bytes(changed)
        with self.assertRaises(ValueError):
            adoption.replace_fstab(storage, state)
        self.assertEqual(self.fstab.read_bytes(), changed)
        self.assertEqual(self.chowns, [])

    def test_fstab_metadata_change_before_write_is_preserved(self):
        state = self.fstab_state()
        self.fstab.chmod(0o600)
        with self.assertRaises(ValueError):
            adoption.replace_fstab(storage, state)
        self.assertEqual(self.fstab.read_bytes(), self.before)
        self.assertEqual(stat.S_IMODE(self.fstab.stat().st_mode), 0o600)

    def test_rollback_restores_exact_original_and_keeps_mount_and_files(self):
        state = self.interrupted(after_marker=True)
        adoption.recover(storage, state)
        self.assertEqual(self.fstab.read_bytes(), self.before)
        self.assertEqual(stat.S_IMODE(self.fstab.stat().st_mode), 0o640)
        self.assertFalse(self.data.exists())
        self.assert_preserved()
        self.assertEqual(json.loads(self.journal.read_text())["stage"], "recovered")
        before_commands = copy.deepcopy(self.commands)
        adoption.recover(storage, state)
        self.assertEqual(self.commands, before_commands)

    def test_recovery_before_data_creation_never_unmounts_prepared_disk(self):
        state = self.interrupted(stage="adopt-fstab-written")
        adoption.recover(storage, state)
        self.assertEqual(self.fstab.read_bytes(), self.before)
        self.assertFalse(self.data.exists())
        self.assert_preserved()

    def test_recovery_after_reboot_remounts_without_moving_or_formatting_files(self):
        state = self.interrupted(after_marker=True)
        self.mounted = False
        adoption.recover(storage, state)
        self.assertIn(["systemctl", "start", "var-lib-docker.mount"], self.commands)
        self.assert_preserved()
        self.assertEqual(self.fstab.read_bytes(), self.before)

    def test_later_fstab_edits_block_rollback_and_are_preserved(self):
        state = self.interrupted(after_marker=True)
        changed = self.fstab.read_bytes() + b"# administrator adjustment\n"
        self.fstab.write_bytes(changed)
        with self.assertRaises(ValueError):
            adoption.recover(storage, state)
        self.assertEqual(self.fstab.read_bytes(), changed)
        self.assertTrue((self.data / ".owner").exists())
        self.assertTrue(adoption.MOUNT_DROPIN.exists())
        self.assert_preserved()

    def test_later_managed_config_edits_block_all_rollback_changes(self):
        state = self.interrupted(after_marker=True)
        adoption.MOUNT_DROPIN.write_text("administrator mount policy\n")
        current_fstab = self.fstab.read_bytes()
        with self.assertRaises(ValueError):
            adoption.recover(storage, state)
        self.assertEqual(adoption.MOUNT_DROPIN.read_text(), "administrator mount policy\n")
        self.assertEqual(self.fstab.read_bytes(), current_fstab)
        self.assertTrue((self.data / ".owner").exists())
        self.assert_preserved()

    def test_nonempty_runtime_data_survives_failed_rollback(self):
        state = self.interrupted(after_marker=True)
        (self.data / "docker").mkdir()
        content = self.data / "docker" / "live-data"
        content.write_bytes(b"do not remove")
        with self.assertRaises((ValueError, OSError)):
            adoption.recover(storage, state)
        self.assertEqual(content.read_bytes(), b"do not remove")
        self.assert_preserved()

    def test_completed_install_cannot_be_automatically_rolled_back(self):
        with patch.object(adoption, "inspect", return_value=self.selected):
            adoption.configure(storage, self.selected)
        state = json.loads(self.journal.read_text())
        after = self.fstab.read_bytes()
        with self.assertRaises(ValueError):
            adoption.recover(storage, state)
        self.assertEqual(self.fstab.read_bytes(), after)
        self.assertTrue(self.data.is_dir())
        self.assert_preserved()

    @contextlib.contextmanager
    def real_preflight_environment(self, installed=None):
        required = {"lsblk", "blkid", "wipefs", "udevadm", "swapon", "mkfs.ext4", "dumpe2fs", "systemctl", "findmnt"}
        (self.root / "run/systemd/system").mkdir(parents=True, exist_ok=True)
        with patch.object(storage, "Path", self.virtual_path), \
                patch.object(storage, "MOUNT", self.root / "mnt/nosana"), \
                patch.object(storage, "UNIT", self.root / "etc/systemd/system/mnt-nosana.mount"), \
                patch.object(storage.shutil, "which", side_effect=lambda name: "/fixture/bin/" + name if name in required or name == installed else None):
            yield

    def test_real_fresh_preflight_permits_prepared_mount_files(self):
        with self.real_preflight_environment():
            self.real_preflight(adopting=True)
        self.assert_preserved()

    def test_real_preflight_still_rejects_installed_container_runtimes(self):
        for executable in ["docker", "dockerd", "containerd", "runc", "nerdctl"]:
            with self.subTest(executable=executable), self.real_preflight_environment(installed=executable):
                with self.assertRaisesRegex(storage.Refusal, "Existing container runtime"):
                    self.real_preflight(adopting=True)
        self.assertEqual(self.fstab.read_bytes(), self.before)

    def test_real_preflight_rejects_existing_containerd_data(self):
        root = self.root / "var/lib/containerd"
        root.mkdir()
        content = root / "metadata.db"
        content.write_bytes(b"preexisting runtime state")
        with self.real_preflight_environment(), self.assertRaisesRegex(storage.Refusal, "Existing runtime data"):
            self.real_preflight(adopting=True)
        self.assertEqual(content.read_bytes(), b"preexisting runtime state")

    def test_real_preflight_rejects_existing_runtime_configs(self):
        for relative in ["etc/docker/daemon.json", "etc/containerd/config.toml"]:
            with self.subTest(relative=relative):
                config = self.root / relative
                config.parent.mkdir(parents=True, exist_ok=True)
                config.write_bytes(b"existing administrator configuration")
                try:
                    with self.real_preflight_environment(), self.assertRaisesRegex(storage.Refusal, "Existing configuration"):
                        self.real_preflight(adopting=True)
                    self.assertEqual(config.read_bytes(), b"existing administrator configuration")
                finally:
                    config.unlink()

    def test_recovery_can_retry_after_exact_fstab_restore_before_config_removal(self):
        state = self.interrupted(after_marker=True)
        real_unlink = Path.unlink

        def interrupt(path, *args, **kwargs):
            if path == adoption.MOUNT_DROPIN:
                raise KeyboardInterrupt("deliberate cleanup interruption")
            return real_unlink(path, *args, **kwargs)

        with patch.object(Path, "unlink", interrupt), self.assertRaises(KeyboardInterrupt):
            adoption.recover(storage, state)
        self.assertEqual(self.fstab.read_bytes(), self.before)
        self.assertEqual(json.loads(self.journal.read_text())["stage"], "recovering")
        adoption.recover(storage, json.loads(self.journal.read_text()))
        self.assertEqual(self.fstab.read_bytes(), self.before)
        self.assertEqual(json.loads(self.journal.read_text())["stage"], "recovered")
        self.assert_preserved()

    def test_changed_inspection_result_prevents_transaction_start(self):
        changed = copy.deepcopy(self.selected)
        changed["uuid"] = "different filesystem"
        with patch.object(adoption, "inspect", return_value=changed), self.assertRaises(ValueError):
            adoption.configure(storage, self.selected)
        self.assertFalse(self.journal.exists())
        self.assertEqual(self.fstab.read_bytes(), self.before)
        self.assertFalse(self.data.exists())

    def test_adoption_prompt_rejects_cancel_erasure_phrase_and_wrong_identity(self):
        for answer in ["", "yes", "ERASE ADOPTION-DATA", "USE ADOPTION-OS"]:
            with self.subTest(answer=answer), patch.object(adoption.sys.stdin, "isatty", return_value=True), \
                    patch("builtins.input", return_value=answer), patch.object(adoption, "configure") as configure:
                with self.assertRaises(ValueError):
                    adoption.prompt(storage, self.selected)
                configure.assert_not_called()
                self.assertEqual(self.fstab.read_bytes(), self.before)

    def test_vendor_mount_units_are_not_silently_overridden(self):
        for base in ["usr/lib/systemd/system", "lib/systemd/system"]:
            with self.subTest(base=base):
                unit = self.root / base / "var-lib-docker.mount"
                unit.parent.mkdir(parents=True, exist_ok=True)
                unit.write_bytes(b"existing vendor mount contract")
                try:
                    with self.assertRaises(ValueError):
                        adoption.inspect(storage)
                    self.assertEqual(unit.read_bytes(), b"existing vendor mount contract")
                    self.assertFalse(self.journal.exists())
                finally:
                    unit.unlink()

    def test_recovery_preserves_later_fstab_permissions_before_cleaning_data(self):
        state = self.interrupted(after_marker=True)
        self.fstab.chmod(0o600)
        with self.assertRaises(ValueError):
            adoption.recover(storage, state)
        self.assertEqual(stat.S_IMODE(self.fstab.stat().st_mode), 0o600)
        self.assertTrue((self.data / ".owner").exists())
        self.assertTrue(adoption.MOUNT_DROPIN.exists())

    def test_fstab_noop_restore_still_protects_metadata_changes(self):
        state = self.fstab_state()
        self.fstab.chmod(0o600)
        with self.assertRaises(ValueError):
            adoption.replace_fstab(storage, state, restore=True)
        self.assertEqual(self.fstab.read_bytes(), self.before)
        self.assertEqual(stat.S_IMODE(self.fstab.stat().st_mode), 0o600)

    def test_missing_owned_runtime_directory_is_not_verified(self):
        with patch.object(adoption, "inspect", return_value=self.selected):
            adoption.configure(storage, self.selected)
        state = json.loads(self.journal.read_text())
        (self.data / "containerd").rmdir()
        with self.assertRaises(ValueError):
            adoption.check(storage, state)
        self.assert_preserved()

    def test_generated_guard_refuses_bind_subdirectory_even_with_optimization(self):
        source = adoption.guard_source(storage, FS_UUID, "test-token")
        for optimize in [0, 1, 2]:
            with self.subTest(optimize=optimize):
                mount = dict(self.observed["mount"], fsroot="/subdirectory")
                response = subprocess.CompletedProcess([], 0, json.dumps({"filesystems": [mount]}), "")
                with patch("subprocess.run", return_value=response), patch("pathlib.Path") as touched_path:
                    with self.assertRaises(SystemExit) as failure:
                        exec(compile(source, "<adopted-storage-guard>", "exec", optimize=optimize), {})
                    self.assertEqual(failure.exception.code, 1)
                    touched_path.assert_not_called()


if __name__ == "__main__":
    unittest.main()
