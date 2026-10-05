"""The producer's port of mvm's `sdk_cdylib_source_fingerprint` must agree
with the Rust function byte for byte.

The expected values are known answers computed with the Rust implementation
(`mvm_build::guest_agent_build::sdk_cdylib_source_fingerprint`) and checked
in, so a change to either side's framing or input list fails here rather
than silently publishing a fingerprint no consumer ever matches. The
consumer-side consequence of a mismatch is only a needless pair-build, but
the parity this test holds is what makes the fetch-when-unchanged arm worth
having.
"""

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/assemble-release.py"
SPEC = importlib.util.spec_from_file_location("assemble_release", SCRIPT)
assert SPEC and SPEC.loader
ASSEMBLER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = ASSEMBLER
SPEC.loader.exec_module(ASSEMBLER)

# Known answer from the Rust function over the synthetic tree built below
# (mvm_build::guest_agent_build::sdk_cdylib_source_fingerprint, run
# 2026-10-04 against mvm 4007a31595, whose input list includes mvm-setpriv).
FIXTURE_FINGERPRINT = "6c863f266a6ad21830bb9b35a92b10a38be37be599448053ebf935594634c95c"

FIXTURE_FILES = {
    "Cargo.toml": '[workspace]\n',
    "Cargo.lock": "lock-v1\n",
    "crates/mvm-contract/Cargo.toml": '[package]\nname = "mvm-contract"\n',
    "crates/mvm-contract/src/lib.rs": "pub const A: u8 = 1;\n",
    "crates/mvm-core/Cargo.toml": '[package]\nname = "mvm-core"\n',
    "crates/mvm-core/src/lib.rs": "pub const B: u8 = 2;\n",
    "crates/mvm-core/src/sub/mod.rs": "nested\n",
    "crates/mvm-agentd/Cargo.toml": '[package]\nname = "mvm-agentd"\n',
    "crates/mvm-agentd/src/lib.rs": "pub const C: u8 = 3;\n",
    "crates/mvm-host-services/Cargo.toml": '[package]\nname = "mvm-host-services"\n',
    "crates/mvm-host-services/src/lib.rs": "pub const D: u8 = 4;\n",
    "crates/mvm-setpriv/Cargo.toml": '[package]\nname = "mvm-setpriv"\n',
    "crates/mvm-setpriv/src/main.rs": "fn main() {}\n",
}


class SdkCdylibFingerprintTests(unittest.TestCase):
    def test_the_port_matches_the_rust_known_answer(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for rel, body in FIXTURE_FILES.items():
                path = root / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(body)
            self.assertEqual(
                ASSEMBLER.sdk_cdylib_fingerprint(root), FIXTURE_FINGERPRINT
            )

    def test_a_missing_input_refuses_by_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for rel, body in FIXTURE_FILES.items():
                if rel == "crates/mvm-agentd/src/lib.rs":
                    continue
                path = root / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(body)
            with self.assertRaises(ASSEMBLER.Refusal) as caught:
                ASSEMBLER.sdk_cdylib_fingerprint(root)
            self.assertIn("crates/mvm-agentd/src", str(caught.exception))

    def test_a_missing_setpriv_input_refuses_by_path(self):
        """mvm added mvm-setpriv to the hashed inputs; a tree without it must
        refuse rather than fingerprint the subset it can read."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for rel, body in FIXTURE_FILES.items():
                if rel.startswith("crates/mvm-setpriv/"):
                    continue
                path = root / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(body)
            with self.assertRaises(ASSEMBLER.Refusal) as caught:
                ASSEMBLER.sdk_cdylib_fingerprint(root)
            self.assertIn("crates/mvm-setpriv", str(caught.exception))

    def test_every_byte_feeds_the_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for rel, body in FIXTURE_FILES.items():
                path = root / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(body)
            (root / "crates/mvm-core/src/lib.rs").write_text(
                "pub const B: u8 = 99;\n"
            )
            self.assertNotEqual(
                ASSEMBLER.sdk_cdylib_fingerprint(root), FIXTURE_FINGERPRINT
            )


if __name__ == "__main__":
    unittest.main()
