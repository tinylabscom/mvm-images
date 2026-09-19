"""Tests for building against a local mvm checkout.

Run with: python3 -m unittest discover -s scripts/tests -v

Covers `emit-local-manifest.py` and the `--mvm-checkout` argument handling of
`build-host-binaries.sh`. Every checkout here is a throwaway git repository
under a temporary directory; nothing touches this repository's own tree.
"""

from __future__ import annotations

import importlib.util
import json
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parent.parent
REPO = SCRIPTS.parent

_spec = importlib.util.spec_from_file_location("emit_local_manifest", SCRIPTS / "emit-local-manifest.py")
eml = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(eml)

# A fixed identity for every commit these tests make, so commit ids and the
# dirty fingerprint are the same on every machine.
GIT_ENV = {
    "GIT_AUTHOR_NAME": "fixture",
    "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
    "GIT_AUTHOR_DATE": "2026-01-01T00:00:00Z",
    "GIT_COMMITTER_NAME": "fixture",
    "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
    "GIT_COMMITTER_DATE": "2026-01-01T00:00:00Z",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
}

# The fingerprint of the tree `dirty_fixture` leaves behind. mvm's
# `crates/mvm-build/src/image_source/tests.rs` pins the same value for the same
# fixture, which is what makes a manifest written here comparable with a
# checkout mvm re-reads.
DIRTY_FIXTURE_FINGERPRINT = "952dbae933d34ca2625e9964a3e919b48271a821117b03daf9a606382f7490f1"


def run_git(root: Path, *args: str) -> None:
    env = {**os.environ, **GIT_ENV}
    subprocess.run(["git", "-C", str(root), *args], env=env, check=True, capture_output=True)


def committed_repo(root: Path, files: dict[str, str]) -> Path:
    root.mkdir(parents=True)
    run_git(root, "init", "-q", "-b", "main")
    for name, body in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
    run_git(root, "add", "-A")
    run_git(root, "commit", "-q", "-m", "fixture")
    return root


def dirty_fixture(root: Path) -> Path:
    """One tracked edit, one untracked file and one untracked link."""
    committed_repo(root, {"a.txt": "tracked\n"})
    (root / "a.txt").write_text("tracked\nchanged\n")
    (root / "u.txt").write_text("untracked\n")
    os.symlink("a.txt", root / "link")
    return root


AGENT_SOURCE = """\
/// The protocol this agent speaks.
pub const PROTOCOL_VERSION: u32 = 3;

/// The oldest one it still accepts.
pub const MIN_SUPPORTED_PROTOCOL_VERSION: u32 = 2;
"""


def mvm_checkout(root: Path) -> Path:
    return committed_repo(
        root,
        {
            "Cargo.toml": "[workspace]\n",
            "Cargo.lock": "version = 4\n",
            "nix/flake.nix": "{ }\n",
            "crates/mvm-build/Cargo.toml": "[package]\n",
            eml.AGENT_PROTOCOL_SOURCE: AGENT_SOURCE,
        },
    )


def images_checkout(root: Path) -> Path:
    files = {marker: f"# {marker}\n" for marker in eml.IMAGES_MARKERS}
    files["kernel/flake.lock"] = '{"nodes": {}}\n'
    return committed_repo(root, files)


class Fixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(os.path.realpath(self._tmp.name))
        self.mvm = mvm_checkout(self.tmp / "mvm")
        self.images = images_checkout(self.tmp / "mvm-images")
        self.built = self.tmp / "built"
        self.built.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def artifact(self, name: str, body: bytes = b"bytes\n") -> Path:
        path = self.built / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
        return path

    def args(self, *artifacts, out=None, capabilities=(), arch="aarch64", contract=1):
        argv = [
            "--mvm-checkout", str(self.mvm),
            "--images-checkout", str(self.images),
            "--arch", arch,
            "--builder-cache-contract", str(contract),
            "--out", str(out or self.tmp / "set"),
        ]
        for role, fmt, path in artifacts:
            argv += ["--artifact", role, fmt, str(path)]
        for role, cap in capabilities:
            argv += ["--capability", role, cap]
        return eml.parse_args(argv)

    def emit(self, *artifacts, **kwargs) -> dict:
        path = eml.emit(self.args(*artifacts, **kwargs))
        return json.loads(path.read_text())

    def refused(self, *artifacts, **kwargs) -> str:
        with self.assertRaises(eml.Refusal) as caught:
            eml.emit(self.args(*artifacts, **kwargs))
        return str(caught.exception)


class Fingerprint(unittest.TestCase):
    def test_a_clean_tree_has_no_fingerprint(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = committed_repo(Path(tmp) / "r", {"a.txt": "tracked\n"})
            self.assertEqual(eml.worktree_state(root), {"state": "clean"})

    def test_the_dirty_fixture_fingerprints_to_the_value_mvm_pins(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = dirty_fixture(Path(tmp) / "r")
            self.assertEqual(
                eml.worktree_state(root),
                {"state": "dirty", "fingerprint": DIRTY_FIXTURE_FINGERPRINT},
            )

    def test_two_dirty_trees_on_one_commit_differ(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = dirty_fixture(Path(tmp) / "r")
            first = eml.worktree_state(root)
            (root / "u.txt").write_text("untracked, edited\n")
            self.assertNotEqual(eml.worktree_state(root), first)

    def test_an_untracked_link_fingerprints_as_the_link(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = dirty_fixture(Path(tmp) / "r")
            first = eml.worktree_state(root)
            (root / "a.txt").write_text("tracked\nchanged\n")  # same bytes
            os.remove(root / "link")
            os.symlink("u.txt", root / "link")
            self.assertNotEqual(eml.worktree_state(root), first)

    def test_redirecting_git_variables_are_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = dirty_fixture(Path(tmp) / "r")
            other = committed_repo(Path(tmp) / "other", {"b.txt": "b\n"})
            with mock.patch.dict(os.environ, {"GIT_DIR": str(other / ".git")}):
                self.assertEqual(
                    eml.worktree_state(root)["fingerprint"], DIRTY_FIXTURE_FINGERPRINT
                )


class Emit(Fixture):
    def test_records_both_checkouts_and_every_artifact(self):
        kernel = self.artifact("vmlinux", b"kernel bytes")
        overlay = self.artifact("overlay.ext4", b"overlay")
        pack = self.artifact("qemu-wasm-smoke-pack.tar.gz", b"pack")
        manifest = self.emit(
            ("workload_kernel", "kernel:image", kernel),
            ("runtime_overlay", "ext4", overlay),
            ("qemu_wasm_smoke_pack", "tar_gz", pack),
            capabilities=[("workload_kernel", "virtio_vsock"), ("workload_kernel", "virtio_blk")],
        )
        self.assertEqual(manifest["schema_version"], 1)
        self.assertEqual(manifest["set_version"], "0.0.0-local")
        local = manifest["producer"]["local_checkouts"]
        self.assertEqual(set(manifest["producer"]), {"local_checkouts"})
        self.assertEqual(local["mvm"], eml.repo_identity(self.mvm))
        self.assertEqual(local["images"], eml.repo_identity(self.images))
        self.assertEqual(local["mvm"]["worktree"], {"state": "clean"})
        self.assertEqual(manifest["mvm_source_commit"], local["mvm"]["commit"])
        self.assertEqual(
            manifest["compatibility"],
            {"guest_agent_protocol": {"min": 2, "max": 3}, "builder_cache_contract": 1},
        )
        self.assertEqual(
            [lock["reference"] for lock in manifest["nix_inputs"]["flake_locks"]],
            ["mvm-images:flake.lock", "mvm-images:kernel/flake.lock"],
        )
        self.assertEqual(
            manifest["nix_inputs"]["flake_locks"][0]["lock_hash"],
            eml.sha256_file(self.images / "flake.lock"),
        )

        kernel_member, overlay_member, pack_member = manifest["members"]
        self.assertEqual(kernel_member["role"], "workload_kernel")
        self.assertEqual(kernel_member["target"], {"arch": "aarch64"})
        self.assertEqual(kernel_member["boot_protocol"], "linux_direct")
        self.assertEqual(kernel_member["required_capabilities"], ["virtio_vsock", "virtio_blk"])
        self.assertEqual(
            kernel_member["artifacts"],
            [{
                "name": "workload-kernel-aarch64-vmlinux",
                "format": {"kernel": "image"},
                "sha256": eml.sha256_file(kernel),
                "size": len(b"kernel bytes"),
            }],
        )
        self.assertNotIn("boot_protocol", overlay_member)
        self.assertEqual(overlay_member["artifacts"][0]["name"], "runtime-overlay-aarch64-overlay.ext4")
        self.assertEqual(pack_member["target"], "arch_independent")
        self.assertEqual(
            pack_member["artifacts"][0]["name"], "qemu-wasm-smoke-pack-qemu-wasm-smoke-pack.tar.gz"
        )

    def test_carries_nothing_that_belongs_to_a_release(self):
        manifest = self.emit(("workload_kernel", "kernel:elf", self.artifact("vmlinux")))
        for field in ("revocation_channel", "supersedes"):
            self.assertNotIn(field, manifest)
        for field in ("repository", "workflow", "release_tag", "source_commit"):
            self.assertNotIn(field, manifest["producer"])
        for member in manifest["members"]:
            self.assertNotIn("pack_hash", member)
            self.assertNotIn("sbom", member)

    def test_sdk_sidecars_name_their_c_library(self):
        manifest = self.emit(
            ("sdk_sidecar_glibc", "ext4", self.artifact("glibc/sdk.ext4")),
            ("sdk_sidecar_musl", "ext4", self.artifact("musl/sdk.ext4")),
        )
        self.assertEqual(
            [m["role"] for m in manifest["members"]],
            [{"sdk_sidecar": "glibc"}, {"sdk_sidecar": "musl"}],
        )

    def test_copies_each_artifact_as_a_regular_file_under_its_name(self):
        real = self.artifact("store/vmlinux", b"linked kernel")
        link = self.built / "result"
        link.mkdir()
        os.symlink(real, link / "vmlinux")
        out = self.tmp / "set"
        manifest = self.emit(("builder_vm", "kernel:elf", link / "vmlinux"), out=out)
        copy = out / manifest["members"][0]["artifacts"][0]["name"]
        self.assertTrue(stat.S_ISREG(os.lstat(copy).st_mode))
        self.assertEqual(copy.read_bytes(), b"linked kernel")
        self.assertEqual(sorted(p.name for p in out.iterdir()), sorted([copy.name, "image-set.json"]))

    def test_records_a_dirty_checkout_by_the_fingerprint(self):
        (self.mvm / "Cargo.lock").write_text("version = 4\n# edited\n")
        manifest = self.emit(("workload_kernel", "kernel:elf", self.artifact("vmlinux")))
        mvm = manifest["producer"]["local_checkouts"]["mvm"]
        self.assertEqual(mvm["worktree"], eml.worktree_state(self.mvm))
        self.assertEqual(mvm["worktree"]["state"], "dirty")

    def test_honours_source_date_epoch(self):
        with mock.patch.dict(os.environ, {"SOURCE_DATE_EPOCH": "1767225600"}):
            manifest = self.emit(("workload_kernel", "kernel:elf", self.artifact("vmlinux")))
        self.assertEqual(manifest["issued_at"], "2026-01-01T00:00:00Z")


class Refusals(Fixture):
    def kernel(self):
        return ("workload_kernel", "kernel:elf", self.artifact("vmlinux"))

    def test_an_output_inside_either_checkout(self):
        for root in (self.mvm, self.images):
            self.assertIn("inside the checkout", self.refused(self.kernel(), out=root / "set"))

    def test_an_output_that_already_holds_something(self):
        out = self.tmp / "set"
        out.mkdir()
        (out / "stale").write_text("x")
        self.assertIn("already exists", self.refused(self.kernel(), out=out))

    def test_leaves_nothing_behind_when_refused(self):
        self.refused(self.kernel(), ("no_such_role", "ext4", self.artifact("x")))
        self.assertEqual(sorted(p.name for p in self.tmp.iterdir()), ["built", "mvm", "mvm-images"])

    def test_a_subdirectory_of_a_checkout(self):
        argv = self.args(self.kernel())
        argv.mvm_checkout = str(self.mvm / "nix")
        with self.assertRaises(eml.Refusal) as caught:
            eml.emit(argv)
        self.assertIn("not the root of its git checkout", str(caught.exception))

    def test_a_directory_that_is_not_a_git_checkout(self):
        plain = self.tmp / "plain"
        plain.mkdir()
        argv = self.args(self.kernel())
        argv.mvm_checkout = str(plain)
        with self.assertRaises(eml.Refusal):
            eml.emit(argv)

    def test_a_checkout_that_is_not_mvm(self):
        argv = self.args(self.kernel())
        argv.mvm_checkout = str(self.images)
        with self.assertRaises(eml.Refusal) as caught:
            eml.emit(argv)
        self.assertIn("not an mvm checkout", str(caught.exception))

    def test_a_symlinked_marker(self):
        target = self.tmp / "elsewhere.nix"
        target.write_text("{ }\n")
        os.remove(self.images / "kernel/flake.nix")
        os.symlink(target, self.images / "kernel/flake.nix")
        self.assertIn("no regular file kernel/flake.nix", self.refused(self.kernel()))

    def test_a_checkout_named_through_a_link_resolves_to_its_root(self):
        link = self.tmp / "mvm-link"
        os.symlink(self.mvm, link)
        argv = self.args(self.kernel())
        argv.mvm_checkout = str(link)
        manifest = json.loads(eml.emit(argv).read_text())
        self.assertEqual(
            manifest["producer"]["local_checkouts"]["mvm"], eml.repo_identity(self.mvm)
        )

    def test_unknown_roles_formats_and_capabilities(self):
        f = self.artifact("f")
        self.assertIn("unknown role", self.refused(("workload", "ext4", f)))
        self.assertIn("unknown artifact format", self.refused(("workload_rootfs", "squashfs", f)))
        self.assertIn("unknown artifact format", self.refused(("workload_kernel", "kernel:bzimage", f)))
        self.assertIn(
            "unknown capability",
            self.refused(("workload_rootfs", "ext4", f), capabilities=[("workload_rootfs", "gpu")]),
        )
        self.assertIn(
            "has no --artifact",
            self.refused(("workload_rootfs", "ext4", f), capabilities=[("builder_vm", "virtio_blk")]),
        )

    def test_two_artifacts_with_one_name(self):
        a = self.artifact("a/vmlinux")
        b = self.artifact("b/vmlinux")
        self.assertIn(
            "both be named",
            self.refused(("workload_kernel", "kernel:elf", a), ("workload_kernel", "kernel:elf", b)),
        )

    def test_an_empty_or_missing_or_non_file_artifact(self):
        self.assertIn("zero-size", self.refused(("workload_rootfs", "ext4", self.artifact("e", b""))))
        self.assertIn("no such file", self.refused(("workload_rootfs", "ext4", self.built / "absent")))
        self.assertIn("not a regular file", self.refused(("workload_rootfs", "ext4", self.built)))

    def test_no_artifacts_or_a_bad_cache_contract(self):
        self.assertIn("no --artifact", self.refused())
        self.assertIn("positive integer", self.refused(self.kernel(), contract=0))

    def test_an_agent_source_without_one_protocol_range(self):
        (self.mvm / eml.AGENT_PROTOCOL_SOURCE).write_text("pub const PROTOCOL_VERSION: u32 = 3;\n")
        run_git(self.mvm, "commit", "-qam", "drop the minimum")
        self.assertIn("MIN_SUPPORTED_PROTOCOL_VERSION", self.refused(self.kernel()))

    def test_a_checkout_that_changes_while_recording(self):
        real = eml.repo_identity
        calls = {"n": 0}

        def drifting(root):
            calls["n"] += 1
            identity = real(root)
            if calls["n"] > 2 and root == self.mvm:
                identity = {**identity, "worktree": {"state": "dirty", "fingerprint": "0" * 64}}
            return identity

        with mock.patch.object(eml, "repo_identity", drifting):
            self.assertIn("changed while the set was being recorded", self.refused(self.kernel()))
        self.assertFalse((self.tmp / "set").exists())


class BuildHostBinariesCheckout(Fixture):
    """`--mvm-checkout` is validated before any toolchain is looked at."""

    def run_script(self, *argv: str):
        return subprocess.run(
            ["bash", str(SCRIPTS / "build-host-binaries.sh"), *argv],
            capture_output=True,
            text=True,
            check=False,
        )

    def test_refuses_a_path_that_is_not_an_mvm_checkout_root(self):
        for given, reason in (
            (self.tmp / "absent", "not a directory"),
            (self.mvm / "nix", "not the root of its git checkout"),
            (self.images, "not an mvm checkout"),
        ):
            result = self.run_script("--mvm-checkout", str(given), "aarch64")
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertIn(reason, result.stderr)

    def test_refuses_a_work_dir_alongside_a_checkout(self):
        result = self.run_script("--mvm-checkout", str(self.mvm), "aarch64", str(self.tmp / "w"))
        self.assertEqual(result.returncode, 2)
        self.assertIn("takes no work-dir", result.stderr)

    def test_builds_from_the_canonical_root_and_says_so(self):
        # The fixture declares no toolchain, so the script stops right after
        # naming the source it would build.
        link = self.tmp / "mvm-link"
        os.symlink(self.mvm, link)
        (self.mvm / "Cargo.lock").write_text("version = 4\n# edited\n")
        result = self.run_script("--mvm-checkout", str(link), "x86_64")
        self.assertEqual(result.returncode, 1)
        head = subprocess.run(
            ["git", "-C", str(self.mvm), "rev-parse", "HEAD"], capture_output=True, text=True
        ).stdout.strip()
        self.assertIn(
            f"building host binaries from the local mvm checkout {self.mvm} at {head} "
            "(with uncommitted changes)",
            result.stderr,
        )
        self.assertIn(f"of the local mvm checkout {self.mvm}", result.stderr)


if __name__ == "__main__":
    unittest.main()
