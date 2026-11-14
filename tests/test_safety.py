"""Independent safety requirements for the disk-selection policy.

These fixtures describe host layouts, not mocked implementation calls.  A disk
being returned is authorization to proceed to further privileged checks, so
uncertain and already-used layouts must never be returned as candidates.
Run with: python3 -m unittest discover -s tests -p 'test_safety.py' -v
"""

import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.storage import candidates  # noqa: E402


def block(path, kind="disk", **overrides):
    """Produce an explicit, otherwise unused 2 TB block-device observation."""
    item = {
        "name": path,
        "path": path,
        "type": kind,
        "size": 2_000_000_000_000,
        "model": "Fixture SSD",
        "serial": "DATA-SERIAL-002" if kind == "disk" else None,
        "wwn": None,
        "ro": False,
        "rm": False,
        "tran": "sata",
        "fstype": None,
        "uuid": None,
        "label": None,
        "mountpoints": [None],
        "maj:min": "8:16" if kind == "disk" else "8:17",
    }
    item.update(overrides)
    return item


def boot_disk():
    return block(
        "/dev/sda",
        serial="OS-SERIAL-001",
        **{
            "maj:min": "8:0",
            "children": [
                block(
                    "/dev/sda1", "part", fstype="vfat", uuid="EFI-0001",
                    label="EFI", mountpoints=["/boot/efi"], **{"maj:min": "8:1"},
                ),
                block(
                    "/dev/sda2", "part", fstype="ext4", uuid="root-uuid-001",
                    label="ubuntu-root", mountpoints=["/"], **{"maj:min": "8:2"},
                ),
            ],
        },
    )


def host(*disks):
    return {"blockdevices": [boot_disk(), *disks]}


def existing_partition(**overrides):
    properties = {"fstype": "ext4", "uuid": "data-uuid-002", "label": "existing-files"}
    properties.update(overrides)
    partition = block("/dev/sdb1", "part", **properties)
    return block("/dev/sdb", children=[partition])


class CandidateSafetyTests(unittest.TestCase):
    def assert_refused(self, tree, target="/dev/sdb", **kwargs):
        selected, rejected = candidates(tree, **kwargs)
        self.assertNotIn(target, [item["disk"]["path"] for item in selected])
        self.assertTrue(
            any(path == target and isinstance(reason, str) and reason.strip()
                for path, reason in rejected),
            "The operator needs a reason for each refused physical disk",
        )
        return selected, rejected

    def assert_choice(self, disk, mode, device_path=None):
        selected, _ = candidates(host(disk))
        self.assertEqual(len(selected), 1)
        choice = selected[0]
        self.assertEqual(choice["disk"], disk)
        self.assertEqual(choice["device"]["path"], device_path or disk["path"])
        self.assertEqual(choice["mode"], mode)
        self.assertIsInstance(choice["identity"], str)
        self.assertTrue(choice["identity"].strip())
        return choice

    def test_only_os_disk_has_no_candidate(self):
        selected, rejected = candidates(host())
        self.assertEqual(selected, [])
        self.assertTrue(any(path == "/dev/sda" and reason for path, reason in rejected))

    def test_blank_sata_disk_is_format_candidate(self):
        self.assert_choice(block("/dev/sdb"), "format")

    def test_blank_nvme_and_virtio_disks_are_supported(self):
        for path, transport in [("/dev/nvme1n1", "nvme"), ("/dev/vdb", None)]:
            with self.subTest(path=path):
                self.assert_choice(block(path, tran=transport), "format")

    def test_whole_disk_ext4_is_reused_without_formatting(self):
        self.assert_choice(block("/dev/sdb", fstype="ext4", uuid="data-uuid-002"), "reuse")

    def test_single_ext4_partition_is_reused_without_formatting(self):
        self.assert_choice(existing_partition(), "reuse", "/dev/sdb1")

    def test_nvme_ext4_partition_is_selected_by_observed_path(self):
        disk = block("/dev/nvme1n1", tran="nvme", children=[
            block("/dev/nvme1n1p1", "part", fstype="ext4", uuid="nvme-data-uuid"),
        ])
        self.assert_choice(disk, "reuse", "/dev/nvme1n1p1")

    def test_os_disk_cannot_offer_its_unused_third_partition(self):
        root = boot_disk()
        root["children"].append(block("/dev/sda3", "part", fstype="ext4", uuid="spare"))
        self.assert_refused({"blockdevices": [root]}, target="/dev/sda")

    def test_any_mounted_descendant_excludes_entire_disk(self):
        for mount in ["/home", "/var", "/boot", "/boot/efi", "/mnt/archive", "/media/user/drive"]:
            with self.subTest(mount=mount):
                self.assert_refused(host(existing_partition(mountpoints=[mount])))

    def test_second_mountpoint_is_not_ignored(self):
        self.assert_refused(host(existing_partition(mountpoints=[None, "/srv/data"])))

    def test_mounted_whole_disk_is_rejected(self):
        self.assert_refused(host(block("/dev/sdb", fstype="ext4", uuid="data", mountpoints=["/mnt/data"])))

    def test_active_swap_blocks_whole_disk_and_partition(self):
        self.assert_refused(host(block("/dev/sdb")), swap_paths={"/dev/sdb"})
        self.assert_refused(host(existing_partition()), swap_paths={"/dev/sdb1"})

    def test_swap_signature_is_never_blank(self):
        self.assert_refused(host(block("/dev/sdb", fstype="swap", uuid="swap-uuid")))

    def test_holders_block_whole_disk_and_partition(self):
        self.assert_refused(host(block("/dev/sdb")), held={"/dev/sdb"})
        self.assert_refused(host(existing_partition()), held={"/dev/sdb1"})

    def test_configuration_references_block_disk_or_descendant(self):
        cases = [
            (block("/dev/sdb"), "/dev/sdb"),
            (existing_partition(), "/dev/sdb1"),
            (existing_partition(), "UUID=data-uuid-002"),
            (existing_partition(), "LABEL=existing-files"),
        ]
        for disk, reference in cases:
            with self.subTest(reference=reference):
                self.assert_refused(host(disk), referenced={reference})

    def test_read_only_and_removable_flags_block_disk(self):
        for field in ["ro", "rm"]:
            for true_value in [True, 1]:
                with self.subTest(field=field, value=true_value):
                    self.assert_refused(host(block("/dev/sdb", **{field: true_value})))

    def test_read_only_partition_cannot_be_reused(self):
        self.assert_refused(host(existing_partition(ro=True)))

    def test_removable_usb_with_missing_rm_flag_is_not_internal_storage(self):
        self.assert_refused(host(block("/dev/sdb", tran="usb", rm=False)))

    def test_unsupported_physical_path_is_not_eligible(self):
        for path in ["/dev/loop0", "/dev/nbd0", "/dev/mmcblk0", "/dev/dm-0", "/dev/md0", "/tmp/disk.img"]:
            with self.subTest(path=path):
                selected, _ = candidates(host(block(path)))
                self.assertEqual(selected, [])

    def test_lvm_raid_crypto_and_other_signatures_are_not_blank(self):
        for signature in ["LVM2_member", "linux_raid_member", "crypto_LUKS", "zfs_member", "btrfs", "ntfs", "xfs", "vfat"]:
            with self.subTest(signature=signature):
                self.assert_refused(host(block("/dev/sdb", fstype=signature, uuid="existing")))

    def test_only_single_plain_ext4_partition_can_be_reused(self):
        for kind in ["lvm", "crypt", "raid1", "mpath", "loop", "unknown"]:
            with self.subTest(kind=kind):
                disk = block("/dev/sdb", children=[block("/dev/sdb1", kind, fstype="ext4", uuid="existing")])
                self.assert_refused(host(disk))

    def test_empty_partition_is_not_a_blank_whole_disk(self):
        self.assert_refused(host(block("/dev/sdb", children=[block("/dev/sdb1", "part")])))

    def test_two_partitions_are_not_silently_collapsed_into_one(self):
        disk = existing_partition()
        disk["children"].append(block("/dev/sdb2", "part", fstype="ext4", uuid="other"))
        self.assert_refused(host(disk))

    def test_partition_with_nested_holder_is_rejected(self):
        disk = existing_partition(children=[block("/dev/dm-7", "crypt", fstype="ext4", uuid="nested")])
        self.assert_refused(host(disk))

    def test_uuid_required_before_reusing_filesystem(self):
        for value in [None, "", " "]:
            with self.subTest(uuid=value):
                self.assert_refused(host(existing_partition(uuid=value)))

    def test_duplicate_filesystem_uuid_blocks_reuse(self):
        other = block("/dev/sdc", serial="OTHER-SERIAL-003", fstype="ext4", uuid="data-uuid-002")
        self.assert_refused(host(existing_partition(), other))

    def test_uuid_cloned_from_root_is_rejected(self):
        self.assert_refused(host(existing_partition(uuid="root-uuid-001")))

    def test_serial_or_wwn_is_required(self):
        for value in [None, "", " "]:
            with self.subTest(serial=value):
                self.assert_refused(host(block("/dev/sdb", serial=value, wwn=None)))

    def test_unique_wwn_can_identify_disk_without_serial(self):
        self.assert_choice(block("/dev/sdb", serial=None, wwn="0x5000000000000002"), "format")

    def test_duplicate_serial_without_wwn_is_rejected(self):
        other = block("/dev/sdc", serial="DATA-SERIAL-002")
        self.assert_refused(host(block("/dev/sdb"), other))

    def test_duplicate_identity_with_os_disk_is_rejected(self):
        self.assert_refused(host(block("/dev/sdb", serial="OS-SERIAL-001")))

    def test_duplicate_wwn_is_rejected_even_when_serial_differs(self):
        disk = block("/dev/sdb", wwn="0x5000000000000002")
        other = block("/dev/sdc", serial="OTHER-SERIAL-003", wwn="0x5000000000000002")
        self.assert_refused(host(disk, other))

    def test_missing_root_resolution_disables_all_candidates(self):
        selected, _ = candidates({"blockdevices": [block("/dev/sdb")]})
        self.assertEqual(selected, [])

    def test_root_visible_only_as_unparented_logical_device_is_unresolved(self):
        logical = block("/dev/mapper/ubuntu-root", "lvm", fstype="ext4", uuid="root", mountpoints=["/"])
        selected, _ = candidates({"blockdevices": [logical, block("/dev/sdb")]})
        self.assertEqual(selected, [])

    def test_fully_resolved_lvm_root_does_not_hide_its_physical_disk(self):
        root = block("/dev/sda", serial="OS-SERIAL-001", children=[
            block("/dev/sda2", "part", fstype="LVM2_member", children=[
                block("/dev/mapper/ubuntu-root", "lvm", fstype="ext4", uuid="root", mountpoints=["/"]),
            ]),
        ])
        selected, _ = candidates({"blockdevices": [root, block("/dev/sdb")]})
        self.assertEqual([item["disk"]["path"] for item in selected], ["/dev/sdb"])

    def test_candidate_inventory_is_not_mutated(self):
        inventory = host(existing_partition())
        before = copy.deepcopy(inventory)
        candidates(inventory, referenced={"UUID=unrelated"}, swap_paths=set(), held=set())
        self.assertEqual(inventory, before)

    def test_unrelated_references_do_not_hide_valid_disk(self):
        selected, _ = candidates(host(block("/dev/sdb")), referenced={"UUID=other", "/dev/sdz1"}, swap_paths={"/dev/sdz2"}, held={"/dev/sdz3"})
        self.assertEqual([item["disk"]["path"] for item in selected], ["/dev/sdb"])

    def test_missing_safety_observations_are_not_treated_as_false(self):
        for field in ["type", "path", "size", "ro", "rm", "fstype", "uuid", "mountpoints", "maj:min"]:
            with self.subTest(field=field):
                disk = block("/dev/sdb")
                del disk[field]
                selected, _ = candidates(host(disk))
                self.assertEqual(selected, [])

    def test_unknown_safety_flags_are_not_treated_as_false(self):
        for field in ["ro", "rm", "mountpoints"]:
            with self.subTest(field=field):
                selected, _ = candidates(host(block("/dev/sdb", **{field: None})))
                self.assertEqual(selected, [])

    def test_partition_requires_its_own_safety_observations(self):
        for field in ["ro", "rm", "mountpoints", "maj:min"]:
            with self.subTest(field=field):
                disk = existing_partition()
                del disk["children"][0][field]
                selected, _ = candidates(host(disk))
                self.assertEqual(selected, [])

    def test_invalid_capacity_is_rejected(self):
        for value in [0, -1, None, "unknown"]:
            with self.subTest(size=value):
                selected, _ = candidates(host(block("/dev/sdb", size=value)))
                self.assertEqual(selected, [])


if __name__ == "__main__":
    unittest.main()
