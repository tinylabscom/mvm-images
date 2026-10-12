"""Admission unit tests; fixtures are not evidence of a published consumer."""
import copy
import io
import importlib.util
import os
from pathlib import Path
import tarfile
from types import SimpleNamespace
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("released_consumer", ROOT / "scripts/verify-released-consumer.py")
GATE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GATE)
TAG = "v1.2.3"
COMMIT = "a" * 40
RUN = "123"


def report():
    return {
        "schema_version": 1,
        "cli": {"tag": TAG, "commit": COMMIT},
        "workflow": {"repository": GATE.REPOSITORY, "path": GATE.WORKFLOW,
                     "run_id": RUN, "run_attempt": "2", "event": "push",
                     "ref": f"refs/tags/{TAG}"},
        "assets": [{"name": name, "sha256": "b" * 64} for name in sorted(GATE.assets_for(TAG))],
        "boots": [{"host_target": host, "guest_arch": guest, "backend": backend,
                   "result": "success"} for host, guest, backend in sorted(GATE.BOOTS)],
    }


class ReportTests(unittest.TestCase):
    def admit(self, value):
        return GATE.validate_report(value, TAG, COMMIT, RUN, 2)

    def test_complete_reserved_report_and_extra_fields(self):
        value = report()
        value["future_evidence"] = {}
        value["cli"]["future_evidence"] = {}
        self.assertEqual(set(self.admit(value)), GATE.assets_for(TAG))

    def test_no_schema_protocol_or_count_substitute(self):
        for value in ({}, {"schema_version": 3, "base_image_contract": 1},
                      {"schema_version": 1, "required_linux_members": 13}):
            with self.subTest(value=value), self.assertRaises(GATE.Refusal):
                self.admit(value)

    def test_pins_and_workflow_identity_must_match(self):
        changes = [("cli", "tag", "v9.9.9"), ("cli", "commit", "c" * 40),
                   ("workflow", "repository", "other/repo"), ("workflow", "path", "other.yml"),
                   ("workflow", "run_id", "456"), ("workflow", "run_attempt", "1"),
                   ("workflow", "event", "workflow_dispatch"), ("workflow", "ref", "refs/heads/main")]
        for section, field, value in changes:
            with self.subTest(field=field):
                fixture = report()
                fixture[section][field] = value
                with self.assertRaises(GATE.Refusal):
                    self.admit(fixture)

    def test_missing_duplicate_failed_or_wrong_required_boot_is_refused(self):
        fixtures = []
        value = report()
        value["boots"].pop()
        fixtures.append(value)
        value = report()
        value["boots"].append(copy.deepcopy(value["boots"][0]))
        fixtures.append(value)
        value = report()
        value["boots"][0]["result"] = "skipped"
        fixtures.append(value)
        value = report()
        value["boots"][0]["backend"] = "qemu"
        fixtures.append(value)
        for value in fixtures:
            with self.subTest(boots=value["boots"]), self.assertRaises(GATE.Refusal):
                self.admit(value)

    def test_archives_cannot_be_missing_duplicate_or_have_bad_digests(self):
        for kind in ("missing", "duplicate", "extra", "digest"):
            value = report()
            if kind == "missing":
                value["assets"].pop()
            elif kind == "duplicate":
                value["assets"][1] = value["assets"][0]
            elif kind == "extra":
                value["assets"][0]["name"] = "other.tar.gz"
            else:
                value["assets"][0]["sha256"] = "not a digest"
            with self.subTest(kind=kind), self.assertRaises(GATE.Refusal):
                self.admit(value)


class AuthenticationTests(unittest.TestCase):
    def test_report_uses_attestation_bundle_not_raw_blob_signature(self):
        with mock.patch.object(GATE, "command") as command:
            GATE.verify_attestation(Path("installed-boot.json"), TAG, COMMIT,
                                    Path("installed-boot.json.bundle"))
        command.assert_called_once()
        args = command.call_args.args
        self.assertEqual(args[:3], ("gh", "attestation", "verify"))
        self.assertIn("--bundle", args)
        self.assertIn("installed-boot.json.bundle", args)

    def test_installer_skip_download_verifier_overrides_are_removed(self):
        overrides = {"MVM_SKIP_HASH_VERIFY": "1", "MVM_SKIP_CODESIGN": "1",
                     "MVM_RELEASE_BASE_URL": "https://untrusted.invalid",
                     "MVM_COSIGN": "/untrusted/cosign", "GH_HOST": "untrusted.invalid",
                     "GITHUB_API_URL": "https://untrusted.invalid", "GH_TOKEN": "unit-fixture"}
        args = SimpleNamespace(tag=TAG, out=Path("/private/unit-fixture"))
        with mock.patch.dict(os.environ, overrides, clear=True):
            env = GATE.installer_environment(args, "b" * 64)
        for key in overrides:
            if key != "GH_TOKEN":
                self.assertNotIn(key, env)
        self.assertEqual(env["MVM_SKIP_BOOTSTRAP"], "1")
        self.assertEqual(env["MVM_VERSION"], TAG)
        self.assertEqual(env["MVM_TRUSTED_ARCHIVE_SHA256"], "b" * 64)
        self.assertTrue(env["MVM_INSTALL_DIR"].endswith("/installed/bin"))

    def test_cosign_and_attestation_require_exact_tag_commit_workflow_issuer(self):
        with mock.patch.object(GATE, "command") as command:
            GATE.verify_signed(Path("archive"), Path("bundle"), TAG, COMMIT)
        cosign, attest = [call.args for call in command.call_args_list]
        self.assertEqual(cosign[0:2], ("cosign", "verify-blob"))
        self.assertIn(GATE.ISSUER, cosign)
        identity = f"https://github.com/{GATE.REPOSITORY}/{GATE.WORKFLOW}@refs/tags/{TAG}"
        self.assertIn(identity, cosign)
        for required in ("--source-ref", f"refs/tags/{TAG}", "--source-digest", COMMIT,
                         "--signer-workflow", f"{GATE.REPOSITORY}/{GATE.WORKFLOW}",
                         "--cert-identity", identity, "--cert-oidc-issuer", GATE.ISSUER):
            self.assertIn(required, attest)

    def test_checksum_proof_requires_its_own_signature_not_an_assumed_attestation(self):
        with mock.patch.object(GATE, "command") as command:
            GATE.verify_blob(Path("archive.sha256"), Path("archive.sha256.bundle"), TAG)
        command.assert_called_once()
        args = command.call_args.args
        self.assertEqual(args[:2], ("cosign", "verify-blob"))
        self.assertIn("archive.sha256.bundle", args)
        self.assertIn(
            f"https://github.com/{GATE.REPOSITORY}/{GATE.WORKFLOW}@refs/tags/{TAG}",
            args,
        )
        self.assertIn(GATE.ISSUER, args)

    def test_command_failure_is_never_a_success_bypass(self):
        with mock.patch.object(GATE.subprocess, "run",
                               return_value=mock.Mock(returncode=1, stderr=b"not authenticated")):
            with self.assertRaisesRegex(GATE.Refusal, "not authenticated"):
                GATE.command("gh", "api")

    def test_pins_refuse_missing_and_non_release_inputs(self):
        GATE.validate_pins(TAG, COMMIT, RUN)
        for pins in (("", COMMIT, RUN), (TAG, "", RUN), (TAG, COMMIT, ""),
                     ("main", COMMIT, RUN), ("v1.2.3-rc1", COMMIT, RUN)):
            with self.subTest(pins=pins), self.assertRaises(GATE.Refusal):
                GATE.validate_pins(*pins)

    def test_signed_checksums_are_strict_and_unique(self):
        self.assertEqual(GATE.checksum_table("a" * 64 + "  archive.tar.gz\n"),
                         {"archive.tar.gz": "a" * 64})
        for text in ("", "garbage\n", "a" * 64 + "  ../archive\n",
                     ("a" * 64 + "  archive\n") * 2):
            with self.subTest(text=text), self.assertRaises(GATE.Refusal):
                GATE.checksum_table(text)

    def test_missing_published_asset_refuses_before_download(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(GATE, "command") as command:
            with self.assertRaises(GATE.Refusal):
                GATE.release_download([], "missing", Path(tmp))
            command.assert_not_called()

    def test_exact_postpublication_successful_run_is_required(self):
        release = {"tag_name": TAG, "draft": False, "prerelease": False,
                   "published_at": "2026-01-01T00:00:00Z"}
        run = {"id": int(RUN), "head_sha": COMMIT, "head_branch": TAG, "event": "push",
               "path": GATE.WORKFLOW, "status": "completed", "conclusion": "success",
               "repository": {"full_name": GATE.REPOSITORY}}
        job = {"name": GATE.JOB, "status": "completed", "conclusion": "success",
               "started_at": "2026-01-01T00:01:00Z"}
        GATE.validate_run(release, run, [job], TAG, COMMIT, RUN)
        for field, value in (("head_sha", "c" * 40), ("event", "workflow_dispatch"),
                             ("head_branch", "main"), ("conclusion", "failure")):
            modified = {**run, field: value}
            with self.subTest(field=field), self.assertRaises(GATE.Refusal):
                GATE.validate_run(release, modified, [job], TAG, COMMIT, RUN)
        for jobs in ([], [job, job], [{**job, "conclusion": "skipped"}],
                     [{**job, "started_at": "2025-12-31T00:00:00Z"}]):
            with self.subTest(jobs=jobs), self.assertRaises(GATE.Refusal):
                GATE.validate_run(release, run, jobs, TAG, COMMIT, RUN)


class InstallerTests(unittest.TestCase):
    def test_relative_output_executes_the_installer_at_its_real_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            previous = Path.cwd()
            os.chdir(tmp)
            try:
                out = Path("consumer")
                out.mkdir()
                args = SimpleNamespace(tag=TAG, commit=COMMIT, run_id=RUN, out=out)
                cli = "mvmctl-x86_64-unknown-linux-gnu.tar.gz"
                top = cli.removesuffix(".tar.gz")
                runtime = f"mvm-guest-bins-{TAG}.tar.gz"
                installer = f"""#!/bin/sh
set -eu
version="$MVM_INSTALL_LIB_DIR/test"
mkdir -p "$version/guest-runtime" "$MVM_INSTALL_DIR"
printf '%s' 'unit CLI fixture' > "$version/mvmctl"
for suffix in "" .sha256 .bundle .sha256.bundle; do
  cp "../../{runtime}$suffix" "$version/guest-runtime/{runtime}$suffix"
done
ln -s "$version/mvmctl" "$MVM_INSTALL_DIR/mvmctl"
""".encode()
                with tarfile.open(out / cli, "w:gz") as archive:
                    for name, data in (("install.sh", installer), ("mvmctl", b"unit CLI fixture")):
                        member = tarfile.TarInfo(f"{top}/{name}")
                        member.size = len(data)
                        archive.addfile(member, io.BytesIO(data))
                for suffix in ("", ".sha256", ".bundle", ".sha256.bundle"):
                    (out / (runtime + suffix)).write_bytes(f"fixture{suffix}".encode())
                with mock.patch.object(GATE.sys, "platform", "linux"), \
                     mock.patch.object(GATE.os, "uname", return_value=SimpleNamespace(machine="x86_64")), \
                     mock.patch("builtins.print"):
                    GATE.install(args, {cli: "b" * 64})
                self.assertTrue((out / "mvmctl").is_file())
            finally:
                os.chdir(previous)

    def test_unsafe_archive_is_refused_before_executing_installer(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = SimpleNamespace(tag=TAG, out=Path(tmp))
            archive = args.out / "mvmctl-x86_64-unknown-linux-gnu.tar.gz"
            with tarfile.open(archive, "w:gz") as stream:
                member = tarfile.TarInfo("../install.sh")
                member.size = 1
                stream.addfile(member, io.BytesIO(b"x"))
            with mock.patch.object(GATE.sys, "platform", "linux"), \
                 mock.patch.object(GATE.os, "uname", return_value=SimpleNamespace(machine="x86_64")), \
                 mock.patch.object(GATE.subprocess, "run") as execute:
                with self.assertRaisesRegex(GATE.Refusal, "unsafe"):
                    GATE.install(args, {})
                execute.assert_not_called()

    def test_installed_binary_and_runtime_proofs_must_match_authenticated_bytes(self):
        for corrupt in (None, "binary", ".sha256", ".bundle", ".sha256.bundle"):
            with self.subTest(corrupt=corrupt), tempfile.TemporaryDirectory() as tmp:
                args = SimpleNamespace(tag=TAG, commit=COMMIT, run_id=RUN, out=Path(tmp))
                cli = "mvmctl-x86_64-unknown-linux-gnu.tar.gz"
                top = cli.removesuffix(".tar.gz")
                with tarfile.open(args.out / cli, "w:gz") as stream:
                    for name, data in (("install.sh", b"# unit fixture, never executed\n"),
                                       ("mvmctl", b"unit CLI fixture")):
                        member = tarfile.TarInfo(f"{top}/{name}")
                        member.size = len(data)
                        stream.addfile(member, io.BytesIO(data))
                runtime = f"mvm-guest-bins-{TAG}.tar.gz"
                for suffix in ("", ".sha256", ".bundle", ".sha256.bundle"):
                    (args.out / (runtime + suffix)).write_bytes(f"fixture{suffix}".encode())

                def fake_installer(*_args, **_kwargs):
                    installed = args.out / "installed"
                    version = installed / "lib/mvm/releases" / TAG
                    (version / "guest-runtime").mkdir(parents=True)
                    binary = version / "mvmctl"
                    binary.write_bytes(b"corrupt" if corrupt == "binary" else b"unit CLI fixture")
                    (installed / "bin").mkdir()
                    (installed / "bin/mvmctl").symlink_to(binary)
                    for suffix in ("", ".sha256", ".bundle", ".sha256.bundle"):
                        data = (args.out / (runtime + suffix)).read_bytes()
                        (version / "guest-runtime" / (runtime + suffix)).write_bytes(
                            b"corrupt" if corrupt == suffix else data)
                    return SimpleNamespace(returncode=0)

                with mock.patch.object(GATE.sys, "platform", "linux"), \
                     mock.patch.object(GATE.os, "uname", return_value=SimpleNamespace(machine="x86_64")), \
                     mock.patch.object(GATE.subprocess, "run", side_effect=fake_installer), \
                     mock.patch("builtins.print"):
                    if corrupt:
                        with self.assertRaises(GATE.Refusal):
                            GATE.install(args, {cli: "b" * 64})
                        self.assertFalse((args.out / "mvmctl").exists())
                    else:
                        GATE.install(args, {cli: "b" * 64})
                        self.assertTrue((args.out / "mvmctl").is_file())


if __name__ == "__main__":
    unittest.main()
