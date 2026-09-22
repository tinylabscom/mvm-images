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
