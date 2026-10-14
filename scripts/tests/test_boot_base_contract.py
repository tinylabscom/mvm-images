import argparse
import copy
import gzip
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
SPEC = importlib.util.spec_from_file_location("boot_base_contract", ROOT / "scripts/boot-base-contract.py")
contract = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(contract)


def probe_bytes(arch):
    name = b"./bin/busybox\0"
    elf = bytearray(64)
    elf[:6] = b"\x7fELF\x02\x01"
    elf[18:20] = (62 if arch == "x86_64" else 183).to_bytes(2, "little")
    fields = [0, 0o100755, 0, 0, 1, 1, len(elf), 0, 0, 0, 0, len(name), 0]
    data = b"070701" + b"".join(f"{n:08x}".encode() for n in fields) + name
    data += b"\0" * (-len(data) % 4)
    return gzip.compress(data + elf)


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.members = []
        for arch in contract.inventory.ARCHES:
            for member in contract.inventory.member_specs():
                if isinstance(member.role, dict) and member.target["arch"] == arch:
                    for artifact in member.artifacts:
                        (self.directory / artifact.name).write_bytes(artifact.name.encode())
            self.members.extend(contract.build_members(self.directory, arch))
            (self.directory / f"base-contract-probe-{arch}.cpio.gz").write_bytes(probe_bytes(arch))
        self.manifest = self.directory / "image-set.json"
        self.write_manifest()
        self.args = argparse.Namespace(
            artifacts=self.directory, manifest=self.manifest, arch=None,
            expected_contract=None, accel="tcg", timeout=2, plan=False,
            witness=self.directory / "evidence.json",
            probe=[f"{a}={self.directory}/base-contract-probe-{a}.cpio.gz"
                   for a in contract.inventory.ARCHES],
        )
        self.vsock = patch.object(contract.boot, "host_supports_vhost_vsock", return_value=True)
        self.vsock.start()
        self.addCleanup(self.vsock.stop)

    def write_manifest(self):
        self.manifest.write_text(json.dumps({
            "schema_version": 3, "compatibility": {"base_image_contract": 1},
            "members": self.members,
        }))

    def test_candidate_witness_binds_all_six_actual_pairs_and_probes(self):
        with patch.object(contract.boot, "_require_binary"), patch.object(contract.boot, "run_until_ready") as run:
            evidence = contract.execute(self.args)
        self.assertEqual(6, run.call_count)
        self.assertTrue(evidence["evidence"])
        self.assertEqual("assembled-candidate", evidence["scope"])
        self.assertEqual(contract.inventory.sha256_file(self.manifest), evidence["manifest_sha256"])
        self.assertEqual(evidence, json.loads(self.args.witness.read_text()))
        for boot in evidence["boots"]:
            self.assertEqual(64, len(boot["probe"]["sha256"]))
            self.assertEqual(64, len(boot["kernel"]["sha256"]))
            self.assertEqual(64, len(boot["rootfs"]["sha256"]))
        run.assert_called_with(evidence["boots"][-1]["vmm_plan"], 2, contract.READY,
                               exact_marker=True, refusal_marker=contract.REFUSED)

    def test_failed_boot_never_writes_success(self):
        self.args.witness.write_text("stale")
        with patch.object(contract.boot, "_require_binary"), patch.object(
            contract.boot, "run_until_ready", side_effect=contract.boot.E2EError("refused")
        ):
            with self.assertRaises(contract.boot.E2EError):
                contract.execute(self.args)
        self.assertFalse(self.args.witness.exists())

    def test_plan_is_not_evidence_and_does_not_boot_or_write(self):
        self.args.plan = True
        with patch.object(contract.boot, "run_until_ready") as run:
            result = contract.execute(self.args)
        run.assert_not_called()
        self.assertFalse(result["evidence"])
        self.assertFalse(self.args.witness.exists())

    def test_native_build_diagnostic_has_three_roots_no_fabricated_release(self):
        self.args.manifest = None
        self.args.arch = "aarch64"
        self.args.expected_contract = 1
        with patch.object(contract.boot, "_require_binary"), patch.object(contract.boot, "run_until_ready"):
            result = contract.execute(self.args)
        self.assertEqual("build-diagnostic", result["scope"])
        self.assertEqual(3, len(result["boots"]))
        for token in ("producer", "release_tag", "manifest_sha256"):
            self.assertNotIn(token, result)

    def test_build_requires_explicit_contract_and_arch(self):
        self.args.manifest = None
        with self.assertRaises(contract.boot.E2EError):
            contract.execute(self.args)

    def test_manifest_requires_bare_integer_contract_and_schema(self):
        manifest = json.loads(self.manifest.read_text())
        for key, value in (("base_image_contract", True), ("base_image_contract", 1.0),
                           ("base_image_contract", "1"), ("schema_version", 3.0)):
            changed = copy.deepcopy(manifest)
            if key == "schema_version":
                changed[key] = value
            else:
                changed["compatibility"][key] = value
            self.manifest.write_text(json.dumps(changed))
            with self.subTest(key=key, value=value), \
                 patch.object(contract.boot, "run_until_ready") as run:
                with self.assertRaisesRegex(contract.boot.E2EError, "schema 3"):
                    contract.execute(self.args)
                run.assert_not_called()
            self.assertFalse(self.args.witness.exists())

    def test_hash_tamper_refused_before_any_boot(self):
        name = self.members[0]["artifacts"][0]["name"]
        (self.directory / name).write_bytes(b"tampered")
        with patch.object(contract.boot, "run_until_ready") as run:
            with self.assertRaisesRegex(contract.boot.E2EError, "digest/size"):
                contract.execute(self.args)
        run.assert_not_called()
        self.assertFalse(self.args.witness.exists())

    def test_changed_boot_inputs_refuse_before_evidence_is_written(self):
        root = next(m for m in self.members if "workload_rootfs" in m["role"])
        paths = (
            self.directory / root["artifacts"][0]["name"],
            self.manifest,
            self.directory / "base-contract-probe-x86_64.cpio.gz",
        )
        for path in paths:
            original = path.read_bytes()
            def mutate(*args, **kwargs):
                path.write_bytes(original + b"concurrent host mutation")
            with self.subTest(path=path.name), \
                 patch.object(contract.boot, "_require_binary"), \
                 patch.object(contract.boot, "run_until_ready", side_effect=mutate):
                with self.assertRaises(contract.boot.E2EError):
                    contract.execute(self.args)
            self.assertFalse(self.args.witness.exists())
            path.write_bytes(original)

    def test_plain_artifact_names_only(self):
        for name in ("../escape", "/absolute", "file,if=virtio", ".", "a/b"):
            with self.subTest(name=name), self.assertRaises(contract.boot.E2EError):
                contract.artifact_path(self.directory, {"name": name})

    def test_witness_cannot_mutate_candidate_root_or_other_inputs(self):
        root = next(m for m in self.members if "workload_rootfs" in m["role"])
        for path in (self.directory / root["artifacts"][0]["name"], self.manifest,
                     self.directory / "base-contract-probe-x86_64.cpio.gz"):
            original = path.read_bytes()
            self.args.witness = path
            with self.subTest(path=path.name), self.assertRaisesRegex(contract.boot.E2EError, "overwrite"):
                contract.execute(self.args)
            self.assertEqual(original, path.read_bytes())

    def test_missing_duplicate_or_wrong_profile_mode_pair_refused(self):
        for change in ("missing-root", "duplicate-root", "kernel-mode", "kernel-profile"):
            members = copy.deepcopy(self.members)
            root = next(m for m in members if "workload_rootfs" in m["role"])
            kernel = next(m for m in members if "workload_kernel" in m["role"])
            if change == "missing-root":
                members.remove(root)
            elif change == "duplicate-root":
                members.append(root)
            elif change == "kernel-mode":
                kernel["build_mode"] = "unknown"
            else:
                kernel["role"]["workload_kernel"] = "unknown"
            with self.subTest(change=change), self.assertRaises(contract.boot.E2EError):
                contract.select_pairs(members, contract.inventory.ARCHES)

    def test_probe_architecture_and_paths_are_checked(self):
        path = self.directory / "base-contract-probe-x86_64.cpio.gz"
        path.write_bytes(probe_bytes("aarch64"))
        with self.assertRaisesRegex(contract.boot.E2EError, "architecture mismatch"):
            contract.execute(self.args)
        self.args.probe = [f"x86_64={self.directory}/base-contract-probe-aarch64.cpio.gz"]
        with self.assertRaisesRegex(contract.boot.E2EError, "filename"):
            contract.execute(self.args)

    def test_missing_vsock_refuses_even_plan(self):
        self.args.plan = True
        with patch.object(contract.boot, "host_supports_vhost_vsock", return_value=False):
            with self.assertRaisesRegex(contract.boot.E2EError, "requires usable"):
                contract.execute(self.args)

    def test_probe_and_script_ownership_and_exact_marker_source(self):
        source = (ROOT / "images/base-contract-probe.nix").read_text()
        for token in ("pkgs.pkgsStatic.busybox", "mount -t ext4 -o ro,noload",
                      '[ ! -L "$marker" ] && [ -f "$marker" ]',
                      'stat -c %a "$marker"', '= 444', "printf '1\\n'",
                      "mount -t sysfs sysfs /sys", "/sys/class/net/lo",
                      '0x0013 ] && [ -L "$device/driver" ]',
                      '[ "$vsock" = yes ] || refuse',
                      'cmp -s /expected-contract "$marker"', contract.READY, contract.REFUSED):
            self.assertIn(token, source)
        self.assertLess(source.index("cmp -s"), source.index("echo " + contract.READY))
        self.assertNotIn("tree/init", source)
        self.assertNotIn("mvmctl", source)
        self.assertNotIn("runtime-overlay", source)

    def test_probe_marker_shell_accepts_only_exact_regular_0444_bytes(self):
        # Execute the probe's actual marker validation, not a Python reimplementation.
        # Only filesystem paths and host stat syntax are adapted; no mount/VM involved.
        source = (ROOT / "images/base-contract-probe.nix").read_text()
        validation = source.split("marker=/workload/etc/mvm/base-image-contract\n", 1)[1]
        validation = validation.split("echo " + contract.READY, 1)[0]
        validation = validation.replace("/expected-contract", '"$2"')
        script = """
refuse() { exit 42; }
stat() {
  "$PYTHON" -c 'import os,sys;print(oct(os.stat(sys.argv[-1]).st_mode & 0o7777)[2:])' "$@"
}
marker="$1"
""" + validation
        marker = self.directory / "marker"
        for value, mode, status in (
            (b"1\n", 0o444, 0), (b"1", 0o444, 42),
            (b"1\n\n", 0o444, 42), (b"2\n", 0o444, 42),
            (b"", 0o444, 42), (b"1\n", 0o644, 42),
            (b"1\n", 0o4444, 42),
        ):
            marker.write_bytes(value)
            marker.chmod(mode)
            with self.subTest(value=value, mode=oct(mode)):
                result = subprocess.run(
                    ["sh", "-c", script, "probe-validation", str(marker), str(self.directory / "expected")],
                    env={**os.environ, "PYTHON": sys.executable}, capture_output=True,
                )
                self.assertEqual(status, result.returncode, result.stderr)
            marker.chmod(0o644)
        marker.unlink()
        target = self.directory / "target"
        target.write_bytes(b"1\n")
        target.chmod(0o444)
        marker.symlink_to(target)
        result = subprocess.run(
            ["sh", "-c", script, "probe-validation", str(marker), str(self.directory / "expected")],
            env={**os.environ, "PYTHON": sys.executable}, capture_output=True,
        )
        self.assertEqual(42, result.returncode)


if __name__ == "__main__":
    unittest.main()
