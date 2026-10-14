"""V2 defaults need no system controls; explicit requests never guess prefixes."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from dulwich.repo import Repo
from ext4_fixture import contract_rootfs_bytes
from test_local_mvm import committed_repo, eml, images_checkout


class SystemContextPolicyTests(unittest.TestCase):
    def test_default_excludes_system_sources_without_environment_mutation(self):
        with mock.patch.dict(os.environ, {"PATH": ""}, clear=True), mock.patch(
            "subprocess.Popen", side_effect=AssertionError("no subprocess discovery")
        ):
            before = dict(os.environ)
            context = eml._system_context()
            self.assertIsNone(context.config)
            self.assertIsNone(context.attributes)
            self.assertEqual(dict(os.environ), before)

    def test_flag_free_observation_preserves_repo_and_declared_global_rules(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            home = parent / "home"
            home.mkdir()
            ignore = home / "global-ignore"
            ignore.write_text("*.global-noise\n")
            (home / ".gitconfig").write_text(f"[core]\nautocrlf=true\nexcludesfile={ignore}\n")
            env = {"HOME": str(home), "XDG_CONFIG_HOME": str(home / "xdg"), "PATH": ""}
            with mock.patch.dict(os.environ, env, clear=True):
                root = committed_repo(parent / "repo", {"a": "base\n", ".gitignore": "*.repo-noise\n"})
                (root / "a").write_bytes(b"base\r\n")
                (root / "x.global-noise").write_text("global ignored")
                (root / "x.repo-noise").write_text("repository ignored")
                with mock.patch("subprocess.Popen", side_effect=AssertionError("no subprocess")):
                    self.assertEqual(eml.worktree_state(root), {"state": "clean"})
                for name in ("GIT_CONFIG_SYSTEM", "GIT_CONFIG_NOSYSTEM", "GIT_ATTR_NOSYSTEM"):
                    self.assertNotIn(name, os.environ)

    def test_one_observation_shares_one_immutable_system_context(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            with mock.patch.dict(os.environ, {"PATH": ""}, clear=True):
                root = committed_repo(parent / "repo", {"a": "base\n"})
                (root / "a").write_bytes(b"base\r\n")
                contexts = []
                for name, rule in (("first", "a text\n"), ("second", "a -text\n")):
                    prefix = parent / name
                    attrs = prefix / "etc/gitattributes"
                    attrs.parent.mkdir(parents=True)
                    attrs.write_text(rule)
                    contexts.append(eml.GitSystemContext(None, attrs, prefix))
                with mock.patch.object(eml, "_system_context", side_effect=contexts) as resolve:
                    self.assertEqual(eml.worktree_state(root), {"state": "clean"})
                self.assertEqual(resolve.call_count, 1)

    def test_flag_free_local_manifest_inspects_actual_copied_root(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            env = {"HOME": str(parent / "home"), "XDG_CONFIG_HOME": str(parent / "xdg"), "PATH": ""}
            with mock.patch.dict(os.environ, env, clear=True):
                checkout = images_checkout(parent / "images", boot_abi="2\n")
                rootfs = parent / "rootfs.ext4"
                rootfs.write_bytes(contract_rootfs_bytes())
                args = eml.parse_args([
                    "--images-checkout", str(checkout), "--arch", "x86_64",
                    "--builder-cache-contract", "1", "--out", str(parent / "set"),
                    "--artifact", "default_tenant_workload_rootfs", "ext4", str(rootfs),
                ])
                with mock.patch("subprocess.Popen", side_effect=AssertionError("no subprocess")):
                    manifest = json.loads(eml.emit(args).read_text())
                self.assertEqual(manifest["compatibility"]["base_image_contract"], 1)
                self.assertEqual(manifest["producer"]["local_checkouts"]["images"]["worktree"],
                                 {"state": "clean"})
                for name in ("GIT_CONFIG_SYSTEM", "GIT_CONFIG_NOSYSTEM", "GIT_ATTR_NOSYSTEM"):
                    self.assertNotIn(name, os.environ)

    def test_independent_controls_and_nosystem_precedence(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "selected-config"
            config.write_text("[core]\nautocrlf=yes\n")
            with mock.patch.dict(os.environ, {"GIT_CONFIG_SYSTEM": str(config)}, clear=True):
                context = eml._system_context()
                self.assertEqual(context.config, config)
                self.assertIsNone(context.attributes)
            with mock.patch.dict(os.environ, {
                "GIT_CONFIG_SYSTEM": "/missing/ignored", "GIT_CONFIG_NOSYSTEM": "YES",
            }, clear=True):
                self.assertIsNone(eml._system_context().config)
            for name in ("GIT_CONFIG_NOSYSTEM", "GIT_ATTR_NOSYSTEM"):
                for invalid in ("nonsense", "08", "2g"):
                    with self.subTest(name=name, invalid=invalid), \
                         mock.patch.dict(os.environ, {name: invalid}, clear=True):
                        with self.assertRaises(eml.Refusal):
                            eml._system_context()

    def test_proven_windows_prefix_resolves_explicit_sources_independently(self):
        with tempfile.TemporaryDirectory() as directory:
            prefix = Path(directory) / "git"
            (prefix / "etc").mkdir(parents=True)
            config = Path(directory) / "elsewhere-config"
            config.write_text("[core]\nautocrlf=true\n")
            # Mock only the platform dispatch and already-verified prefix.
            # pathlib still uses the real host OS; no OS behavior is simulated.
            platform = SimpleNamespace(name="nt", environ=os.environ, fsencode=os.fsencode)
            with mock.patch.object(eml, "os", platform), \
                 mock.patch.object(eml, "_windows_prefix", return_value=prefix) as discover:
                with mock.patch.dict(os.environ, {}, clear=True):
                    context = eml._system_context()
                    self.assertIsNone(context.config)
                    self.assertIsNone(context.attributes)
                    self.assertIsNone(context.prefix)
                    discover.assert_not_called()
                with mock.patch.dict(os.environ, {
                    "GIT_CONFIG_SYSTEM": str(config), "GIT_ATTR_NOSYSTEM": "false",
                }, clear=True):
                    context = eml._system_context()
                    self.assertEqual(context.config, config)
                    self.assertEqual(context.attributes, prefix / "etc/gitattributes")
                    self.assertEqual(context.prefix, prefix)
                with mock.patch.dict(os.environ, {"GIT_CONFIG_NOSYSTEM": "0"}, clear=True):
                    context = eml._system_context()
                    self.assertEqual(context.config, prefix / "etc/gitconfig")
                    self.assertIsNone(context.attributes)


@unittest.skipIf(os.name == "nt", "POSIX prefix policy")
class ConfigurationPrefixTests(unittest.TestCase):
    def test_no_system_modes_need_no_installed_git(self):
        with mock.patch.dict(os.environ, {
            "PATH": "", "GIT_CONFIG_NOSYSTEM": "1", "GIT_ATTR_NOSYSTEM": "1",
            "GIT_CONFIG_SYSTEM": "/missing/ignored",
        }, clear=True):
            self.assertEqual(eml._system_config(), (None, None))
            with self.assertRaisesRegex(eml.Refusal, "absolute data path"):
                eml._rule_path(b"%(prefix)/etc/ignore", "ignore", b"/repo", None)

    def test_default_does_not_guess_posix_installations_from_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = []
            for name in ("homebrew", "custom", "nix/store/git", "usr"):
                prefix = root / name
                (prefix / "bin").mkdir(parents=True)
                (prefix / "etc").mkdir()
                (prefix / "bin/git").write_bytes(b"not an executable probe\n")
                (prefix / "etc/gitconfig").write_text("[core]\nautocrlf = true\n")
                paths.append(str(prefix / "bin"))
            for path in ("", *paths, os.pathsep.join(paths)):
                with self.subTest(path=path), mock.patch.dict(os.environ, {
                    "PATH": path, "GIT_ATTR_NOSYSTEM": "1",
                }, clear=True):
                    self.assertEqual(eml._system_config(), (None, None))
                    os.environ["GIT_CONFIG_NOSYSTEM"] = "false"
                    with self.assertRaisesRegex(eml.Refusal, "GIT_CONFIG_SYSTEM"):
                        eml._system_config()

    def test_explicit_homebrew_and_custom_config_contribute_rules(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo_path = root / "repo"
            repo_path.mkdir()
            with Repo.init(str(repo_path)) as repo:
                for name in ("homebrew", "custom", "nix"):
                    prefix = root / name
                    (prefix / "etc").mkdir(parents=True)
                    ignore = prefix / "etc/ignore"
                    ignore.write_text("ignored\n")
                    attrs = prefix / "etc/attributes"
                    attrs.write_text("*.txt text\n")
                    included = prefix / "etc/included"
                    included.write_text("[core]\nautocrlf = true\n")
                    config_path = prefix / "etc/gitconfig"
                    config_path.write_text(
                        f'[include]\npath = {included}\n[core]\n'
                        f'excludesfile = {ignore}\nattributesfile = {attrs}\n'
                    )
                    with self.subTest(prefix=name), mock.patch.dict(os.environ, {
                        "PATH": "", "GIT_CONFIG_SYSTEM": str(config_path),
                        "GIT_ATTR_NOSYSTEM": "1",
                    }, clear=True):
                        path, discovered = eml._system_config()
                        self.assertEqual(path, config_path)
                        self.assertIsNone(discovered)
                        config = eml._strict_config(path, repo, discovered)
                        self.assertEqual(eml._config(config, b"autocrlf"), b"true")
                        rules = eml.RepositoryRules(repo, config, {}, os.fsencode(repo_path))
                        self.assertTrue(rules.global_ignore[0].is_ignored(b"ignored"))
                        self.assertEqual(rules.global_attrs, b"*.txt text\n")
                        self.assertIsNone(rules.system_attrs)
                        with self.assertRaisesRegex(eml.Refusal, "absolute data path"):
                            eml._rule_path(b"%(prefix)/etc/ignore", "ignore",
                                           os.fsencode(repo_path), discovered)

    def test_config_override_does_not_redirect_system_attributes(self):
        with mock.patch.dict(os.environ, {
            "GIT_CONFIG_SYSTEM": "/arbitrary/etc/gitconfig",
            "GIT_ATTR_NOSYSTEM": "false",
        }, clear=True):
            with self.assertRaisesRegex(eml.Refusal, "GIT_ATTR_NOSYSTEM"):
                eml._system_config()
        with mock.patch.dict(os.environ, {
            "GIT_CONFIG_NOSYSTEM": "1", "GIT_ATTR_NOSYSTEM": "false",
        }, clear=True):
            with self.assertRaisesRegex(eml.Refusal, "GIT_ATTR_NOSYSTEM"):
                eml._system_config()

    def test_explicit_missing_and_empty_config_paths_refuse(self):
        with tempfile.TemporaryDirectory() as directory:
            for path in ("", str(Path(directory) / "absent")):
                with self.subTest(path=path), mock.patch.dict(os.environ, {
                    "GIT_CONFIG_SYSTEM": path, "GIT_ATTR_NOSYSTEM": "1",
                }, clear=True):
                    with self.assertRaisesRegex(eml.Refusal, "GIT_CONFIG_NOSYSTEM"):
                        eml._system_config()

    def test_windows_data_prefix_behavior_remains_available(self):
        with tempfile.TemporaryDirectory() as directory:
            prefix = Path(directory)
            (prefix / "cmd").mkdir()
            (prefix / "etc").mkdir()
            (prefix / "cmd/git.exe").write_bytes(b"fixture")
            self.assertEqual(eml._windows_prefix_from_path([prefix / "cmd"]), prefix.resolve())
            self.assertEqual(eml._rule_path(b"%(prefix)/etc/ignore", "ignore",
                                           b"/repo", prefix), prefix / "etc/ignore")


if __name__ == "__main__":
    unittest.main()
