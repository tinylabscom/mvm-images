"""Tests for describing locally built Linux images without an mvm checkout.

Run with: python3 -m unittest discover -s scripts/tests -v

Covers `emit-local-manifest.py` against throwaway `mvm-images`
checkouts. Every checkout here is a throwaway git repository under a
temporary directory; nothing touches this repository's own tree.
"""

from __future__ import annotations

import importlib.util
import hashlib
import errno
import json
import os
import stat
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from types import SimpleNamespace

from dulwich.index import ConflictedIndexEntry, IndexExtension, index_entry_from_stat, write_index_dict
from dulwich.objects import Blob, Commit
from dulwich.repo import Repo

from ext4_fixture import contract_rootfs_bytes

SCRIPTS = Path(__file__).resolve().parent.parent
REPO = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))

_spec = importlib.util.spec_from_file_location("emit_local_manifest", SCRIPTS / "emit-local-manifest.py")
eml = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = eml
_spec.loader.exec_module(eml)

# The fingerprint of the tree `dirty_fixture` leaves behind. mvm's
# `crates/mvm-build/src/image_source/tests.rs` pins the same value for the same
# fixture, which is what makes a manifest written here comparable with a
# checkout mvm re-reads.
DIRTY_FIXTURE_FINGERPRINT = "62287e21e3b30dc2978c2e7e05f65e9770e6a641048161493425b0799c734bae"


def commit_all(root: Path, message: bytes = b"fixture\n") -> None:
    """Create objects/index/commit directly; never enter hooks/filter pipelines."""
    with Repo(str(root)) as repo:
        index = repo.open_index()
        index.clear()
        for base, directories, files in os.walk(os.fsencode(root)):
            directories[:] = [d for d in directories if d != b".git"]
            for name in files:
                path = os.path.join(base, name)
                relative = os.path.relpath(path, os.fsencode(root)).replace(os.fsencode(os.sep), b"/")
                mode = os.lstat(path)
                blob = Blob.from_string(os.readlink(path) if stat.S_ISLNK(mode.st_mode)
                                        else Path(os.fsdecode(path)).read_bytes())
                repo.object_store.add_object(blob)
                index[relative] = index_entry_from_stat(mode, blob.id)
        index.write()
        commit = Commit()
        commit.tree = index.commit(repo.object_store)
        try:
            commit.parents = [repo.head()]
        except KeyError:
            commit.parents = []
        commit.author = commit.committer = b"mvm-test <mvm-test@example.invalid>"
        commit.author_time = commit.commit_time = 1700000000
        commit.author_timezone = commit.commit_timezone = 0
        commit.message = message
        repo.object_store.add_object(commit)
        repo.refs[b"HEAD"] = commit.id


def committed_repo(root: Path, files: dict[str, str]) -> Path:
    root.mkdir(parents=True)
    with Repo.init(str(root)) as repo:
        repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/main")
    for name, body in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body.encode())
    commit_all(root)
    return root


def dirty_fixture(root: Path) -> Path:
    """One tracked edit, one untracked file and one untracked link."""
    committed_repo(root, {"a.txt": "tracked\n"})
    (root / "a.txt").write_bytes(b"tracked\nchanged\n")
    (root / "u.txt").write_bytes(b"untracked\n")
    os.symlink("a.txt", root / "link")
    return root


def images_checkout(root: Path, boot_abi: str = "1\n") -> Path:
    files = {marker: f"# {marker}\n" for marker in eml.IMAGES_MARKERS}
    files["kernel/flake.lock"] = '{"nodes": {}}\n'
    files["images/builder-vm/boot-abi.nix"] = boot_abi
    files["images/base-image-contract.nix"] = "1\n"
    return committed_repo(root, files)


class Fixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(os.path.realpath(self._tmp.name))
        self.env = mock.patch.dict(os.environ, {
            "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_ATTR_NOSYSTEM": "1", "XDG_CONFIG_HOME": str(self.tmp / "xdg"),
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        self.mvm = committed_repo(self.tmp / "other", {"unrelated.txt": "not an image checkout\n"})
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
    def setUp(self):
        # Fixtures and observation must not depend on the runner's home config.
        self.home = tempfile.TemporaryDirectory()
        self.addCleanup(self.home.cleanup)
        self.env = mock.patch.dict(os.environ, {
            "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_ATTR_NOSYSTEM": "1", "XDG_CONFIG_HOME": self.home.name,
        })
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_autocrlf_accepts_all_git_boolean_spellings_and_input(self):
        for value in (b"true", b"YES", b"on", b"1", b"01", b"0x1", b"-1", b"1k", b"INPUT"):
            with self.subTest(value=value), tempfile.TemporaryDirectory() as tmp:
                root = committed_repo(Path(tmp) / "r", {"a": "base\n"})
                with (root / ".git/config").open("ab") as stream:
                    stream.write(b"\n[core]\nautocrlf=" + value + b"\n")
                (root / "a").write_bytes(b"base\r\n")
                self.assertEqual(eml.worktree_state(root), {"state": "clean"})

    def test_autocrlf_absent_empty_false_and_valueless_are_distinct(self):
        for line, clean in (
            (b"", False), (b"autocrlf=\n", False), (b"autocrlf=no\n", False),
            (b"autocrlf=off\n", False), (b"autocrlf=0\n", False),
            (b"autocrlf\n", True),
        ):
            with self.subTest(line=line), tempfile.TemporaryDirectory() as tmp:
                root = committed_repo(Path(tmp) / "r", {"a": "base\n"})
                with (root / ".git/config").open("ab") as stream:
                    stream.write(b"\n[core]\n" + line)
                (root / "a").write_bytes(b"base\r\n")
                self.assertEqual(eml.worktree_state(root)["state"], "clean" if clean else "dirty")

    def test_invalid_autocrlf_refuses_even_unchanged_or_empty_checkout(self):
        for files in ({"a": "base\n"}, {}):
            for value in (b"nonsense", b"08", b"2147483648", b"2g", b'" yes"'):
                with self.subTest(files=files, value=value), tempfile.TemporaryDirectory() as tmp:
                    root = committed_repo(Path(tmp) / "r", files)
                    with (root / ".git/config").open("ab") as stream:
                        stream.write(b"\n[core]\nautocrlf=" + value + b"\n")
                    with self.assertRaises(eml.Refusal):
                        eml.worktree_state(root)

    def test_relative_rule_resources_are_worktree_rooted_not_cwd_rooted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = committed_repo(Path(tmp) / "r", {
                "a": "base\n", "rules.ignore": "*.log\n", "rules.attrs": "a text\n",
            })
            with open(root / ".git" / "config", "a") as source:
                source.write("\n[core]\nexcludesfile=rules.ignore\nattributesfile=rules.attrs\n")
            (root / "a").write_bytes(b"base\r\n")
            (root / "noise.log").write_text("ignored")
            outside = Path(tmp) / "outside"
            outside.mkdir()
            (outside / "rules.ignore").write_text("a\n")
            (outside / "rules.attrs").write_text("a -text\n")
            before = Path.cwd()
            try:
                os.chdir(outside)
                first = eml.repo_identity(root)
                self.assertEqual(first["worktree"], {"state": "clean"})
                os.chdir(root)
                self.assertEqual(first, eml.repo_identity(root))
            finally:
                os.chdir(before)

    def test_rule_path_contract_home_xdg_prefix_and_no_host_home_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            home = Path(tmp) / "home"
            prefix = Path(tmp) / "prefix"
            env = {"HOME": str(home), "XDG_CONFIG_HOME": "relative-xdg"}
            with mock.patch.dict(os.environ, env):
                self.assertEqual(eml._rule_path(b"rules.ignore", "ignore", os.fsencode(root), prefix), root / "rules.ignore")
                self.assertEqual(eml._rule_path(b"~/rules.attrs", "attributes", os.fsencode(root), prefix), home / "rules.attrs")
                self.assertEqual(eml._rule_path(b"%(prefix)/rules.attrs", "attributes", os.fsencode(root), prefix), prefix / "rules.attrs")
                self.assertEqual(eml._rule_path(None, "ignore", os.fsencode(root), prefix), root / "relative-xdg/git/ignore")
            with mock.patch.dict(os.environ, {}, clear=True):
                self.assertEqual(eml._rule_path(None, "ignore", os.fsencode(root), None), Path(os.devnull))
                with self.assertRaisesRegex(eml.Refusal, "HOME is required"):
                    eml._rule_path(b"~/missing", "ignore", os.fsencode(root), None)
            with self.assertRaisesRegex(eml.Refusal, "installation prefix"):
                eml._rule_path(b"%(prefix)/missing", "ignore", os.fsencode(root), None)

    def test_prefix_rule_resources_are_read_as_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = committed_repo(Path(tmp) / "r", {"a": "base\n"})
            prefix = Path(tmp) / "prefix"
            prefix.mkdir()
            (prefix / "rules.ignore").write_text("*.log\n")
            (prefix / "rules.attrs").write_text("a text\n")
            with open(root / ".git" / "config", "a") as source:
                source.write("\n[core]\nexcludesfile=%(prefix)/rules.ignore\nattributesfile=%(prefix)/rules.attrs\n")
            (root / "a").write_bytes(b"base\r\n")
            (root / "noise.log").write_text("ignored")
            with mock.patch.object(eml, "_system_context", return_value=eml.GitSystemContext(
                None, None, prefix
            )):
                self.assertEqual(eml.worktree_state(root), {"state": "clean"})

    def test_gitdir_matching_preserves_alias_relative_case_and_raw_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = committed_repo(Path(tmp) / "r", {"a": "base\n"})
            alias = Path(tmp) / "alias"
            os.symlink(root, alias, target_is_directory=True)
            with Repo(str(alias)) as repo:
                self.assertTrue(eml._matches_gitdir(repo, str(alias / ".git"), None, False))
                self.assertTrue(eml._matches_gitdir(repo, str(root.resolve() / ".git"), None, False))
                self.assertTrue(eml._matches_gitdir(repo, "./alias/.git", tmp, False))
                self.assertTrue(eml._matches_gitdir(repo, str(alias / ".git").upper(), None, True))
            if os.name == "posix":
                raw = os.fsdecode(os.path.join(os.fsencode(tmp), b"r-\xff", b".git"))
                other = os.fsdecode(os.path.join(os.fsencode(tmp), b"r-\xfe", b".git"))
                repo = SimpleNamespace(controldir=lambda: raw)
                self.assertTrue(eml._matches_gitdir(repo, raw, None, False))
                self.assertFalse(eml._matches_gitdir(repo, other, None, False))

    def test_unsupported_config_includes_fail_closed_in_contributing_sources(self):
        for directive in ('[includeIf "hasconfig:remote.*.url:*"]\npath=not-read\n',
                          '[include]\npath=%(prefix)/not-read\n'):
            with tempfile.TemporaryDirectory() as tmp:
                root = committed_repo(Path(tmp) / "r", {"a": "base\n"})
                child = Path(tmp) / "included"
                child.write_text(directive)
                with open(root / ".git" / "config", "a") as source:
                    source.write(f'\n[include]\npath="{child.as_posix()}"\n')
                with self.assertRaisesRegex(eml.Refusal, "unsupported .*include"):
                    eml.worktree_state(root)

    def test_windows_prefix_is_discovered_from_data_not_execution(self):
        for suffix in ("cmd", "bin", "mingw64/bin", "mingw32/bin", "usr/bin"):
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / suffix).mkdir(parents=True)
                (root / "etc").mkdir()
                (root / suffix / "git.exe").write_bytes(b"not executable code")
                self.assertEqual(eml._windows_prefix_from_path([root / suffix]), root.resolve())

    def test_contextual_config_and_precedence_are_read_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = committed_repo(Path(tmp) / "r", {"a": "base\n"})
            included = Path(tmp) / "included"
            included.write_text("[identity]\ngitdir=true\n")
            branch = Path(tmp) / "branch"
            branch.write_text("[identity]\nbranch=true\n")
            system = Path(tmp) / "system"
            system.write_text(f'[identity]\nchoice=system\n[includeIf "gitdir:{(root / ".git").as_posix()}"]\n'
                              f'path="{included.as_posix()}"\n[includeIf "onbranch:main"]\npath="{branch.as_posix()}"\n')
            global_config = Path(tmp) / "global"
            global_config.write_text("[identity]\nchoice=global\n")
            with open(root / ".git" / "config", "a") as source:
                source.write("\n[identity]\nchoice=local\n")
            before = (root / ".git" / "config").read_bytes()
            with mock.patch.dict(os.environ, {"GIT_CONFIG_SYSTEM": str(system),
                                             "GIT_CONFIG_GLOBAL": str(global_config),
                                             "GIT_CONFIG_NOSYSTEM": "0"}):
                with Repo(str(root)) as repo:
                    config = eml._config_stack(repo)
                    self.assertTrue(config.get_boolean((b"identity",), b"gitdir"))
                    self.assertTrue(config.get_boolean((b"identity",), b"branch"))
                    self.assertEqual(config.get((b"identity",), b"choice"), b"local")
            self.assertEqual((root / ".git" / "config").read_bytes(), before)

    def test_sparse_index_expands_without_hiding_present_edits(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = committed_repo(Path(tmp) / "r", {"a": "base\n", "d/a": "sparse\n"})
            with Repo(str(root)) as repo:
                subtree = repo.object_store[repo.object_store[repo.head()].tree][b"d"][1]
                index = repo.open_index()
                entry = index[b"d/a"]
                del index[b"d/a"]
                entry.mode, entry.sha, entry.extended_flags = 0o040000, subtree, 0x4000
                index[b"d/"] = entry
                # Real sparse indexes carry the mandatory, empty sdir extension.
                # Index.write drops empty extensions, so write the actual format.
                source = eml.BytesIO()
                write_index_dict(source, dict(index.items()), version=3,
                                 extensions=[IndexExtension(b"sdir", b"")])
                body = source.getvalue()
                Path(repo.index_path()).write_bytes(body + hashlib.sha1(body).digest())
            (root / "d/a").unlink()
            self.assertEqual(eml.worktree_state(root), {"state": "clean"})
            (root / "d/a").write_text("visible edit\n")
            self.assertEqual(eml.worktree_state(root)["state"], "dirty")

    def test_sparse_directory_extension_rejects_payload_and_duplicates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = committed_repo(Path(tmp) / "r", {"a": "base\n"})
            index_path = root / ".git/index"
            original = index_path.read_bytes()[:-20]
            for extension in (b"sdir\0\0\0\1x", b"sdir\0\0\0\0sdir\0\0\0\0"):
                body = original + extension
                index_path.write_bytes(body + hashlib.sha1(body).digest())
                with self.subTest(extension=extension):
                    with self.assertRaisesRegex(eml.Refusal, "sparse-directory"):
                        eml.worktree_state(root)

    def test_recursive_submodule_edits_and_cycle_depth_guards(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = committed_repo(Path(tmp) / "r", {"a": "base\n"})
            sub = committed_repo(root / "s", {"a": "submodule\n"})
            with Repo(str(sub)) as child, Repo(str(root)) as repo:
                index = repo.open_index()
                entry = index_entry_from_stat(os.lstat(root / "a"), child.head())
                entry.mode = 0o160000
                index[b"s"] = entry
                index.write()
            before = eml.worktree_state(root)
            (sub / "u").write_text("recursive untracked\n")
            self.assertNotEqual(before, eml.worktree_state(root))
            with self.assertRaisesRegex(eml.Refusal, "cycle"):
                eml._observe(root, {root.resolve()})
            with self.assertRaisesRegex(eml.Refusal, "depth"):
                eml._observe(root, {Path(f"depth-{i}") for i in range(64)})

    def test_split_index_hydrates_shared_entries_and_replacements(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = dirty_fixture(Path(tmp) / "r")
            first = eml.worktree_state(root)
            index_path = root / ".git" / "index"
            shared = index_path.read_bytes()
            checksum = shared[-20:]
            shared_path = root / ".git" / ("sharedindex." + checksum.hex())
            shared_path.write_bytes(shared)
            empty = struct.pack(">IIQI", 0, 1, 0, 0)

            def split(entries, replacement):
                source = eml.BytesIO()
                extension = IndexExtension(b"link", checksum + empty + replacement)
                write_index_dict(source, entries, version=2, extensions=[extension])
                body = source.getvalue()
                index_path.write_bytes(body + hashlib.sha1(body).digest())

            split({}, empty)
            self.assertEqual(first, eml.worktree_state(root))
            with Repo(str(root)) as repo:
                from dulwich.index import Index
                entry = Index(str(shared_path))[b"a.txt"]
                blob = Blob.from_string(b"replacement\n")
                repo.object_store.add_object(blob)
                entry.sha = blob.id
            replacement = struct.pack(">IIQQI", 1, 2, 1 << 33, 1, 0)
            split({b"": entry}, replacement)
            self.assertNotEqual(first, eml.worktree_state(root))
            shared_path.unlink()
            with self.assertRaisesRegex(eml.Refusal, "missing shared index"):
                eml.worktree_state(root)

    def test_split_index_bitmaps_are_bounded_and_reject_malformed_inputs(self):
        for data in (b"", struct.pack(">IIQI", 1, 1, 3, 0),
                     struct.pack(">IIQI", 1, 1, 1 << 33, 0)):
            with self.assertRaises(eml.Refusal):
                eml._bitmap(data, 0, 0)

    def test_canonical_encoding_vectors(self):
        self.assertEqual(eml.fingerprint_v2(bytes(20), [], [], []),
                         "f05485947d595bce2f26829620e3dfcdd28c7248b6dc0d20681c1ad2699b1ecb")
        self.assertEqual(eml.fingerprint_v2(bytes(20), [], [], [(b"\xff", 0o100644, b"\0\xff\n")]),
                         "06e6284c46206320bd0679940642175026ef97888a63afd2e166c5119ad88787")
        self.assertEqual(eml.fingerprint_v2(
            b"\x11" * 20, [(b"\xff", 0o100755, 2, 7, b"\x22" * 20)],
            [(b"\xff", 0, b"")], [(b"l", 0o120000, b"\xff\n")]),
            "7ff6f04e29602de8984dde4663b9b41edd4b441d37c66e74d0c477d0b175171d")
        self.assertEqual(eml.fingerprint_v2(
            b"\x11" * 20, [(b"s", 0o160000, 0, 0, b"\x22" * 20)],
            [(b"s", 0o160000, b"\x22" * 20 + b"\0")], []),
            "e535f6bf8dc4488cf36988106591011f5fdcbe778163423d006fc3bfe62c1cd8")

    def test_staged_edits_and_conflicts_are_not_lost_when_worktree_matches_head(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = committed_repo(Path(tmp) / "r", {"a": "base\n"})
            with Repo(str(root)) as repo:
                index = repo.open_index()
                entry = index[b"a"]
                blob = Blob.from_string(b"staged\n")
                repo.object_store.add_object(blob)
                entry.sha = blob.id
                index[b"a"] = entry
                index.write()
                first = eml.worktree_state(root)
                self.assertEqual(first["state"], "dirty")
                index[b"a"] = ConflictedIndexEntry(this=entry)
                index.write()
                self.assertNotEqual(first, eml.worktree_state(root))

    def test_stat_cache_is_not_a_fingerprint_input_and_flags_do_not_hide_edits(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = dirty_fixture(Path(tmp) / "r")
            first = eml.worktree_state(root)
            with Repo(str(root)) as repo:
                index = repo.open_index()
                entry = index[b"a.txt"]
                entry.ino, entry.size = 9876, 123456
                index[b"a.txt"] = entry
                index.write()
                self.assertEqual(first, eml.worktree_state(root))
                entry.flags |= 0x8000
                entry.extended_flags |= 0x4000
                index[b"a.txt"] = entry
                index.write()
                self.assertEqual(eml.worktree_state(root)["state"], "dirty")

    def test_ignores_and_git_administration_are_not_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = committed_repo(Path(tmp) / "r", {"a": "base\n", ".gitignore": "*.log\n!keep.log\n"})
            (root / "ignored.log").write_text("ignored")
            (root / ".git" / "administration").write_text("not source")
            self.assertEqual(eml.worktree_state(root), {"state": "clean"})
            (root / "keep.log").write_text("included")
            self.assertEqual(eml.worktree_state(root)["state"], "dirty")

    def test_external_filters_are_refused_without_commands(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = committed_repo(Path(tmp) / "r", {"a": "base\n"})
            with open(root / ".git" / "config", "a") as config:
                config.write('\n[filter "run"]\nclean = nonexistent-clean\n'
                             'smudge = nonexistent-smudge\nprocess = nonexistent-process\n'
                             'required = true\n[core]\nhooksPath = nonexistent-hooks\n'
                             'fsmonitor = nonexistent-monitor\n')
            for attribute in ("filter=run", "working-tree-encoding=UTF-16"):
                (root / ".gitattributes").write_text(f"a {attribute}\n")
                with self.assertRaisesRegex(eml.Refusal, "unsupported external filter"):
                    eml.worktree_state(root)

    def test_builtin_text_ident_and_raw_dirty_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = committed_repo(Path(tmp) / "r", {"a": "$Id$\n", ".gitattributes": "a text ident\n"})
            (root / "a").write_bytes(b"$Id: expanded $\r\n")
            self.assertEqual(eml.worktree_state(root), {"state": "clean"})
            (root / "u").write_text("dirty")
            first = eml.worktree_state(root)
            (root / "a").write_bytes(b"$Id: another expansion $\n")
            self.assertNotEqual(first, eml.worktree_state(root))

    @unittest.skipUnless(os.name == "posix", "raw Unix paths and modes")
    def test_raw_paths_modes_links_and_deletions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = committed_repo(Path(tmp) / "r", {"a": "base\n"})
            path = os.path.join(os.fsencode(root), b"x\xff\n")
            try:
                with open(path, "wb") as source:
                    source.write(b"\0\xff\n")
            except OSError as exc:
                if exc.errno != errno.EILSEQ:
                    raise
                self.skipTest(f"filesystem rejects raw-byte names with EILSEQ: {exc}")
            first = eml.worktree_state(root)
            os.chmod(path, 0o755)
            self.assertNotEqual(first, eml.worktree_state(root))
            os.unlink(path)
            try:
                os.symlink(b"target\xff", path)
            except OSError as exc:
                if exc.errno != errno.EILSEQ:
                    raise
                self.skipTest(f"filesystem rejects raw-byte link targets with EILSEQ: {exc}")
            link = eml.worktree_state(root)
            (root / "a").unlink()
            self.assertNotEqual(link, eml.worktree_state(root))

    @unittest.skipUnless(os.name == "posix", "Unix executable-bit filesystem semantics")
    def test_portable_names_still_witness_executable_mode_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = committed_repo(Path(tmp) / "r", {"a": "base\n"})
            (root / "u").write_bytes(b"untracked\n")
            first = eml.worktree_state(root)
            os.chmod(root / "u", 0o755)
            self.assertNotEqual(first, eml.worktree_state(root))
            os.chmod(root / "a", 0o755)
            (root / "u").unlink()
            self.assertEqual(eml.worktree_state(root)["state"], "dirty")

    def test_portable_link_targets_and_tracked_deletions_are_observed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = dirty_fixture(Path(tmp) / "r")
            first = eml.worktree_state(root)
            (root / "a.txt").unlink()
            self.assertNotEqual(first, eml.worktree_state(root))
            # The dangling untracked link is encoded as a link, never followed.
            self.assertEqual(eml._file(os.fsencode(root), b"link"),
                             (b"link", 0o120000, b"a.txt"))

    def test_linked_worktree_reads_its_own_head_and_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = committed_repo(Path(tmp) / "r", {"a": "base\n"})
            linked = Path(tmp) / "linked"
            linked.mkdir()
            admin = root / ".git" / "worktrees" / "witness"
            admin.mkdir(parents=True)
            with Repo(str(root)) as repo:
                head = repo.head()
            (admin / "HEAD").write_bytes(head + b"\n")
            (admin / "commondir").write_text("../..\n")
            (admin / "gitdir").write_text(str(linked / ".git") + "\n")
            (linked / ".git").write_text(f"gitdir: {admin}\n")
            (admin / "index").write_bytes((root / ".git" / "index").read_bytes())
            (linked / "a").write_bytes(b"base\n")
            self.assertEqual(eml.worktree_state(linked), {"state": "clean"})
            (linked / "a").write_text("linked edit\n")
            self.assertEqual(eml.worktree_state(linked)["state"], "dirty")
            self.assertEqual(eml.worktree_state(root), {"state": "clean"})

    def test_uninitialized_gitlink_and_malformed_initialized_submodule(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = committed_repo(Path(tmp) / "r", {"a": "base\n"})
            with Repo(str(root)) as repo:
                index = repo.open_index()
                entry = index_entry_from_stat(os.lstat(root / "a"), repo.head())
                entry.mode = 0o160000
                index[b"s"] = entry
                index.write()
            self.assertEqual(eml.worktree_state(root)["state"], "dirty")
            (root / "s").mkdir()
            (root / "s" / ".git").write_text("malformed\n")
            with self.assertRaises(eml.Refusal):
                eml.worktree_state(root)

    def test_unsafe_paths_are_refused(self):
        for name in (b"../outside", b"/absolute", b"a/.git/config", b"a\0b"):
            with self.assertRaises(eml.Refusal):
                eml._safe_path(name)

    def test_malformed_and_unsupported_repository_data_never_reports_clean(self):
        for relative, body in (
            (".git/index", b"malformed index"),
            (".git/config", b"[broken config\n"),
            (".git/HEAD", b"not a ref or object\n"),
            (".git/config", b'[core]\nrepositoryformatversion=1\n[extensions]\nunknown=true\n'),
            (".git/config", b'[core]\nrepositoryformatversion=0\n[includeIf "unknown:x"]\npath=nope\n'),
        ):
            with self.subTest(resource=relative, body=body), tempfile.TemporaryDirectory() as tmp:
                root = committed_repo(Path(tmp) / "r", {"a": "base\n"})
                (root / relative).write_bytes(body)
                with self.assertRaises(eml.Refusal):
                    eml.repo_identity(root)

    def test_inaccessible_repository_data_never_reports_clean(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = committed_repo(Path(tmp) / "r", {"a": "base\n"})
            read = eml._read_optional

            def inaccessible(path):
                if Path(os.fsdecode(path)).name == "index":
                    raise PermissionError("fixture denied index read")
                return read(path)

            with mock.patch.object(eml, "_read_optional", inaccessible):
                with self.assertRaisesRegex(eml.Refusal, "denied index"):
                    eml.repo_identity(root)

    def test_command_scope_config_is_explicitly_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = committed_repo(Path(tmp) / "r", {"a": "base\n"})
            with mock.patch.dict(os.environ, {"GIT_CONFIG_COUNT": "1",
                                             "GIT_CONFIG_KEY_0": "core.autocrlf",
                                             "GIT_CONFIG_VALUE_0": "true"}):
                with self.assertRaisesRegex(eml.Refusal, "command-scope"):
                    eml.repo_identity(root)

    def test_symlinked_repository_rule_resources_are_explicitly_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = committed_repo(Path(tmp) / "r", {"a": "base\n"})
            external = Path(tmp) / "rules"
            external.write_bytes(b"*\n")
            os.symlink(external, root / ".gitignore")
            with self.assertRaisesRegex(eml.Refusal, "non-regular"):
                eml.repo_identity(root)

    def test_a_clean_tree_has_no_fingerprint(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = committed_repo(Path(tmp) / "r", {"a.txt": "tracked\n"})
            self.assertEqual(eml.worktree_state(root), {"state": "clean"})

    def test_the_dirty_fixture_fingerprints_to_the_value_mvm_pins(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = dirty_fixture(Path(tmp) / "r")
            self.assertEqual(eml.repo_identity(root)["commit"],
                             "d40e7ed3d340d03c7ad6910387c60943c0ff3d0d")
            self.assertEqual(
                eml.worktree_state(root),
                {"state": "dirty", "fingerprint_version": 2, "fingerprint": DIRTY_FIXTURE_FINGERPRINT},
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
            (root / "a.txt").write_bytes(b"tracked\nchanged\n")  # same bytes
            os.remove(root / "link")
            os.symlink("u.txt", root / "link")
            self.assertNotEqual(eml.worktree_state(root), first)

    def test_redirecting_git_variables_are_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = dirty_fixture(Path(tmp) / "r")
            other = committed_repo(Path(tmp) / "other", {"b.txt": "b\n"})
            redirects = {name: str(other) for name in (
                "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY",
                "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_COMMON_DIR", "GIT_NAMESPACE")}
            redirects["PATH"] = ""
            with mock.patch.dict(os.environ, redirects):
                self.assertEqual(
                    eml.worktree_state(root)["fingerprint"], DIRTY_FIXTURE_FINGERPRINT
                )


class Emit(Fixture):
    def test_fixtures_identity_and_emit_never_launch_git_or_other_subprocesses(self):
        with mock.patch("subprocess.Popen", side_effect=AssertionError("subprocess forbidden")):
            root = dirty_fixture(self.tmp / "no-git")
            self.assertEqual(eml.worktree_state(root)["fingerprint"], DIRTY_FIXTURE_FINGERPRINT)
            manifest = self.emit(
                ("builder_vm", "kernel:elf", self.artifact("vmlinux")),
                ("default_tenant_workload_rootfs", "ext4",
                 self.artifact("rootfs.ext4", contract_rootfs_bytes())),
            )
            self.assertEqual(manifest["producer"]["local_checkouts"]["images"],
                             eml.repo_identity(self.images))

    def test_partial_set_without_workload_root_retains_expected_contract(self):
        manifest = self.emit(("builder_vm", "kernel:elf", self.artifact("vmlinux")))
        self.assertEqual(manifest["compatibility"]["base_image_contract"], 1)

    def test_invalid_supplied_root_never_falls_back_to_source_contract(self):
        for number, data in enumerate((b"not ext4", contract_rootfs_bytes().replace(b"1\n", b"2\n"))):
            with self.subTest(number=number):
                detail = self.refused(
                    ("default_tenant_workload_rootfs", "ext4",
                     self.artifact(f"bad-{number}.ext4", data)),
                )
                self.assertIn("cannot inspect supplied workload root", detail)
                self.assertFalse((self.tmp / "set").exists())
                self.assertFalse(any(self.tmp.glob(".set.*")))

    def test_mixed_or_uninspectable_workload_members_are_refused(self):
        artifact = self.artifact("rootfs.ext4", contract_rootfs_bytes())
        with mock.patch.object(eml, "read_base_image_contract", side_effect=[1, 2]):
            detail = self.refused(
                ("default_tenant_workload_rootfs", "ext4", artifact),
                ("rootless_tenant_workload_rootfs", "ext4", artifact),
            )
        self.assertIn("inconsistent", detail)
        detail = self.refused(
            ("default_tenant_workload_rootfs", "verity_root_hash", self.artifact("hash")),
        )
        self.assertIn("exactly one ext4", detail)

    def test_staged_root_mutation_refuses_and_preserves_existing_empty_output(self):
        reader = eml.read_base_image_contract
        def mutate(path):
            value = reader(path)
            with path.open("ab") as stream:
                stream.write(b"changed after inspection")
            return value
        out = self.tmp / "empty-output"
        out.mkdir()
        with mock.patch.object(eml, "read_base_image_contract", side_effect=mutate):
            detail = self.refused(
                ("default_tenant_workload_rootfs", "ext4",
                 self.artifact("rootfs.ext4", contract_rootfs_bytes())),
                out=out,
            )
        self.assertIn("artifact changed", detail)
        self.assertTrue(out.is_dir())
        self.assertEqual(list(out.iterdir()), [])
        self.assertFalse(any(self.tmp.glob(".empty-output.*")))

    def test_the_declared_boot_abi_comes_from_the_images_checkout(self):
        self.images = images_checkout(self.tmp / "images-abi-1", boot_abi="1\n")
        manifest = self.emit(("builder_vm", "ext4", self.artifact("rootfs.ext4")))
        self.assertEqual(manifest["compatibility"]["builder_boot_abi"], 1)

    def test_an_images_checkout_without_a_boot_abi_is_refused_by_path(self):
        (self.images / "images" / "builder-vm" / "boot-abi.nix").unlink()
        detail = self.refused(("builder_vm", "ext4", self.artifact("rootfs.ext4")))
        self.assertIn("boot-abi.nix", detail)

    def test_records_only_images_checkout_and_every_artifact(self):
        kernel = self.artifact("vmlinux", b"kernel bytes")
        overlay = self.artifact("overlay.ext4", contract_rootfs_bytes())
        pack = self.artifact("qemu-wasm-smoke-pack.tar.gz", b"pack")
        manifest = self.emit(
            ("default_tenant_workload_kernel", "kernel:image", kernel),
            ("default_tenant_workload_rootfs", "ext4", overlay),
            ("qemu_wasm_smoke_pack", "tar_gz", pack),
            capabilities=[
                ("default_tenant_workload_kernel", "virtio_vsock"),
                ("default_tenant_workload_kernel", "virtio_blk"),
            ],
        )
        self.assertEqual(manifest["schema_version"], 3)
        self.assertEqual(manifest["set_version"], "0.0.0-local")
        local = manifest["producer"]["local_checkouts"]
        self.assertEqual(set(manifest["producer"]), {"local_checkouts"})
        self.assertEqual(set(local), {"images"})
        self.assertEqual(local["images"], eml.repo_identity(self.images))
        self.assertEqual(local["images"]["worktree"], {"state": "clean"})
        self.assertNotIn("mvm_source_commit", manifest)
        self.assertEqual(
            manifest["compatibility"],
            {
                "base_image_contract": 1,
                "builder_cache_contract": 1,
                "builder_boot_abi": 1,
            },
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
        self.assertEqual(
            kernel_member["role"], {"workload_kernel": "default_tenant"}
        )
        self.assertEqual(kernel_member["target"], {"arch": "aarch64"})
        self.assertEqual(kernel_member["boot_protocol"], "linux_direct")
        self.assertEqual(kernel_member["required_capabilities"], ["virtio_vsock", "virtio_blk"])
        self.assertEqual(
            kernel_member["artifacts"],
            [{
                "name": "default-tenant-workload-kernel-aarch64-vmlinux",
                "format": {"kernel": "image"},
                "sha256": eml.sha256_file(kernel),
                "size": len(b"kernel bytes"),
            }],
        )
        self.assertNotIn("boot_protocol", overlay_member)
        self.assertEqual(overlay_member["artifacts"][0]["name"], "default-tenant-workload-rootfs-aarch64-overlay.ext4")
        self.assertEqual(pack_member["target"], "arch_independent")
        self.assertEqual(
            pack_member["artifacts"][0]["name"], "qemu-wasm-smoke-pack-qemu-wasm-smoke-pack.tar.gz"
        )

    def test_carries_nothing_that_belongs_to_a_release(self):
        manifest = self.emit(
            ("default_tenant_workload_kernel", "kernel:elf", self.artifact("vmlinux"))
        )
        for field in ("revocation_channel", "supersedes"):
            self.assertNotIn(field, manifest)
        for field in ("repository", "workflow", "release_tag", "source_commit"):
            self.assertNotIn(field, manifest["producer"])
        for member in manifest["members"]:
            self.assertNotIn("pack_hash", member)
            self.assertNotIn("sbom", member)

    def test_retired_guest_runtime_roles_are_refused(self):
        for role in ("sdk_sidecar_glibc", "sdk_sidecar_musl", "runtime_overlay", "initramfs"):
            with self.subTest(role=role):
                self.assertIn("unknown role", self.refused(
                    (role, "ext4", self.artifact(f"{role}.ext4"))))

    def test_default_and_rootless_workload_pairs_have_distinct_profiled_roles(self):
        artifacts = []
        for prefix in ("default_tenant", "rootless_tenant"):
            artifacts.extend(
                [
                    (
                        f"{prefix}_workload_kernel",
                        "kernel:elf",
                        self.artifact(f"{prefix}/vmlinux"),
                    ),
                    (
                        f"{prefix}_workload_rootfs",
                        "ext4",
                        self.artifact(f"{prefix}/rootfs.ext4", contract_rootfs_bytes()),
                    ),
                ]
            )

        manifest = self.emit(*artifacts)

        self.assertEqual(
            [member["role"] for member in manifest["members"]],
            [
                {"workload_kernel": "default_tenant"},
                {"workload_rootfs": "default_tenant"},
                {"workload_kernel": "rootless_tenant"},
                {"workload_rootfs": "rootless_tenant"},
            ],
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
        (self.images / "flake.lock").write_text("# edited\n")
        manifest = self.emit(
            ("default_tenant_workload_kernel", "kernel:elf", self.artifact("vmlinux"))
        )
        images = manifest["producer"]["local_checkouts"]["images"]
        self.assertEqual(images["worktree"], eml.worktree_state(self.images))
        self.assertEqual(images["worktree"]["state"], "dirty")
        self.assertEqual(set(images["worktree"]),
                         {"state", "fingerprint_version", "fingerprint"})
        self.assertEqual(images["worktree"]["fingerprint_version"], 2)
        self.assertRegex(images["worktree"]["fingerprint"], r"^[0-9a-f]{64}$")

    def test_honours_source_date_epoch(self):
        with mock.patch.dict(os.environ, {"SOURCE_DATE_EPOCH": "1767225600"}):
            manifest = self.emit(
                ("default_tenant_workload_kernel", "kernel:elf", self.artifact("vmlinux"))
            )
        self.assertEqual(manifest["issued_at"], "2026-01-01T00:00:00Z")


class Refusals(Fixture):
    def kernel(self):
        return (
            "default_tenant_workload_kernel",
            "kernel:elf",
            self.artifact("vmlinux"),
        )

    def test_an_output_inside_either_checkout(self):
        for root in (self.images,):
            self.assertIn("inside the checkout", self.refused(self.kernel(), out=root / "set"))

    def test_an_output_that_already_holds_something(self):
        out = self.tmp / "set"
        out.mkdir()
        (out / "stale").write_text("x")
        self.assertIn("already exists", self.refused(self.kernel(), out=out))
        self.assertEqual((out / "stale").read_bytes(), b"x")
        self.assertEqual(list(out.iterdir()), [out / "stale"])

    def test_leaves_nothing_behind_when_refused(self):
        self.refused(self.kernel(), ("no_such_role", "ext4", self.artifact("x")))
        self.assertEqual(sorted(p.name for p in self.tmp.iterdir()), ["built", "mvm-images", "other"])

    def test_a_subdirectory_of_a_checkout(self):
        argv = self.args(self.kernel())
        argv.images_checkout = str(self.images / "images")
        with self.assertRaises(eml.Refusal) as caught:
            eml.emit(argv)
        self.assertIn("not the root of its git checkout", str(caught.exception))

    def test_a_directory_that_is_not_a_git_checkout(self):
        plain = self.tmp / "plain"
        plain.mkdir()
        argv = self.args(self.kernel())
        argv.images_checkout = str(plain)
        with self.assertRaises(eml.Refusal):
            eml.emit(argv)

    def test_a_checkout_that_is_not_mvm_images(self):
        argv = self.args(self.kernel())
        argv.images_checkout = str(self.mvm)
        with self.assertRaises(eml.Refusal) as caught:
            eml.emit(argv)
        self.assertIn("not an mvm-images checkout", str(caught.exception))

    def test_a_symlinked_marker(self):
        target = self.tmp / "elsewhere.nix"
        target.write_text("{ }\n")
        os.remove(self.images / "kernel/flake.nix")
        os.symlink(target, self.images / "kernel/flake.nix")
        self.assertIn("no regular file kernel/flake.nix", self.refused(self.kernel()))

    def test_a_checkout_named_through_a_link_resolves_to_its_root(self):
        link = self.tmp / "mvm-link"
        os.symlink(self.images, link)
        argv = self.args(self.kernel())
        argv.images_checkout = str(link)
        manifest = json.loads(eml.emit(argv).read_text())
        self.assertEqual(
            manifest["producer"]["local_checkouts"]["images"], eml.repo_identity(self.images)
        )

    def test_unknown_roles_formats_and_capabilities(self):
        f = self.artifact("f")
        self.assertIn("unknown role", self.refused(("workload", "ext4", f)))
        self.assertIn(
            "unknown artifact format",
            self.refused(("default_tenant_workload_rootfs", "squashfs", f)),
        )
        self.assertIn(
            "unknown artifact format",
            self.refused(("default_tenant_workload_kernel", "kernel:bzimage", f)),
        )
        self.assertIn(
            "unknown capability",
            self.refused(
                ("default_tenant_workload_rootfs", "ext4", f),
                capabilities=[("default_tenant_workload_rootfs", "gpu")],
            ),
        )
        self.assertIn(
            "has no --artifact",
            self.refused(
                ("default_tenant_workload_rootfs", "ext4", f),
                capabilities=[("builder_vm", "virtio_blk")],
            ),
        )

    def test_two_artifacts_with_one_name(self):
        a = self.artifact("a/vmlinux")
        b = self.artifact("b/vmlinux")
        self.assertIn(
            "both be named",
            self.refused(
                ("default_tenant_workload_kernel", "kernel:elf", a),
                ("default_tenant_workload_kernel", "kernel:elf", b),
            ),
        )

    def test_an_empty_or_missing_or_non_file_artifact(self):
        self.assertIn(
            "zero-size",
            self.refused(
                ("default_tenant_workload_rootfs", "ext4", self.artifact("e", b""))
            ),
        )
        self.assertIn(
            "no such file",
            self.refused(
                ("default_tenant_workload_rootfs", "ext4", self.built / "absent")
            ),
        )
        self.assertIn(
            "not a regular file",
            self.refused(("default_tenant_workload_rootfs", "ext4", self.built)),
        )

    def test_no_artifacts_or_a_bad_cache_contract(self):
        self.assertIn("no --artifact", self.refused())
        self.assertIn("positive integer", self.refused(self.kernel(), contract=0))

    def test_an_unsupported_base_contract_is_refused(self):
        (self.images / "images/base-image-contract.nix").write_text("2\n")
        self.assertIn("unsupported base-image contract", self.refused(self.kernel()))

    def test_a_checkout_that_changes_while_recording(self):
        real_copy = eml.copy_and_hash
        for initially_dirty in (False, True):
            with self.subTest(initially_dirty=initially_dirty):
                tracked = self.images / "flake.lock"
                if initially_dirty:
                    tracked.write_bytes(b"first dirty observation\n")
                before = {"images": eml.repo_identity(self.images)}
                out = self.tmp / "set"
                out.mkdir()

                def mutate_after_copy(source, dest):
                    result = real_copy(source, dest)
                    tracked.write_bytes(b"actual mutation during artifact copy\n")
                    return result

                with mock.patch.object(eml, "copy_and_hash", mutate_after_copy):
                    detail = self.refused(self.kernel(), out=out)
                after = {"images": eml.repo_identity(self.images)}
                self.assertNotEqual(before, after)
                self.assertIn("changed while the set was being recorded", detail)
                self.assertIn(f"was {json.dumps(before)}, now {json.dumps(after)}", detail)
                self.assertTrue(out.is_dir())
                self.assertEqual(list(out.iterdir()), [])
                self.assertEqual(list(self.tmp.glob(".set.*")), [])
                out.rmdir()


if __name__ == "__main__":
    unittest.main()
