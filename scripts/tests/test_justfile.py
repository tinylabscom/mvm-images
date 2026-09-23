from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]


class JustfileToolchainTests(unittest.TestCase):
    def test_builder_vm_uses_the_zig_it_installs(self):
        justfile = (ROOT / "justfile").read_text()
        recipe = justfile.split("builder-vm mvm_checkout=", 1)[1].split(
            "# Build every release-bearing output", 1
        )[0]

        install = 'scripts/install-host-toolchain.sh "${install_args[@]}" "$toolchain_prefix"'
        activate = 'export PATH="$toolchain_prefix/bin:$PATH"'
        build = "scripts/build-host-binaries.sh"

        self.assertIn('toolchain_prefix="$HOME/.local/mvm-images"', recipe)
        self.assertIn(install, recipe)
        self.assertIn(activate, recipe)
        self.assertLess(recipe.index(install), recipe.index(activate))
        self.assertLess(recipe.index(activate), recipe.index(build))


if __name__ == "__main__":
    unittest.main()
