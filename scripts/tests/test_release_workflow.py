"""Structure of the release workflow's signing and provenance steps.

The workflow only runs on a protected release tag, so a broken attestation
step would first show up while cutting a release. These tests pin the parts
that make the attestation mean something: it is pinned, it attests exactly the
subjects the assembler derived from the set it built, it runs only after the
set is signed and verified, and its permissions are granted to the publishing
job alone. The parser is deliberately small and assumes this repository's
two-space workflow layout rather than pulling in a YAML dependency.
"""

from __future__ import annotations

from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/release.yml"
ATTEST_ACTION = "actions/attest-build-provenance"
SUBJECTS_OUTPUT = "${{ steps.assemble.outputs.subjects }}"


def indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def block(lines: list[str], header: str, depth: int) -> list[str]:
    """The lines nested under the first `header` found at `depth` spaces."""
    for start, line in enumerate(lines):
        if indent(line) == depth and line.strip() == header:
            body = []
            for nested in lines[start + 1 :]:
                if nested.strip() and indent(nested) <= depth:
                    break
                body.append(nested)
            return body
    raise AssertionError(f"no `{header}` at indent {depth} in {WORKFLOW.name}")


def mapping(lines: list[str], depth: int) -> dict[str, str]:
    """The scalar `key: value` pairs directly at `depth`, comments dropped."""
    result = {}
    for line in lines:
        if indent(line) != depth or line.lstrip().startswith("#"):
            continue
        key, _, value = line.strip().partition(":")
        result[key] = value.strip()
    return result


def steps(job: list[str]) -> list[list[str]]:
    """Each step of a job as its own list of lines, `- ` marker included."""
    found: list[list[str]] = []
    for line in block(job, "steps:", 4):
        if indent(line) == 6 and line.lstrip().startswith("- "):
            found.append([line])
        elif found and (not line.strip() or indent(line) > 6):
            found[-1].append(line)
    return found


def step_field(step: list[str], key: str) -> str | None:
    for line in step:
        match = re.match(rf"^\s+(?:- )?{re.escape(key)}:\s*(.*?)\s*$", line)
        if match and indent(line.replace("- ", "  ", 1)) == 8:
            return match.group(1)
    return None


class ReleaseWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.lines = WORKFLOW.read_text().splitlines()
        self.jobs = block(self.lines, "jobs:", 0)
        self.publish = block(self.jobs, "publish:", 2)
        self.steps = steps(self.publish)
        self.names = [step_field(step, "name") for step in self.steps]

    def step_named(self, prefix: str) -> list[str]:
        matches = [
            step for step, name in zip(self.steps, self.names) if name and name.startswith(prefix)
        ]
        self.assertEqual(len(matches), 1, f"expected one publish step named {prefix!r}")
        return matches[0]

    def position(self, prefix: str) -> int:
        return self.steps.index(self.step_named(prefix))

    def test_attestation_permissions_belong_to_the_publish_job_only(self):
        self.assertEqual(mapping(block(self.lines, "permissions:", 0), 2), {"contents": "read"})
        self.assertEqual(
            mapping(block(self.publish, "permissions:", 4), 6),
            {"contents": "write", "id-token": "write", "attestations": "write"},
        )
        build = block(self.jobs, "build:", 2)
        self.assertEqual(mapping(block(build, "permissions:", 4), 6), {"contents": "read"})

    def test_the_attestation_step_is_pinned_and_takes_the_assembled_subjects(self):
        attesting = [
            step for step in self.steps if (step_field(step, "uses") or "").startswith(ATTEST_ACTION)
        ]
        self.assertEqual(len(attesting), 1)
        uses = step_field(attesting[0], "uses")
        self.assertRegex(uses, rf"^{re.escape(ATTEST_ACTION)}@[0-9a-f]{{40}} # v\d")
        self.assertEqual(
            mapping(block(attesting[0], "with:", 8), 10),
            {"subject-checksums": SUBJECTS_OUTPUT},
            "the attestation must take the assembler's subjects and nothing else",
        )
        self.assertEqual(
            "".join(self.lines).count(ATTEST_ACTION), 1, "the set is attested in one place"
        )

    def test_the_subjects_come_from_the_assembler(self):
        assemble = self.step_named("Assemble the complete image set")
        self.assertEqual(step_field(assemble, "id"), "assemble")
        script = "\n".join(assemble)
        self.assertIn("scripts/assemble-release.py", script)
        self.assertIn('--subjects "$subjects"', script)
        self.assertIn("printf 'subjects=%s\\n' \"$subjects\" >> \"$GITHUB_OUTPUT\"", script)
        self.assertRegex(script, r'subjects="\$RUNNER_TEMP/[^"/]+"')

        check = self.step_named("Check the provenance subjects")
        self.assertEqual(mapping(block(check, "env:", 8), 10), {"SUBJECTS": SUBJECTS_OUTPUT})
        script = "\n".join(check)
        self.assertIn('test -s "$SUBJECTS"', script)
        self.assertIn('(cd release-assets && sha256sum --check --strict', script)

    def test_only_a_signed_and_verified_set_is_attested_and_published(self):
        order = [
            self.position("Assemble the complete image set"),
            self.position("Sign every member pack and the image-set manifest"),
            self.position("Verify the signed complete set with pinned mvm"),
            self.position("Check the provenance subjects"),
            self.position("Attest build provenance"),
            self.position("Publish the immutable GitHub Release"),
        ]
        self.assertEqual(order, sorted(order))

    def test_signing_and_complete_set_verification_are_unchanged(self):
        sign = "\n".join(self.step_named("Sign every member pack"))
        self.assertIn("release-assets/pack-*.json release-assets/image-set.json", sign)
        self.assertIn("cosign sign-blob", sign)
        verify = "\n".join(self.step_named("Verify the signed complete set"))
        self.assertIn("image boot verify --json --require-complete", verify)


if __name__ == "__main__":
    unittest.main()
