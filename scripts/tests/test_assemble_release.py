import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/assemble-release.py"
SPEC = importlib.util.spec_from_file_location("assemble_release", SCRIPT)
ASSEMBLER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = ASSEMBLER
SPEC.loader.exec_module(ASSEMBLER)


class ContractTests(unittest.TestCase):
    def test_local_contract_and_builder_abi(self):
        self.assertEqual(ASSEMBLER.base_image_contract(ROOT), 1)
        self.assertEqual(ASSEMBLER.builder_boot_abi(ROOT), 2)

    def test_contract_is_a_single_supported_integer(self):
        for body in ("0\n", "2\n", "1\n2\n", "# contract\n1\n", '"1"\n'):
            with self.subTest(body=body), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "images"
                path.mkdir()
                (path / "base-image-contract.nix").write_text(body)
                with self.assertRaises(ASSEMBLER.Refusal):
                    ASSEMBLER.base_image_contract(Path(tmp))

    def test_missing_boot_abi_and_invalid_integer_are_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ASSEMBLER.Refusal):
                ASSEMBLER.builder_boot_abi(Path(tmp))
            path = Path(tmp) / "images/builder-vm"
            path.mkdir(parents=True)
            for body in ("# comment\n", "0\n1\n", "abi = 2;\n"):
                (path / "boot-abi.nix").write_text(body)
                with self.assertRaises(ASSEMBLER.Refusal):
                    ASSEMBLER.builder_boot_abi(Path(tmp))


class AssembleReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.assets = self.root / "assets"
        self.assets.mkdir()
        for member in ASSEMBLER.member_specs():
            for artifact in member.artifacts:
                if not artifact.name.startswith("stage0-vmlinux-"):
                    (self.assets / artifact.name).write_bytes(artifact.name.encode())
        for arch in ASSEMBLER.ARCHES:
            (self.assets / f"vmlinux-{arch}-builder").write_bytes(arch.encode())
        self.subjects = self.root / "subjects.sha256"
        self.lock = self.root / "flake.lock"
        # No mvm source or locked mvm node is needed to produce a Linux set.
        self.lock.write_text('{"nodes": {}, "version": 7}\n')

    def tearDown(self):
        self.temp.cleanup()

    def run_assembler(self, subjects=None):
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--artifacts", str(self.assets),
             "--tag", "image-set/v0.1.0", "--source-commit", "b" * 40,
             "--issued-at", "2026-09-22T12:00:00Z", "--flake-lock", str(self.lock),
             "--subjects", str(subjects or self.subjects)],
            text=True, capture_output=True, check=False,
        )

    def test_complete_linux_set_preserves_profiles_arches_and_dev_variants(self):
        result = self.run_assembler()
        self.assertEqual(result.returncode, 0, result.stderr)
        manifest = json.loads((self.assets / "image-set.json").read_text())
        self.assertEqual(manifest["schema_version"], 3)
        self.assertEqual(len(manifest["members"]), 17)
        self.assertEqual(manifest["set_version"], "0.1.0")
        self.assertNotIn("mvm_source_commit", manifest)
        self.assertEqual(manifest["compatibility"], {
            "base_image_contract": 1, "builder_cache_contract": 5, "builder_boot_abi": 2,
        })
        self.assertIs(type(manifest["compatibility"]["base_image_contract"]), int)
        required = [m for m in manifest["members"] if m.get("build_mode") != "dev"]
        self.assertEqual(len(required), 13)
        for member in manifest["members"]:
            self.assertNotIn("source_fingerprint", member)
            self.assertNotIn(member["role"], ["runtime_overlay", "initramfs"])
            if isinstance(member["role"], dict):
                self.assertNotIn("sdk_sidecar", member["role"])
            self.assertNotIn("network_device", member["required_capabilities"])
        for arch in ASSEMBLER.ARCHES:
            roles = [m["role"] for m in required if m["target"] == {"arch": arch}]
            self.assertEqual(len(roles), 6)
            for profile in ("default_tenant", "rootless_tenant"):
                self.assertIn({"workload_kernel": profile}, roles)
                self.assertIn({"workload_rootfs": profile}, roles)
            self.assertEqual(
                (self.assets / f"stage0-vmlinux-{arch}").read_bytes(),
                (self.assets / f"vmlinux-{arch}-builder").read_bytes(),
            )
        self.assertIn('workflow = ".github/workflows/release.yml"',
                      (self.assets / "images.lock").read_text())

    def test_missing_linux_artifact_refuses_candidate(self):
        missing = self.assets / "rootless-microvm-rootfs-aarch64.ext4"
        missing.unlink()
        result = self.run_assembler()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(missing.name, result.stderr)
        self.assertFalse((self.assets / "image-set.json").exists())
        self.assertFalse(self.subjects.exists())

    def test_provenance_subjects_derive_only_from_members_and_root(self):
        result = self.run_assembler()
        self.assertEqual(result.returncode, 0, result.stderr)
        expected = {"image-set.json"}
        for member in ASSEMBLER.member_specs():
            expected.update(a.name for a in member.artifacts)
            expected.update((f"pack-{member.slug}.json", f"sbom-{member.slug}.spdx.json"))
        subjects = {}
        for line in self.subjects.read_text().splitlines():
            digest, name = line.split("  ", 1)
            self.assertNotIn(name, subjects)
            subjects[name] = digest
        self.assertEqual(set(subjects), expected)
        for name, digest in subjects.items():
            self.assertEqual(digest, ASSEMBLER.sha256_file(self.assets / name))
        self.assertNotIn("images.lock", subjects)

    def test_subjects_cannot_be_published_as_unattested_asset(self):
        inside = self.assets / "subjects.sha256"
        result = self.run_assembler(inside)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("outside", result.stderr)
        self.assertFalse(inside.exists())
        self.assertFalse((self.assets / "image-set.json").exists())


if __name__ == "__main__":
    unittest.main()
