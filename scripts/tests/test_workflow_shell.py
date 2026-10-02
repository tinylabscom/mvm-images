from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[2]


class WorkflowShellTests(unittest.TestCase):
    def test_image_ci_architecture_normalization_is_valid_shell(self):
        workflow = (ROOT / ".github/workflows/image-owned-ci.yml").read_text()
        command = next(
            line.strip()
            for line in workflow.splitlines()
            if line.strip().startswith("system=$(uname -m | sed ")
        )

        subprocess.run(["bash", "-n"], input=f"{command}\n", text=True, check=True)


if __name__ == "__main__":
    unittest.main()
