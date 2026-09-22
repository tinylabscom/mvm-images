import importlib.util
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "check_rootless_tenant", ROOT / "scripts/check_rootless_tenant.py"
)
assert SPEC is not None and SPEC.loader is not None
contract = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(contract)


class RootlessTenantContractTests(unittest.TestCase):
    def test_repository_contract(self):
        contract.check_rootless_contract()

    def test_resolved_kernel_requires_rootless_floor_and_no_network_namespace(self):
        required = (
            "NAMESPACES",
            "UTS_NS",
            "IPC_NS",
            "USER_NS",
            "PID_NS",
            "CGROUPS",
            "MEMCG",
            "BLK_CGROUP",
            "CGROUP_SCHED",
            "CGROUP_PIDS",
            "CPUSETS",
            "UNIX98_PTYS",
            "INOTIFY_USER",
            "FANOTIFY",
            "VSOCKETS",
            "VIRTIO_VSOCKETS",
        )
        with tempfile.NamedTemporaryFile(mode="w", delete=False) as handle:
            handle.write("".join(f"CONFIG_{symbol}=y\n" for symbol in required))
            handle.write("# CONFIG_NET_NS is not set\n")
            path = Path(handle.name)
        self.addCleanup(path.unlink, missing_ok=True)
        contract.check_resolved_kernel_config(path)

        path.write_text(path.read_text() + "CONFIG_NET_NS=y\n")
        with self.assertRaises(contract.ContractError):
            contract.check_resolved_kernel_config(path)


if __name__ == "__main__":
    unittest.main()
