import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("e2e_boot", ROOT / "scripts/e2e_boot.py")
assert SPEC is not None and SPEC.loader is not None
e2e_boot = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(e2e_boot)


class E2EBootPlanTests(unittest.TestCase):
    def test_qemu_plan_has_explicit_vsock_and_no_implicit_devices(self):
        command = e2e_boot.qemu_command(
            binary="qemu-system-x86_64",
            kernel=Path("kernel.img"),
            rootfs=Path("rootfs.bin"),
            guest_cid=9,
        )
        self.assertIn("-nodefaults", command)
        self.assertTrue(any("vhost-vsock" in arg for arg in command))
        e2e_boot.assert_no_network_devices(command)

    def test_qemu_tcg_plan_keeps_explicit_vsock_and_uses_emulated_cpu(self):
        command = e2e_boot.qemu_command(
            binary="qemu-system-x86_64",
            kernel=Path("kernel.img"),
            rootfs=Path("rootfs.ext4"),
            guest_cid=9,
            accel="tcg",
            rootfs_type="ext4",
        )
        self.assertIn("q35,accel=tcg", command)
        self.assertIn("max", command)
        self.assertTrue(any("rootfstype=ext4" in arg for arg in command))
        self.assertTrue(any("vhost-vsock" in arg for arg in command))
        e2e_boot.assert_no_network_devices(command)

    def test_qemu_runtime_overlay_is_read_only_second_block_device(self):
        command = e2e_boot.qemu_command(
            binary="qemu-system-x86_64",
            kernel=Path("kernel.img"),
            rootfs=Path("rootfs.ext4"),
            runtime_overlay=Path("runtime-overlay.ext4"),
            guest_cid=9,
        )
        self.assertTrue(any("mvm.runtime_data=/dev/vdb" in arg for arg in command))
        self.assertTrue(
            any("mvm.runtime_source_policy=required_overlay" in arg for arg in command)
        )
        self.assertIn(
            "id=runtime,file=runtime-overlay.ext4,format=raw,if=none,readonly=on",
            command,
        )
        self.assertIn("virtio-blk-pci,drive=runtime", command)
        e2e_boot.assert_no_network_devices(command)

    def test_firecracker_plan_has_vsock_and_no_network_interfaces(self):
        config = e2e_boot.firecracker_config(
            kernel=Path("vmlinux"),
            rootfs=Path("rootfs.bin"),
            vsock_path=Path("vsock.sock"),
            guest_cid=9,
        )
        self.assertIn("vsock", config)
        self.assertNotIn("network-interfaces", config)
        e2e_boot.assert_no_network_devices(config)

    def test_firecracker_runtime_overlay_is_read_only_second_block_device(self):
        config = e2e_boot.firecracker_config(
            kernel=Path("vmlinux"),
            rootfs=Path("rootfs.ext4"),
            runtime_overlay=Path("runtime-overlay.ext4"),
            vsock_path=Path("vsock.sock"),
            guest_cid=9,
        )
        self.assertIn("mvm.runtime_data=/dev/vdb", config["boot-source"]["boot_args"])
        self.assertIn(
            "mvm.runtime_source_policy=required_overlay",
            config["boot-source"]["boot_args"],
        )
        self.assertEqual(
            {
                "drive_id": "runtime",
                "path_on_host": "runtime-overlay.ext4",
                "is_root_device": False,
                "is_read_only": True,
            },
            config["drives"][1],
        )
        self.assertNotIn("network-interfaces", config)
        e2e_boot.assert_no_network_devices(config)

    def test_guard_rejects_qemu_network_device(self):
        with self.assertRaises(e2e_boot.E2EError):
            e2e_boot.assert_no_network_devices(
                ["qemu-system-x86_64", "-device", "virtio-net-pci"]
            )

    def test_guard_rejects_firecracker_network_interface(self):
        with self.assertRaises(e2e_boot.E2EError):
            e2e_boot.assert_no_network_devices({"network-interfaces": []})


if __name__ == "__main__":
    unittest.main()
