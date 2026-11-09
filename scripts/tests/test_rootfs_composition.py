from pathlib import Path
import re
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]


class RootfsCompositionTests(unittest.TestCase):
    def test_sealed_workload_tars_share_the_fakeroot_tree_with_ext4(self):
        source = (ROOT / "images/mk-rootfs.nix").read_text()
        default = (ROOT / "images/default-tenant/image.nix").read_text()
        rootless = (ROOT / "images/rootless-tenant/image.nix").read_text()
        self.assertIn('outputs = [ "out" ] ++ lib.optional composition "composition"', source)
        self.assertIn("assert !composition || (sealed && builderUid == null)", source)
        self.assertIn('tar --sort=name --format=gnu --numeric-owner --mtime=@1', source)
        self.assertLess(source.index("mkfs.ext4 -q -F -d root"), source.index("tar --sort=name"))
        self.assertLess(source.index("tar --sort=name"), source.index("  IMAGE"))
        self.assertIn("composition = sealed;", default)
        self.assertIn("composition = sealed && !smoke;", rootless)
        self.assertIn('cp ${rootfsPkg.composition} $out/rootfs.tar.gz', default)
        self.assertIn('cp ${rootfsPkg.composition} $out/rootfs.tar.gz', rootless)
        reproduce = (ROOT / "scripts/check-reproducible.sh").read_text()
        self.assertIn('rebuild -L "${ref}.rootfs.composition"', reproduce)

    def test_published_kernels_select_the_architecture_matched_format(self):
        builder = (ROOT / "images/builder-vm/image.nix").read_text()
        staging = (ROOT / "scripts/build-kernel-artifacts.sh").read_text()
        self.assertIn(
            'if pkgs.stdenv.hostPlatform.isAarch64 then "Image" else "vmlinux"',
            builder,
        )
        self.assertNotIn("elif [ -f ${kernelPkg}/bzImage", builder)
        self.assertIn("kernel_file=vmlinux", staging)
        self.assertIn('kernel_file=Image', staging)
        self.assertIn("format=elf", staging)
        self.assertNotIn("bzImage", staging)

    def test_explicit_symlink_overrides_replace_package_links(self):
        source = (ROOT / "images/mk-rootfs.nix").read_text()
        rule = re.search(
            r"ln (-[a-z]+) \$\{lib.escapeShellArg value\} "
            r"root\$\{lib.escapeShellArg path\}",
            source,
        )
        self.assertIsNotNone(rule, "the extraFiles symlink rule must be tested")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            destination = root / "firecracker"
            destination.symlink_to(root / "package-firecracker")
            override = root / "explicit-firecracker"
            subprocess.run(
                ["ln", rule.group(1), str(override), str(destination)],
                check=True,
                capture_output=True,
            )
            self.assertEqual(destination.readlink(), override)


if __name__ == "__main__":
    unittest.main()
