import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("e2e_boot", ROOT / "scripts/e2e_boot.py")
assert SPEC is not None and SPEC.loader is not None
e2e_boot = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(e2e_boot)


class E2EBootPlanTests(unittest.TestCase):
    def test_image_owned_smoke_boots_use_ext4_compatibility_and_emit_witnesses(self):
        workflow = (ROOT / ".github/workflows/image-owned-ci.yml").read_text(
            encoding="utf-8"
        )
        self.assertEqual(3, workflow.count("--rootfs-type ext4 --witness"))
        for witness in (
            "/tmp/qemu-boot-witness.json",
            "/tmp/firecracker-boot-witness.json",
            "/tmp/macos-boot-witness.json",
        ):
            self.assertIn(f"--witness {witness}", workflow)

    def test_firecracker_install_is_versioned_and_checksum_verified(self):
        workflow = (ROOT / ".github/workflows/image-owned-ci.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("readonly FIRECRACKER_TAG=v1.12.0", workflow)
        self.assertIn("firecracker-${FIRECRACKER_TAG}-${fc_arch}.tgz", workflow)
        self.assertIn("sha256sum --check", workflow)
        self.assertNotIn("releases/latest/download/firecracker-", workflow)

    def test_qemu_plan_has_explicit_vsock_and_no_implicit_devices(self):
        command = e2e_boot.qemu_command(
            binary="qemu-system-x86_64",
            kernel=Path("kernel.img"),
            rootfs=Path("rootfs.bin"),
            guest_cid=9,
        )
        self.assertIn("-nodefaults", command)
        if e2e_boot.host_supports_vhost_vsock():
            self.assertTrue(any("vhost-vsock" in arg for arg in command))
        else:
            self.assertFalse(any("vhost-vsock" in arg for arg in command))
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
        if e2e_boot.host_supports_vhost_vsock():
            self.assertTrue(any("vhost-vsock" in arg for arg in command))
        else:
            self.assertFalse(any("vhost-vsock" in arg for arg in command))
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

    def test_success_witness_records_ready_marker_and_actual_plan(self):
        plan = ["qemu-system-x86_64", "-nodefaults", "-drive", "file=rootfs.bin"]
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "evidence" / "qemu.json"
            e2e_boot.write_witness(
                path,
                backend="qemu",
                ready_marker=e2e_boot.READY_MARKER,
                rootfs_name="rootfs.bin",
                rootfs_type="ext4",
                vmm_plan=plan,
            )
            witness = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual("qemu", witness["backend"])
        self.assertEqual("ext4", witness["rootfs_type"])
        self.assertEqual(e2e_boot.READY_MARKER, witness["ready_marker"])
        self.assertEqual(plan, witness["vmm_plan"])


if __name__ == "__main__":
    unittest.main()
