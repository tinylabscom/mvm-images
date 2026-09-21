#!/usr/bin/env python3
"""Run this repository's small, dependency-free Gherkin contract suite."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys
from typing import NamedTuple


ROOT = Path(__file__).resolve().parents[1]
FEATURE_DIR = ROOT / "features"


class Scenario(NamedTuple):
    feature: str
    name: str
    steps: list[str]


def parse_feature(path: Path) -> list[Scenario]:
    feature = ""
    scenarios: list[Scenario] = []
    current: Scenario | None = None
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("Feature:"):
            feature = line.removeprefix("Feature:").strip()
        elif line.startswith("Scenario:"):
            if not feature:
                raise ValueError(f"{path}:{number}: Scenario before Feature")
            current = Scenario(feature, line.removeprefix("Scenario:").strip(), [])
            scenarios.append(current)
        elif line.startswith(("Given ", "When ", "Then ", "And ", "But ")):
            if current is None:
                raise ValueError(f"{path}:{number}: step before Scenario")
            current.steps.append(line.split(" ", 1)[1])
        elif current is None:
            # A free-form feature description.
            continue
        else:
            raise ValueError(f"{path}:{number}: unsupported Gherkin: {line}")
    if not scenarios:
        raise ValueError(f"{path}: no scenarios")
    return scenarios


def _run_check(section: str) -> None:
    subprocess.run(
        [sys.executable, str(ROOT / "scripts/check_no_network_devices.py"), section],
        cwd=ROOT,
        check=True,
    )


def _check_entry_points() -> None:
    paths = [
        ROOT / "scripts/e2e_boot.py",
        ROOT / "scripts/run-qemu-wasm-smoke-chromium.py",
        ROOT / "scripts/run-qemu-wasm-smoke-suite.py",
    ]
    forbidden = ("mvmctl", '"../mvm"', "bin/dev")
    violations = []
    for path in paths:
        text = path.read_text(encoding="utf-8")
        for token in forbidden:
            if token in text:
                violations.append(f"{path.relative_to(ROOT)}: {token}")
    if violations:
        raise AssertionError("standalone test invokes mvm: " + ", ".join(violations))


STEPS = {
    "the mvm-images source tree": lambda: None,
    "I inspect the kernel device contract": lambda: _run_check("kernel"),
    "I inspect the QEMU-Wasm device contract": lambda: _run_check("qemu-wasm"),
    "I inspect the standalone E2E plans": lambda: _run_check("e2e"),
    "I inspect the standalone test entry points": _check_entry_points,
    "the contract passes": lambda: None,
}


def main() -> int:
    scenarios = []
    for path in sorted(FEATURE_DIR.glob("*.feature")):
        scenarios.extend(parse_feature(path))
    failed = 0
    for scenario in scenarios:
        print(f"Feature: {scenario.feature}")
        print(f"  Scenario: {scenario.name}")
        try:
            for step in scenario.steps:
                implementation = STEPS.get(step)
                if implementation is None:
                    raise AssertionError(f"undefined step: {step}")
                implementation()
                print(f"    PASS {step}")
        except (AssertionError, subprocess.CalledProcessError, RuntimeError) as exc:
            failed += 1
            print(f"    FAIL {exc}")
    print(f"\n{len(scenarios) - failed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
