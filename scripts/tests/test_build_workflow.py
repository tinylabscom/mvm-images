"""Static admission checks for the Linux-layer build/publication interface."""

import importlib.util
from pathlib import Path
import re
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("build_member_specs", ROOT / "scripts/assemble-release.py")
ASSEMBLER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = ASSEMBLER
SPEC.loader.exec_module(ASSEMBLER)


class BuildWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.build = (ROOT / ".github/workflows/build.yml").read_text()

    def test_every_member_artifact_is_uploaded_by_the_build(self):
        # Use the producer table, not an independent publication-role list.
        uploaded = self.build.replace("${{ matrix.arch }}", "ARCH")
        for member in ASSEMBLER.member_specs():
            for artifact in member.artifacts:
                name = artifact.name
                for arch in ASSEMBLER.ARCHES:
                    name = name.replace(arch, "ARCH")
                if name.startswith("stage0-vmlinux-"):
                    # Assembly aliases the separately checked builder seed.
                    name = "vmlinux-ARCH-builder"
                with self.subTest(artifact=artifact.name):
                    self.assertIn(f"staging/{name}", uploaded)

    def test_both_architectures_use_local_checks_and_no_source_runtime(self):
        for arch in ASSEMBLER.ARCHES:
            self.assertIn(f"- arch: {arch}", self.build)
        for forbidden in ("mvm-source.sh", "MVM_SOURCE", "runtime-overlay", "sdk-sidecar", "initramfs", "sources/"):
            self.assertNotIn(forbidden, self.build)
        self.assertIn("scripts/check_linux_layer.py", self.build)
        self.assertIn("scripts/build-kernel-artifacts.sh", self.build)
        self.assertIn("--profile builder --kernel-config", self.build)
        self.assertIn('--profile "${profile}-tenant"', self.build)
        self.assertIn("--init /usr/local/bin/mvm-rootless-probe", self.build)
        self.assertNotIn("mvmctl", self.build)

    def test_native_contract_boots_are_required_and_probe_is_diagnostic_only(self):
        self.assertIn('.#checks.${ARCH}-linux.base-contract-probe', self.build)
        self.assertIn('scripts/boot-base-contract.py --artifacts staging --arch "$ARCH"', self.build)
        self.assertIn('--expected-contract 1 --accel tcg', self.build)
        self.assertIn('--witness "diagnostics/base-contract-${ARCH}.json"', self.build)
        self.assertIn('sudo modprobe vhost_vsock', self.build)
        self.assertIn('sudo chmod a+rw /dev/vhost-vsock', self.build)
        self.assertIn('qemu-system-arm', self.build)
        self.assertIn('qemu-system-x86', self.build)
        self.assertIn('name: base-contract-probe-${{ matrix.arch }}', self.build)
        self.assertIn('.rootless-tenant.dev', self.build)
        upload = self.build.split('- name: Upload the completed Linux-layer members')[1]
        self.assertNotIn("cpio.gz", upload)
        self.assertNotIn("rootless-microvm-dev", upload)
        boot = self.build.split('- name: Boot every native published workload root')[1].split('- name:')[0]
        self.assertNotIn("continue-on-error", boot)
        self.assertNotIn("if:", boot)
        self.assertNotIn("--plan", boot)

    def test_workflow_run_blocks_are_valid_bash(self):
        for path in sorted((ROOT / ".github/workflows").glob("*.yml")):
            lines = path.read_text().splitlines()
            for index, line in enumerate(lines):
                if line.strip() != "run: |":
                    continue
                depth = len(line) - len(line.lstrip())
                body = []
                for nested in lines[index + 1:]:
                    if nested.strip() and len(nested) - len(nested.lstrip()) <= depth:
                        break
                    body.append(nested[depth + 2:])
                script = re.sub(r"\$\{\{.*?\}\}", "workflow_value", "\n".join(body))
                with self.subTest(workflow=path.name, line=index + 1):
                    result = subprocess.run(
                        ["bash", "-n"], input=script, text=True, capture_output=True,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
