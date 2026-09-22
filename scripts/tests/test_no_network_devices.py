import importlib.util
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "check_no_network_devices", ROOT / "scripts/check_no_network_devices.py"
)
assert SPEC is not None and SPEC.loader is not None
contract = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(contract)


class ResolvedKernelContractTests(unittest.TestCase):
    def write_config(self, text: str) -> Path:
        handle = tempfile.NamedTemporaryFile(mode="w", delete=False)
        self.addCleanup(Path(handle.name).unlink, missing_ok=True)
        with handle:
            handle.write(text)
        return Path(handle.name)

    def test_accepts_vsock_without_network_devices(self):
        path = self.write_config(
            "CONFIG_VSOCKETS=y\n"
            "CONFIG_VIRTIO_VSOCKETS=y\n"
            "# CONFIG_NETDEVICES is not set\n"
        )
        contract.check_resolved_kernel_configs([path])

    def test_rejects_builtin_or_modular_network_devices(self):
        for setting in ("y", "m"):
            with self.subTest(setting=setting):
                path = self.write_config(
                    "CONFIG_VSOCKETS=y\n"
                    "CONFIG_VIRTIO_VSOCKETS=y\n"
                    f"CONFIG_VIRTIO_NET={setting}\n"
                )
                with self.assertRaises(contract.ContractError):
                    contract.check_resolved_kernel_configs([path])

    def test_requires_vsock(self):
        path = self.write_config("# CONFIG_NETDEVICES is not set\n")
        with self.assertRaises(contract.ContractError):
            contract.check_resolved_kernel_configs([path])


if __name__ == "__main__":
    unittest.main()
