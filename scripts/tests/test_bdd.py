import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("run_bdd", ROOT / "scripts/run-bdd.py")
assert SPEC is not None and SPEC.loader is not None
run_bdd = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(run_bdd)


class GherkinParserTests(unittest.TestCase):
    def test_every_feature_step_has_an_implementation(self):
        scenarios = []
        for path in sorted((ROOT / "features").glob("*.feature")):
            scenarios.extend(run_bdd.parse_feature(path))
        self.assertGreater(len(scenarios), 0)
        for scenario in scenarios:
            for step in scenario.steps:
                self.assertIn(step, run_bdd.STEPS, f"undefined step in {scenario.name}")


if __name__ == "__main__":
    unittest.main()
