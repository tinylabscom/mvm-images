from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]


class CanonicalReproducibilityTests(unittest.TestCase):
    def test_rebuilds_every_canonical_base_role(self):
        script = (ROOT / "scripts/check-reproducible.sh").read_text()
        workflow = (ROOT / ".github/workflows/reproduce.yml").read_text()

        for role in ("builder-vm", "default-tenant", "rootless-tenant"):
            self.assertIn(role, script)
            self.assertIn(role, workflow)
        self.assertIn("--rebuild", script)

    def test_does_not_compare_with_retired_mvm_image_recipes(self):
        script = (ROOT / "scripts/check-reproducible.sh").read_text()
        for forbidden in (
            "compare-same-commit",
            "mvm_source_dir",
            "mvm_assert_tree_matches_lock",
            "git+file://",
            "nix/images/${role}",
        ):
            self.assertNotIn(forbidden, script)


if __name__ == "__main__":
    unittest.main()
