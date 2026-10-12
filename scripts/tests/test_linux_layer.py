import importlib.util
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("linux_layer", ROOT / "scripts/check_linux_layer.py")
layer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(layer)


class LinuxLayerTests(unittest.TestCase):
    def test_only_linux_inputs_and_pins_preserved(self):
        lock = json.loads((ROOT / "flake.lock").read_text())
        self.assertEqual(set(lock["nodes"]["root"]["inputs"]), {"nixpkgs", "microvm"})
        self.assertEqual(lock["nodes"]["nixpkgs"]["locked"]["rev"],
                         "8fd9daa3db09ced9700431c5b7ad0e8ba199b575")
        self.assertEqual(lock["nodes"]["microvm"]["locked"]["rev"],
                         "a7a7009064cec75d9da652c6723412ce27b9bc44")
        for path in (ROOT / "images").rglob("*.nix"):
            text = path.read_text()
            self.assertNotIn("mvm-src", text)
            self.assertNotIn("mvm-workspace", text)
            self.assertNotIn(".mkGuest", text)
        self.assertEqual((ROOT / "images/base-image-contract.nix").read_text(), "1\n")
        self.assertFalse((ROOT / "images/runtime-overlay/image.nix").exists())
        self.assertFalse((ROOT / "images/initramfs/image.nix").exists())
        composer = (ROOT / "images/mk-rootfs.nix").read_text()
        for token in ("nix-store", "closure-seed", "mvm/runtime", "mvm/sdk",
                      "root/nix-path-registration", "ca-bundle.crt",
                      "nameserver 127.0.0.1"):
            self.assertIn(token, composer)
        qemu = (ROOT / "qemu-wasm/qemu-wasm.nix").read_text()
        self.assertNotIn("mvm-src", qemu)
        self.assertIn("https://static.crates.io/crates/${crate}/${crate}-${version}.crate", qemu)

    def test_architecture_and_format_are_not_interchangeable(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "kernel"
            data = bytearray(64)
            data[:6] = b"\x7fELF\x02\x01"
            struct.pack_into("<H", data, 18, 62)
            path.write_bytes(data)
            layer.check_kernel(path, "x86_64", "elf")
            with self.assertRaises(ValueError):
                layer.check_kernel(path, "aarch64", "elf")
            with self.assertRaises(ValueError):
                layer.check_kernel(path, "x86_64", "bzimage")
            data[56:60] = b"ARM\x64"
            path.write_bytes(data)
            layer.check_kernel(path, "aarch64", "image")
            with self.assertRaises(ValueError):
                layer.check_kernel(path, "x86_64", "image")
            data = bytearray(0x210)
            data[0x1FE:0x200] = b"\x55\xaa"
            data[0x202:0x206] = b"HdrS"
            path.write_bytes(data)
            layer.check_kernel(path, "x86_64", "bzimage")
            with self.assertRaises(ValueError):
                layer.check_kernel(path, "aarch64", "bzimage")

    def test_locally_owned_budget_is_enforced(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config"
            path.write_text("".join(f"CONFIG_S{i}=y\n" for i in range(917)))
            layer.check_budget(path, "x86_64")
            path.write_text(path.read_text() + "CONFIG_EXCESS=y\n")
            with self.assertRaises(ValueError):
                layer.check_budget(path, "x86_64")
            layer.check_budget(path, "aarch64")

    def test_default_budget_does_not_apply_to_other_kernel_profiles(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config"
            path.write_text("".join(f"CONFIG_S{i}=y\n" for i in range(1100)))
            for arch in ("x86_64", "aarch64"):
                for profile in ("builder", "rootless-tenant", "default-tenant"):
                    with self.subTest(arch=arch, profile=profile), patch(
                        "sys.argv",
                        ["check_linux_layer.py", "--arch", arch,
                         "--kernel-config", str(path), "--profile", profile],
                    ), patch("builtins.print"):
                        self.assertEqual(layer.main(), 1 if profile == "default-tenant" else 0)

    def test_builder_profile_still_checks_the_builder_root_contract(self):
        with patch("sys.argv", ["check_linux_layer.py", "--arch", "x86_64",
                                "--rootfs", "fixture", "--profile", "builder"]), \
             patch.object(layer, "check_rootfs") as check:
            self.assertEqual(layer.main(), 0)
            check.assert_called_once_with(Path("fixture"), True)

    def rootfs_fixture(self):
        accounts = {"root": 0, "mvm-worker": 901, "mvm-dns": 987,
                    "mvm-crng": 988, "mvm-egress": 989, "mvm-builder": 902}
        values = {
            "stat /etc": "Type: directory",
            "stat /etc/mvm": "Type: directory",
            "stat /etc/mvm/base-image-contract": "Inode: 7 Type: regular Mode: 0444",
            "cat /etc/mvm/base-image-contract": "1\n",
            "cat /etc/mvm/builder-boot-abi": "2\n",
            "cat /etc/passwd": "".join(f"{n}:x:{u}:{u}:account:/home:/bin/sh\n" for n, u in accounts.items()),
            "cat /etc/group": "".join(f"{n}:x:{u}:\n" for n, u in accounts.items()),
        }
        for path in ("/mvm/runtime", "/mvm/sdk", "/nix-store", "/job", "/out",
                     "/work", "/mvm-bins", "/closure-seed"):
            values[f"stat {path}"] = "Type: directory"
        for path in ("/etc/resolv.conf", "/etc/ssl/certs/ca-bundle.crt",
                     "/nix-path-registration"):
            values[f"stat {path}"] = "Type: regular"
        return values

    def test_root_contract_rejects_marker_and_runtime_fallback_defects(self):
        values = self.rootfs_fixture()
        with patch.object(layer, "debugfs", side_effect=lambda _, cmd: values.get(cmd, "")):
            layer.check_rootfs(Path("fixture"), builder=True)
            for command, bad in (
                ("cat /etc/mvm/base-image-contract", "1"),
                ("cat /etc/mvm/base-image-contract", "1\r\n"),
                ("stat /etc/mvm/base-image-contract", "Type: symlink Mode: 0444"),
                ("stat /etc/mvm", "Type: symlink"),
                ("stat /mvm/runtime", ""),
                ("stat /nix-path-registration", ""),
                ("stat /nix-store", ""),
                ("stat /init", "Inode: 99 Type: regular"),
                ("cat /etc/passwd", "root:x:0:0:root:/root:/bin/sh\n"),
                ("ls -p /sbin", "/1/100755/0/0/mvm-setpriv/5/\n"),
                ("cat /etc/mvm/builder-boot-abi", "1\n"),
            ):
                with self.subTest(command=command), patch.dict(values, {command: bad}):
                    with self.assertRaises(ValueError):
                        layer.check_rootfs(Path("fixture"), builder=True)


if __name__ == "__main__":
    unittest.main()
