from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]


class JustfileBuilderVmTests(unittest.TestCase):
    def test_builder_vm_is_a_pure_nix_build(self):
        """ABI 1: the builder image bakes no mvm host binaries, so its
        recipe is a plain nix build — no zig toolchain, no
        build-host-binaries.sh, no MVM_HOST_BIN_DIR, no --impure."""
        justfile = (ROOT / "justfile").read_text()
        recipe = justfile.split("builder-vm mvm_checkout=", 1)[1].split(
            "# Build every release-bearing output", 1
        )[0]

        self.assertIn(
            'nix build ".#legacyPackages.{{system}}.builder-vm.default"', recipe
        )
        self.assertNotIn("install-host-toolchain", recipe)
        self.assertNotIn("build-host-binaries", recipe)
        self.assertNotIn("MVM_HOST_BIN_DIR", recipe)
        self.assertNotIn("--impure", recipe)


if __name__ == "__main__":
    unittest.main()
