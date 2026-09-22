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


class DatapathContractTests(unittest.TestCase):
    def test_the_repo_datapath_kernel_satisfies_its_contract(self):
        # Runs against the real kernel/datapath.nix: the posture must keep
        # host-facing devices out while requesting the in-guest datapath.
        contract.check_datapath_contract()

    def test_datapath_resolved_config_accepts_bridge_and_veth(self):
        path = ResolvedKernelContractTests().write_config(
            "CONFIG_VSOCKETS=y\n"
            "CONFIG_VIRTIO_VSOCKETS=y\n"
            "CONFIG_NETDEVICES=y\n"
            "CONFIG_BRIDGE=y\n"
            "CONFIG_VETH=y\n"
            "CONFIG_NET_NS=y\n"
            "CONFIG_NETFILTER=y\n"
            "# CONFIG_VIRTIO_NET is not set\n"
            "# CONFIG_TUN is not set\n"
            "# CONFIG_MACVLAN is not set\n"
        )
        path = path.rename(path.with_name("datapath.config"))
        contract.check_resolved_kernel_configs([path])

    def test_datapath_resolved_config_rejects_a_host_facing_device(self):
        for symbol in ("VIRTIO_NET", "TUN", "MACVLAN"):
            with self.subTest(symbol=symbol):
                case = ResolvedKernelContractTests()
                path = case.write_config(
                    "CONFIG_VSOCKETS=y\n"
                    "CONFIG_VIRTIO_VSOCKETS=y\n"
                    "CONFIG_NETDEVICES=y\n"
                    "CONFIG_BRIDGE=y\n"
                    "CONFIG_VETH=y\n"
                    "CONFIG_NET_NS=y\n"
                    "CONFIG_NETFILTER=y\n"
                    f"CONFIG_{symbol}=y\n"
                )
                path = path.rename(path.with_name("datapath.config"))
                with self.assertRaises(contract.ContractError):
                    contract.check_resolved_kernel_configs([path])

    def test_datapath_resolved_config_requires_the_datapath_symbols(self):
        case = ResolvedKernelContractTests()
        path = case.write_config(
            "CONFIG_VSOCKETS=y\n"
            "CONFIG_VIRTIO_VSOCKETS=y\n"
            "# CONFIG_NETDEVICES is not set\n"
        )
        path = path.rename(path.with_name("datapath.config"))
        with self.assertRaises(contract.ContractError):
            contract.check_resolved_kernel_configs([path])


if __name__ == "__main__":
    unittest.main()
