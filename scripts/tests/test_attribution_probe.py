"""Host-plan/acceptance/source isolation checks, NOT guest enforcement evidence."""
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "attribution_boot", ROOT / "scripts/experimental_attribution_boot.py"
)
boot = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(boot)


class ProbeTests(unittest.TestCase):
    def test_explicit_devices_and_bounded_success_contract(self):
        for arch in ("x86_64", "aarch64"):
            cmd = boot.command(arch, Path("/kernel"), Path("/rootfs"), "tcg")
            devices = [cmd[i + 1] for i, item in enumerate(cmd) if item == "-device"]
            self.assertEqual(devices, [
                "virtio-blk-pci,drive=probe", "vhost-vsock-pci,guest-cid=9042"
            ])
            self.assertIn("-nodefaults", cmd)
            self.assertIn("readonly=on", " ".join(cmd))
            self.assertEqual(cmd[cmd.index("-net") + 1], "none")
            for forbidden in ("virtio-net", "-netdev", "mvmctl", "bin/dev", "runtime-overlay"):
                self.assertNotIn(forbidden, " ".join(cmd))

    def test_acceptance_requires_both_exact_markers_clean_exit_and_no_failure(self):
        good = boot.PASS + "\r\n" + boot.UNSUPPORTED + "\r\n"
        boot.validate_output(good, 0)
        for output, code in [
            ("", 0), (boot.PASS, 0), (good, 1), (good + boot.PASS + "\n", 0),
            (good + "ATTRIBUTION-PROBE:FAIL:child\n", 0),
            ("prefix" + good, 0), (good.replace("partial", "production"), 0),
        ]:
            with self.subTest(output=output, code=code):
                with self.assertRaises(ValueError):
                    boot.validate_output(output, code)

    def test_invalid_plan_rejected(self):
        for arch, accel in (("riscv64", "kvm"), ("aarch64", "hvf")):
            with self.assertRaises(ValueError):
                boot.command(arch, Path("k"), Path("r"), accel)

    def test_native_image_owned_probe_only(self):
        recipe = (ROOT / "kernel/attribution-probe/default.nix").read_text()
        self.assertIn("chmod 0551 root/tool", recipe)
        self.assertIn("pkgs.libbpf", recipe)
        self.assertNotIn("mvm", recipe)
        init = (ROOT / "kernel/attribution-probe/init.c").read_text()
        for required in ("bpf_object__load", "bpf_program__attach_cgroup",
                         "bpf_program__attach_lsm", "MSG_CTRUNC", "PR_SET_DUMPABLE",
                         "SYS_capget", "CAPBSET_DROP", "SYS_lsm_list_modules"):
            self.assertIn(required, init)
        self.assertIn('put("/proc/sys/kernel/unprivileged_bpf_disabled", "1")', init)
        self.assertLess(init.index('attach(obj, "receive"'), init.index("launch(false, false, -1)"))
        self.assertNotIn("/proc/self", init)


if __name__ == "__main__":
    unittest.main()
