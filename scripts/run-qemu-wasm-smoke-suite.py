#!/usr/bin/env python3
"""Headless-Chromium smoke suite for the standalone QEMU-Wasm pack.

The pack has no guest network device. This suite delegates to the standalone
boot harness and checks the guest readiness marker; it never invokes mvm.
"""
import json
import os
import subprocess
import sys

SMOKE_SCRIPT = os.path.join(os.path.dirname(__file__), "run-qemu-wasm-smoke-chromium.py")


def run_boot(pack_dir: str, chrome_bin: str) -> dict:
    """Boot the pack once and return the smoke harness result."""
    args = [sys.executable, SMOKE_SCRIPT, pack_dir, chrome_bin]
    proc = subprocess.run(args, capture_output=True, text=True, timeout=150)
    marker = "=== RESULT ==="
    pos = proc.stdout.rfind(marker)
    if pos == -1:
        raise RuntimeError(f"no result block from browser boot\n{proc.stdout}\n{proc.stderr}")
    raw = proc.stdout[pos + len(marker):].strip()
    return json.loads(raw)


def main():
    if len(sys.argv) < 3:
        print(f"usage: {sys.argv[0]} <staged-demo-dir> <chrome-binary>", file=sys.stderr)
        sys.exit(2)

    pack_dir = sys.argv[1]
    chrome_bin = sys.argv[2]
    try:
        result = run_boot(pack_dir, chrome_bin)
    except Exception as exc:
        result = {"success": False, "error": str(exc)}
    summary = {"success": result.get("success") is True, "result": result}
    print("\n=== SUITE RESULT ===")
    print(json.dumps(summary, indent=2))
    sys.exit(0 if summary["success"] else 1)


if __name__ == "__main__":
    main()
