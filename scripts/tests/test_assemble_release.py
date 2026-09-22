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
assert SPEC and SPEC.loader
ASSEMBLER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = ASSEMBLER
SPEC.loader.exec_module(ASSEMBLER)


class AssembleReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.assets = self.root / "assets"
        self.assets.mkdir()
        for member in ASSEMBLER.member_specs():
            for artifact in member.artifacts:
                if artifact.name.startswith("stage0-vmlinux-"):
                    continue
                (self.assets / artifact.name).write_bytes(f"bytes:{artifact.name}".encode())
        for arch in ASSEMBLER.ARCHES:
            (self.assets / f"vmlinux-{arch}-builder").write_bytes(
                f"stage0:{arch}".encode()
            )
        self.mvm = self.root / "mvm"
        agent = self.mvm / "crates/mvm-agentd/src/vsock"
        builder = self.mvm / "crates/mvm-build/src"
        agent.mkdir(parents=True)
        builder.mkdir(parents=True)
        (agent / "mod.rs").write_text(
            "pub const PROTOCOL_VERSION: u32 = 3;\n"
            "pub const MIN_SUPPORTED_PROTOCOL_VERSION: u32 = 2;\n"
        )
        (builder / "builder_vm.rs").write_text(
            "pub const BUILDER_VM_CACHE_CONTRACT_VERSION: u32 = 4;\n"
        )
        self.lock = self.root / "flake.lock"
        self.lock.write_text(
            json.dumps(
                {"nodes": {"mvm": {"locked": {"rev": "a" * 40}}}, "version": 7}
            )
        )

    def tearDown(self):
        self.temp.cleanup()

    def run_assembler(self):
        return subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--artifacts",
                str(self.assets),
                "--tag",
                "image-set/v0.1.0",
                "--source-commit",
                "b" * 40,
                "--issued-at",
                "2026-09-22T12:00:00Z",
                "--mvm-source",
                str(self.mvm),
                "--flake-lock",
                str(self.lock),
            ],
            text=True,
            capture_output=True,
            check=False,
        )

    def test_complete_set_emits_every_consumer_role_and_metadata(self):
        result = self.run_assembler()
        self.assertEqual(result.returncode, 0, result.stderr)
        manifest = json.loads((self.assets / "image-set.json").read_text())
        self.assertEqual(len(manifest["members"]), 19)
        self.assertEqual(manifest["set_version"], "0.1.0")
        self.assertEqual(manifest["compatibility"]["guest_agent_protocol"], {"min": 2, "max": 3})
        self.assertEqual(manifest["compatibility"]["builder_cache_contract"], 4)
        for member in ASSEMBLER.member_specs():
            self.assertTrue((self.assets / f"pack-{member.slug}.json").is_file())
            self.assertTrue((self.assets / f"sbom-{member.slug}.spdx.json").is_file())
        for arch in ASSEMBLER.ARCHES:
            self.assertEqual(
                (self.assets / f"stage0-vmlinux-{arch}").read_bytes(),
                (self.assets / f"vmlinux-{arch}-builder").read_bytes(),
            )
        self.assertIn('workflow = ".github/workflows/release.yml"', (self.assets / "images.lock").read_text())

    def test_missing_member_artifact_refuses_the_candidate(self):
        missing = self.assets / "sdk-sidecar-aarch64-musl.tar.gz"
        missing.unlink()
        result = self.run_assembler()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(missing.name, result.stderr)
        self.assertFalse((self.assets / "image-set.json").exists())


if __name__ == "__main__":
    unittest.main()
