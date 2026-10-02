import importlib.util
from pathlib import Path
from unittest import mock
import unittest


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "check_kvm_access", ROOT / "scripts/check_kvm_access.py"
)
assert SPEC is not None and SPEC.loader is not None
check_kvm_access = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(check_kvm_access)


class KvmAccessTests(unittest.TestCase):
    @mock.patch.object(check_kvm_access.os, "close")
    @mock.patch.object(check_kvm_access.fcntl, "ioctl", return_value=12)
    @mock.patch.object(check_kvm_access.os, "open", return_value=42)
    def test_probe_opens_device_read_write_and_uses_kvm_ioctl(
        self, open_device, ioctl, close_device
    ):
        check_kvm_access.probe_kvm()

        open_device.assert_called_once_with(
            "/dev/kvm", check_kvm_access.os.O_RDWR | check_kvm_access.os.O_CLOEXEC
        )
        ioctl.assert_called_once_with(42, check_kvm_access.KVM_GET_API_VERSION)
        close_device.assert_called_once_with(42)

    @mock.patch.object(
        check_kvm_access.os, "open", side_effect=PermissionError(13, "Permission denied")
    )
    def test_probe_rejects_device_the_current_user_cannot_open(self, _open_device):
        with self.assertRaises(PermissionError):
            check_kvm_access.probe_kvm()

    @mock.patch.object(check_kvm_access.os, "close")
    @mock.patch.object(check_kvm_access.fcntl, "ioctl", return_value=11)
    @mock.patch.object(check_kvm_access.os, "open", return_value=42)
    def test_probe_rejects_an_incompatible_kvm_api(
        self, _open_device, _ioctl, close_device
    ):
        with self.assertRaisesRegex(RuntimeError, "returned 11, expected 12"):
            check_kvm_access.probe_kvm()
        close_device.assert_called_once_with(42)


class FirecrackerWorkflowTests(unittest.TestCase):
    def test_firecracker_steps_are_gated_by_successful_kvm_preflight(self):
        workflow = (ROOT / ".github/workflows/image-owned-ci.yml").read_text()
        firecracker_job = workflow.split("  boot-witness-firecracker:", 1)[1].split(
            "  boot-witness-macos:", 1
        )[0]

        self.assertIn("id: kvm", firecracker_job)
        self.assertIn("python3 scripts/check_kvm_access.py", firecracker_job)
        self.assertIn("available=false", firecracker_job)
        self.assertIn("KVM is inaccessible", firecracker_job)
        self.assertEqual(
            firecracker_job.count(
                "if: steps.kvm.outputs.available == 'true'"
            ),
            4,
            "artifact download, Firecracker install, boot, and upload must be gated",
        )
        upload = firecracker_job.split("- name: Upload firecracker witness", 1)[1]
        self.assertIn("if: steps.kvm.outputs.available == 'true'", upload)


if __name__ == "__main__":
    unittest.main()
