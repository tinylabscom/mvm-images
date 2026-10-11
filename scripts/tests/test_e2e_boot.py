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

    def test_qemu_boots_the_init_the_image_owns(self):
        command = e2e_boot.qemu_command(
            binary="qemu-system-x86_64",
            kernel=Path("kernel.img"),
            rootfs=Path("rootfs.ext4"),
            guest_cid=9,
            init="/usr/local/bin/mvm-rootless-probe",
        )
        cmdline = command[command.index("-append") + 1]
        self.assertIn("init=/usr/local/bin/mvm-rootless-probe", cmdline.split())
        self.assertNotIn("mvm.", cmdline)
        self.assertEqual(1, sum(arg.startswith("virtio-blk-pci") for arg in command))

    def test_default_init_is_the_smoke_image_busybox_init(self):
        command = e2e_boot.qemu_command(
            binary="qemu-system-x86_64",
            kernel=Path("kernel.img"),
            rootfs=Path("rootfs.bin"),
            guest_cid=9,
        )
        cmdline = command[command.index("-append") + 1]
        self.assertIn(f"init={e2e_boot.DEFAULT_INIT}", cmdline.split())

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

    def test_firecracker_boots_the_init_the_image_owns_from_one_drive(self):
        config = e2e_boot.firecracker_config(
            kernel=Path("vmlinux"),
            rootfs=Path("rootfs.ext4"),
            vsock_path=Path("vsock.sock"),
            guest_cid=9,
            init="/usr/local/bin/mvm-rootless-probe",
        )
        boot_args = config["boot-source"]["boot_args"].split()
        self.assertIn("init=/usr/local/bin/mvm-rootless-probe", boot_args)
        self.assertFalse(any(arg.startswith("mvm.") for arg in boot_args))
        self.assertEqual(["rootfs"], [d["drive_id"] for d in config["drives"]])
        self.assertNotIn("network-interfaces", config)
        e2e_boot.assert_no_network_devices(config)

    def test_mkguest_shell_init_is_refused(self):
        for builder in (
            lambda: e2e_boot.qemu_command(
                binary="qemu-system-x86_64",
                kernel=Path("kernel.img"),
                rootfs=Path("rootfs.bin"),
                guest_cid=9,
                init="/init",
            ),
            lambda: e2e_boot.firecracker_config(
                kernel=Path("vmlinux"),
                rootfs=Path("rootfs.bin"),
                vsock_path=Path("vsock.sock"),
                guest_cid=9,
                init="/init",
            ),
        ):
            with self.assertRaises(e2e_boot.E2EError):
                builder()

    def test_init_must_be_one_absolute_path(self):
        for init in ("sbin/init", "/sbin/init mvm.runtime_data=/dev/vdb"):
            with self.assertRaises(e2e_boot.E2EError):
                e2e_boot.firecracker_config(
                    kernel=Path("vmlinux"),
                    rootfs=Path("rootfs.bin"),
                    vsock_path=Path("vsock.sock"),
                    guest_cid=9,
                    init=init,
                )

    def test_harness_and_recipes_carry_no_guest_runtime_token(self):
        for relative in ("scripts/e2e_boot.py", "justfile"):
            source = (ROOT / relative).read_text(encoding="utf-8")
            for token in ("init=/init", "mvm.runtime_", "--runtime-overlay"):
                self.assertNotIn(token, source, f"{relative} carries {token}")

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
