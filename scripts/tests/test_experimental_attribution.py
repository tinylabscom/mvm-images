"""Pure spike contracts: no Nix, no boot, no claim about enforcement."""

import hashlib
import importlib.util
import json
from pathlib import Path
import re
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "experimental_config", ROOT / "kernel/check-experimental-attribution.py"
)
checker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(checker)
CONTRACT = json.loads((ROOT / "kernel/experimental-attribution.json").read_text())

# Deliberately frozen pre-spike definitions, not reproducibility claims. An
# intentional production update must explicitly review/update these snapshots.
PRODUCTION = {
    "kernel/base.nix": "1ad36a807c153950c8b7764f79677ed7ab11e06d91501fc82616c53cf43fd7e9",
    "kernel/workload.nix": "f79822733f01772032298f37b3f319fb8725612e27a0f3a0566975f42474543c",
    "kernel/rootless.nix": "c8cb6ce89cec1c51a5a07d9e9b581748b7182378717ce52d6e27fd2bc8f34579",
    "kernel/builder.nix": "840687bb7838ce84328a4391bf40574caabaed9736d081512cf0b9f0702a26df",
    "kernel/flake.nix": "d326ddd15864f5348e1c23dace36b0b64e49cd05d019a8151613a8baabc6122b",
    "kernel/flake.lock": "45791c6438ccaeaa312a454bc47f5729c9508381ef52484f3180bae58a5054f7",
    "flake.nix": "fd5c295604aa2aad9ae0aa27217a0f668eda4f57bafeb5c79254122557cf70de",
    "flake.lock": "cb77a86e9593a8ee3f411743c72e71cbfb939d0a2deb91b99e59a1a46d77c9b1",
    "scripts/assemble-release.py": "9c4109543e7eae816752b8c14526dbde6c2db8e09180212c72fae65a1d40f71f",
    "scripts/build-kernel-artifacts.sh": "4d0ce6a18893211fbef0d2134031d1a143ae6d04a709ca9fb8868f0164cf4361",
}


class ExperimentalAttributionTests(unittest.TestCase):
    def test_production_definitions_unchanged(self):
        for name, digest in PRODUCTION.items():
            with self.subTest(file=name):
                data = (ROOT / name).read_bytes()
                if name == "kernel/flake.nix":
                    data, count = re.subn(
                        rb" *# BEGIN development-only experimental-attribution\n"
                        rb".*? *# END development-only experimental-attribution\n\n?",
                        b"", data, flags=re.DOTALL,
                    )
                    self.assertEqual(count, 2)
                self.assertEqual(hashlib.sha256(data).hexdigest(), digest)

    def test_not_referenced_by_publication_or_images(self):
        paths = [ROOT / "flake.nix"]
        for directory in (".github/workflows", "images", "packaging"):
            paths.extend(p for p in (ROOT / directory).rglob("*") if p.is_file())
        paths.extend(ROOT.glob("scripts/*.py"))
        paths.extend(ROOT.glob("scripts/*.sh"))
        for path in paths:
            if path == ROOT / ".github/workflows/experimental-attribution.yml":
                continue  # Dedicated config-only experiment, not a release lane.
            with self.subTest(file=str(path.relative_to(ROOT))):
                self.assertNotIn(b"experimental-attribution", path.read_bytes())

    def test_experimental_workflow_isolated_from_production(self):
        workflow = (ROOT / ".github/workflows/experimental-attribution.yml").read_text()
        self.assertIn("branches: [experiment/kernel-attribution]", workflow)
        self.assertIn("contents: read", workflow)
        self.assertNotIn("workflow_call:", workflow)
        self.assertNotIn("contents: write", workflow)
        self.assertIn('experimental-attribution-vmlinux.drvPath', workflow)
        self.assertIn('experimental-attribution-configfile', workflow)
        self.assertNotIn('nix build "./kernel#workload', workflow)
        self.assertIn("needs: resolve", workflow)
        self.assertIn("experimental-kernel-${{ matrix.system }}", workflow)
        self.assertIn("sha256sum", workflow)
        self.assertNotIn("assemble-release", workflow)
        self.assertNotIn("gh release", workflow)
        for system in ("x86_64-linux", "aarch64-linux"):
            self.assertIn(system, workflow)

    def test_both_architectures_have_only_development_outputs(self):
        flake = (ROOT / "kernel/flake.nix").read_text()
        self.assertIn('systems = [ "aarch64-linux" "x86_64-linux" ];', flake)
        self.assertIn("experimental-attribution-vmlinux = experimentalAttribution;", flake)
        self.assertIn(
            "experimental-attribution-configfile = experimentalAttribution.passthru.configfile;",
            flake,
        )
        recipe = (ROOT / "kernel/experimental-attribution.nix").read_text()
        self.assertIn('import ./workload.nix', recipe)
        self.assertNotIn('import ./rootless.nix', recipe)
        self.assertIn("workload.override { inherit configfile; }", recipe)
        self.assertIn("check-experimental-attribution.py", recipe)

    def test_contract_has_no_network_devices_or_rootless_posture(self):
        enabled, disabled = map(set, (CONTRACT["enables"], CONTRACT["disables"]))
        self.assertFalse(enabled & disabled)
        self.assertTrue({
            "NETDEVICES", "VIRTIO_NET", "TUN", "VETH", "BRIDGE", "MACVLAN",
            "MACVTAP", "NETFILTER", "NET_SCHED", "PACKET", "XFRM",
            "NAMESPACES", "NET_NS", "IO_URING",
        } <= disabled)
        self.assertTrue({
            "BPF_LSM", "BPF_EVENTS", "BPF_JIT", "DEBUG_INFO_BTF",
            "CGROUPS", "CGROUP_BPF", "BPF_UNPRIV_DEFAULT_OFF", "SECURITY_LANDLOCK",
        } <= enabled)

    def test_lsm_attach_requires_function_entry_and_direct_call_support(self):
        # BPF_LSM/BPF_EVENTS/JIT alone loaded successfully but failed attachment
        # on both architectures in run 37983480539. Freeze the attach contract
        # independently of the generic checker tests' generated "good" config.
        required = {
            "FUNCTION_TRACER", "DYNAMIC_FTRACE", "DYNAMIC_FTRACE_WITH_ARGS",
            "DYNAMIC_FTRACE_WITH_DIRECT_CALLS", "CC_OPTIMIZE_FOR_PERFORMANCE",
        }
        self.assertTrue(required <= set(CONTRACT["enables"]))
        # arm64 GCC cannot select CALL_OPS (and hence DIRECT_CALLS) under -Os.
        self.assertIn("CC_OPTIMIZE_FOR_SIZE", CONTRACT["disables"])
        recipe = (ROOT / "kernel/experimental-attribution.nix").read_text()
        # Freeze normalization: retain every unrelated inherited enable, remove
        # only contract disables, then append the required experimental enables.
        # This is a source contract, not Nix evaluation on the native host.
        self.assertIn(
            "enableList = join (lib.unique ((without contract.disables old.enableList)"
            " ++ contract.enables));",
            recipe,
        )
        self.assertIn(
            "without = removed: value: lib.filter (s: !(builtins.elem s removed)) (words value);",
            recipe,
        )
        self.assertIn("without contract.enables old.disableList", recipe)
        self.assertIn("without contract.enables old.requiredDisableList", recipe)

    def test_resolved_config_rejects_every_dropped_enable_and_reverted_disable(self):
        good = "".join(f"CONFIG_{s}=y\n" for s in CONTRACT["enables"])
        good += f'CONFIG_LSM="{CONTRACT["lsm"]}"\n'
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "config"
            config.write_text(good)
            checker.check_config(config, CONTRACT)
            for symbol in CONTRACT["enables"]:
                for value in ("n", "m", None):
                    with self.subTest(symbol=symbol, value=value):
                        replacement = "" if value is None else f"CONFIG_{symbol}={value}\n"
                        config.write_text(good.replace(f"CONFIG_{symbol}=y\n", replacement))
                        with self.assertRaises(ValueError):
                            checker.check_config(config, CONTRACT)
            for symbol in CONTRACT["disables"]:
                for value in ("y", "m"):
                    with self.subTest(symbol=symbol, value=value):
                        config.write_text(good + f"CONFIG_{symbol}={value}\n")
                        with self.assertRaises(ValueError):
                            checker.check_config(config, CONTRACT)
            config.write_text(good.replace('landlock,bpf', 'landlock'))
            with self.assertRaises(ValueError):
                checker.check_config(config, CONTRACT)


if __name__ == "__main__":
    unittest.main()
