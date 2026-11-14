"""Raw configuration references must exclude the disk before any write is offered.

Expected labels are specified independently of the implementation. fstab fields
are decoded once, then one matching tag-value quote pair is removed (libmount).
systemd's crypttab reader splits fields and preserves backslash-octal text.

Sources: util-linux libmount/src/tab_parse.c and lib/mangle.c; systemd v249
src/cryptsetup/cryptsetup-generator.c and src/shared/fstab-util.c. No real block
device, configuration file, mount, or subprocess is accessed by these tests.
"""

import json
from pathlib import Path, PurePosixPath
import subprocess
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src import storage  # noqa: E402


def block(path, kind, serial, **extra):
    value = {
        "name": path, "path": path, "type": kind, "serial": serial,
        "model": "Reference parser fixture", "wwn": None,
        "size": 1_000_000_000_000, "ro": False, "rm": False, "tran": "sata",
        "fstype": None, "uuid": None, "label": None,
        "partuuid": None, "partlabel": None, "mountpoints": [None],
        "maj:min": {"/dev/sda": "8:0", "/dev/sdb": "8:16", "/dev/sdb1": "8:17"}[path],
    }
    value.update(extra)
    return value


def inventory_tree(label):
    system = block("/dev/sda", "disk", "OS-REF-001", fstype="ext4",
                   uuid="11111111-1111-4111-8111-111111111111", mountpoints=["/"])
    data = block("/dev/sdb1", "part", None, fstype="ext4", label=label,
                 uuid="22222222-2222-4222-8222-222222222222")
    spare = block("/dev/sdb", "disk", "DATA-REF-002", children=[data])
    return {"blockdevices": [system, spare]}


class InventoryReferenceTests(unittest.TestCase):
    def observed(self, label, *, fstab="", crypttab=None):
        configs = {"/etc/fstab": fstab}
        if crypttab is not None:
            configs["/etc/crypttab"] = crypttab
        tree = inventory_tree(label)
        case = self

        class FixturePath(PurePosixPath):
            def exists(self):
                case.assertIn(str(self), ("/etc/fstab", "/etc/crypttab"))
                return str(self) in configs

            def read_text(self):
                case.assertIn(str(self), configs)
                return configs[str(self)]

            def is_dir(self):
                case.assertTrue(str(self).startswith("/sys/class/block/"))
                case.assertEqual(self.name, "holders")
                return True

            def iterdir(self):
                self.is_dir()
                return iter(())

        def process(args, **kwargs):
            case.assertEqual(kwargs.get("stdin"), subprocess.DEVNULL)
            if args[0] == "lsblk":
                return subprocess.CompletedProcess(args, 0, json.dumps(tree), "")
            if args[0] in ("udevadm", "swapon"):
                return subprocess.CompletedProcess(args, 0, "", "")
            case.fail("Unexpected subprocess while reading inventory: " + repr(args))

        with patch.object(storage, "Path", FixturePath), \
                patch.object(storage.subprocess, "run", side_effect=process):
            return storage.inventory()

    def assert_referenced(self, label, **configs):
        eligible, rejected = self.observed(label, **configs)
        self.assertEqual(eligible, [])
        self.assertIn(("/dev/sdb", "Referenced by fstab or crypttab"), rejected)

    def assert_available(self, label, **configs):
        eligible, _ = self.observed(label, **configs)
        self.assertEqual([item["disk"]["path"] for item in eligible], ["/dev/sdb"])
        self.assertEqual(eligible[0]["mode"], "reuse")

    def test_fstab_escaped_space_excludes_unmounted_configured_disk(self):
        self.assert_referenced("Data Drive", fstab=r"LABEL=Data\040Drive /srv/archive ext4 noauto 0 2" + "\n")

    def test_fstab_quoted_escaped_space(self):
        for source in (r'LABEL="Data\040Drive"', r"LABEL='Data\040Drive'"):
            with self.subTest(source=source):
                self.assert_referenced("Data Drive", fstab=source + " /srv/archive ext4 noauto 0 2\n")

    def test_fstab_escaped_tab(self):
        self.assert_referenced("Data\tDrive", fstab=r"LABEL=Data\011Drive /srv/archive ext4 noauto 0 2" + "\n")

    def test_fstab_literal_backslash_is_not_recursively_decoded(self):
        record = r"LABEL=Data\134040Drive /srv/archive ext4 noauto 0 2" + "\n"
        self.assert_referenced(r"Data\040Drive", fstab=record)
        self.assert_available("Data Drive", fstab=record)

    def test_fstab_ordinary_non_ascii_text_is_preserved(self):
        self.assert_referenced("Données", fstab="LABEL=Données /srv/archive ext4 noauto 0 2\n")

    def test_fstab_octal_encoded_utf8_matches_the_decoded_label(self):
        self.assert_referenced("Données", fstab=r"LABEL=Donn\303\251es /srv/archive ext4 noauto 0 2" + "\n")

    def test_fstab_hash_inside_label_is_not_a_comment(self):
        self.assert_referenced("Data#Drive", fstab="LABEL=Data#Drive /srv/archive ext4 noauto 0 2\n")

    def test_fstab_encoded_outer_quotes_are_syntax_after_decoding(self):
        self.assert_referenced("Data", fstab=r"LABEL=\042Data\042 /srv/archive ext4 noauto 0 2" + "\n")

    def test_fstab_removes_only_one_quote_pair_preserving_inner_quotes(self):
        self.assert_referenced('"Data"', fstab=r'LABEL="\042Data\042" /srv/archive ext4 noauto 0 2' + "\n")

    def test_unrelated_entries_and_leading_comments_do_not_exclude_disk(self):
        self.assert_available("Data Drive", fstab=(
            "  # LABEL=Data\\040Drive /srv/archive ext4 noauto 0 2\n"
            "\nLABEL=Other\\040Drive /srv/other ext4 noauto 0 2\n"))

    def test_crypttab_uses_device_column_and_preserves_hash_label(self):
        self.assert_referenced("Data#Drive", crypttab="archive LABEL=Data#Drive none noauto\n")

    def test_crypttab_device_quotes_are_removed_once(self):
        self.assert_referenced("Data", crypttab='archive LABEL="Data" none noauto\n')

    def test_crypttab_backslash_octal_is_literal_not_fstab_escaping(self):
        record = r"archive LABEL=Data\040Drive none noauto" + "\n"
        self.assert_referenced(r"Data\040Drive", crypttab=record)
        self.assert_available("Data Drive", crypttab=record)

    def test_unrelated_crypttab_entry_does_not_exclude_disk(self):
        self.assert_available("Data", crypttab="archive LABEL=Other none noauto\n")


if __name__ == "__main__":
    unittest.main()
