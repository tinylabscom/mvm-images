"""Read the base-image contract from ext4 bytes, never from source metadata."""
from __future__ import annotations

from pathlib import Path
import re
import stat
import subprocess

MARKER = "/etc/mvm/base-image-contract"


def validate_base_image_contract_marker(mode: int, content: bytes) -> int:
    """Shared marker rule for in-process and debugfs filesystem adapters."""
    if mode != stat.S_IFREG | 0o444:
        raise ValueError("base-image contract marker must be regular mode 0444")
    if content != b"1\n":
        raise ValueError(f"unsupported or malformed base-image contract: {content!r}")
    return 1


def debugfs(image: Path, command: str, *, allow_missing: bool = False) -> str:
    """Run read-only debugfs; its exit status alone does not report errors.

    Only the normal version banner is allowed on stderr. Optional filesystem
    entries may explicitly permit debugfs's precise lookup-not-found diagnostic.
    All other diagnostics (including successful-exit tool failures) are fatal.
    """
    result = subprocess.run(
        ["debugfs", "-R", command, str(image)], check=True, capture_output=True,
    )
    diagnostics = result.stderr.decode("utf-8").splitlines()
    if diagnostics and re.fullmatch(r"debugfs \d+\.\d+(?:\.\d+)? \([^)]+\)", diagnostics[0]):
        diagnostics.pop(0)
    if diagnostics:
        missing = f"{command.split()[0]}: File not found by ext2_lookup "
        if (allow_missing and len(diagnostics) == 1
                and diagnostics[0].startswith(missing) and not result.stdout):
            return ""
        raise ValueError(f"{image}: debugfs {command!r}: {'; '.join(diagnostics)}")
    # No universal-newline conversion: the marker promises exact bytes.
    return result.stdout.decode("utf-8")


def read_base_image_contract(image: Path) -> int:
    """Require real directories and a regular mode-0444 marker containing 1\\n.

    Raises ValueError, OSError or subprocess.CalledProcessError on refusal.
    Callers publishing hashes must also guard against changes during inspection.
    """
    for path in ("/etc", "/etc/mvm"):
        if "Type: directory" not in debugfs(image, f"stat {path}"):
            raise ValueError(f"{image}: {path} must be a directory, not a symlink")
    metadata = debugfs(image, f"stat {MARKER}")
    if "Type: regular" not in metadata:
        raise ValueError(f"{image}: missing regular base-image contract marker")
    mode = re.search(r"\bMode:\s+([0-7]+)\b", metadata)
    if mode is None:
        raise ValueError(f"{image}: base-image contract marker must be mode 0444")
    value = debugfs(image, f"cat {MARKER}")
    return validate_base_image_contract_marker(
        stat.S_IFREG | int(mode[1], 8), value.encode("utf-8"),
    )
