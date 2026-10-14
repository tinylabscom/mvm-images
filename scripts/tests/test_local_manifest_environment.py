"""The producer's declared environment is used by ordinary and platform CI."""

from pathlib import Path
import unittest

from test_release_workflow import block, mapping, step_field, steps


ROOT = Path(__file__).resolve().parents[2]


class LocalManifestEnvironmentTests(unittest.TestCase):
    def test_runtime_dependency_closure_is_explicitly_pinned(self):
        requirements = (ROOT / "scripts/requirements-local-manifest.txt").read_text()
        self.assertIn("--no-binary=dulwich", requirements)
        for pin in (
            "dulwich==0.24.10", "urllib3==2.6.3", "ext4==1.4.1",
            "cachetools==7.1.1", "crcmod==1.7", "typing-extensions==4.15.0",
        ):
            self.assertIn(pin, requirements)

    def test_platform_jobs_select_python_and_install_the_declared_environment(self):
        lines = (ROOT / ".github/workflows/lint.yml").read_text().splitlines()
        jobs = block(lines, "jobs:", 0)
        for name in ("tests", "checkout-fingerprint"):
            job = block(jobs, f"{name}:", 2)
            selected = [
                step for step in steps(job)
                if (step_field(step, "uses") or "").startswith("actions/setup-python@")
            ]
            self.assertEqual(len(selected), 1)
            self.assertEqual(
                mapping(block(selected[0], "with:", 8), 10)["python-version"], '"3.12"'
            )
            body = "\n".join(job)
            self.assertIn('python', body)
            self.assertIn('venv "$RUNNER_TEMP/local-manifest-venv"', body)
            self.assertIn("scripts/requirements-local-manifest.txt", body)
            if name == "checkout-fingerprint":
                for platform in ("ubuntu-latest", "windows-latest"):
                    self.assertIn(platform, body)
                self.assertIn("-p test_local_mvm.py", body)
                self.assertIn('PURE: "1"', body)
            else:
                self.assertIn("apt-get install -y e2fsprogs", body)
                self.assertIn("PURE=1", body)


if __name__ == "__main__":
    unittest.main()
