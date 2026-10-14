"""Real artifact checks for the local producer's no-process marker reader."""

import hashlib
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from ext4_fixture import contract_rootfs_bytes
from test_linux_layer import make_ext4

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import ext4_contract
import local_ext4_contract


class LocalExt4ContractTests(unittest.TestCase):
    def test_reads_actual_artifact_without_a_process_or_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rootfs.ext4"
            path.write_bytes(contract_rootfs_bytes())
            before = hashlib.sha256(path.read_bytes()).hexdigest()
            with patch.object(
                ext4_contract.subprocess, "run",
                side_effect=AssertionError("local marker inspection launched a process"),
            ):
                self.assertEqual(local_ext4_contract.read_base_image_contract(path), 1)
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), before)

    def inspect_with_stat_snapshots(self, descriptor_change=None, path_change=None):
        """Model query-family metadata differences, not native Windows syscalls."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rootfs.ext4"
            data = contract_rootfs_bytes()
            path.write_bytes(data)
            actual = path.lstat()
            common = dict(
                st_mode=actual.st_mode, st_dev=actual.st_dev,
                st_ino=actual.st_ino, st_size=actual.st_size,
            )
            # Stable, deliberately unequal precision in the two query families.
            descriptor = dict(common, st_mtime_ns=123456789, st_ctime_ns=987654321)
            pathname = dict(common, st_mtime_ns=123456700, st_ctime_ns=987654300)
            after = dict(descriptor)
            current = dict(pathname)
            if descriptor_change:
                after[descriptor_change] += 1
            if path_change:
                current[path_change] += 1
            with patch.object(
                local_ext4_contract.os, "fstat",
                side_effect=[SimpleNamespace(**descriptor), SimpleNamespace(**after)],
            ) as fstat, patch.object(
                Path, "lstat",
                side_effect=[SimpleNamespace(**pathname), SimpleNamespace(**current)],
            ) as lstat:
                try:
                    return local_ext4_contract.read_base_image_contract(path)
                finally:
                    self.assertEqual(fstat.call_count, 2)
                    self.assertEqual(lstat.call_count, 2)
                    self.assertEqual(path.read_bytes(), data)

    def test_stable_heterogeneous_stat_timestamp_precision_is_accepted(self):
        self.assertEqual(self.inspect_with_stat_snapshots(), 1)

    def test_changes_within_either_stat_family_are_refused(self):
        for family in ("descriptor_change", "path_change"):
            for field in ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns"):
                with self.subTest(family=family, field=field):
                    with self.assertRaisesRegex(
                        ValueError, "rootfs changed during marker inspection"
                    ):
                        self.inspect_with_stat_snapshots(**{family: field})

    def test_path_replacement_during_inspection_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rootfs.ext4"
            replacement = Path(tmp) / "replacement.ext4"
            data = contract_rootfs_bytes()
            path.write_bytes(data)
            replacement.write_bytes(data)
            original_lstat = Path.lstat
            calls = 0

            def replace_before_final_lstat(target):
                nonlocal calls
                if target == path:
                    calls += 1
                    if calls == 2:
                        os.replace(replacement, path)
                return original_lstat(target)

            with patch.object(Path, "lstat", replace_before_final_lstat):
                with self.assertRaisesRegex(
                    ValueError, "rootfs changed during marker inspection"
                ):
                    local_ext4_contract.read_base_image_contract(path)
            self.assertEqual(calls, 2)
            self.assertEqual(path.read_bytes(), data)

    def test_invalid_filesystem_or_changed_marker_never_uses_source_fallback(self):
        data = contract_rootfs_bytes()
        # The fixed artifact contains a unique file payload 1\\n. Alter only
        # payload bytes; filesystem metadata checksums remain valid.
        self.assertEqual(data.count(b"1\n"), 1)
        for malformed in (b"not ext4", data.replace(b"1\n", b"2\n")):
            with self.subTest(length=len(malformed)), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "rootfs.ext4"
                path.write_bytes(malformed)
                with self.assertRaises(ValueError):
                    local_ext4_contract.read_base_image_contract(path)

    def test_wrong_dependency_version_is_an_actionable_refusal(self):
        with patch.object(local_ext4_contract.metadata, "version", return_value="0"):
            with self.assertRaisesRegex(ValueError, "pinned ext4==1.4.1"):
                local_ext4_contract.read_base_image_contract(Path("unused"))

    def test_directory_artifact_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "regular file"):
                local_ext4_contract.read_base_image_contract(Path(tmp))

    def test_both_readers_refuse_real_symlink_ancestors_and_special_mode(self):
        for ancestor in ("etc", "etc/mvm", None):
            with self.subTest(ancestor=ancestor), tempfile.TemporaryDirectory() as tmp:
                directory = Path(tmp)
                if ancestor:
                    root = directory / "root"
                    real = root / "actual-directory"
                    real.mkdir(parents=True)
                    link = root / ancestor
                    link.parent.mkdir(parents=True, exist_ok=True)
                    link.symlink_to(os.path.relpath(real, link.parent), target_is_directory=True)
                image = directory / "rootfs.ext4"
                make_ext4(image, directory, mode=0o4444 if ancestor is None else 0o444)
                for reader in (
                    ext4_contract.read_base_image_contract,
                    local_ext4_contract.read_base_image_contract,
                ):
                    with self.subTest(reader=reader.__module__):
                        with self.assertRaises(ValueError):
                            reader(image)


if __name__ == "__main__":
    unittest.main()
