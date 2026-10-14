"""Real artifact checks for the local producer's no-process marker reader."""

import hashlib
import os
from pathlib import Path
import sys
import tempfile
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
