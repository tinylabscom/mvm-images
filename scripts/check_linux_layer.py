#!/usr/bin/env python3
"""Validate locally owned Linux-layer formats, budgets and root contracts."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import struct
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def check_kernel(path: Path, arch: str, format: str) -> None:
    data = path.read_bytes()
    if format == "elf":
        machine = 183 if arch == "aarch64" else 62
        valid = (len(data) >= 64 and data[:6] == b"\x7fELF\x02\x01"
                 and struct.unpack_from("<H", data, 18)[0] == machine)
    elif format == "image":
        valid = arch == "aarch64" and len(data) >= 64 and data[56:60] == b"ARM\x64"
    else:
        valid = (arch == "x86_64" and len(data) > 0x206
                 and data[0x1FE:0x200] == b"\x55\xaa"
                 and data[0x202:0x206] == b"HdrS")
    if not valid:
        raise ValueError(f"{path}: not a {arch} {format} kernel")


def check_budget(path: Path, arch: str) -> None:
    budget = json.loads((ROOT / "kernel/budgets.json").read_text())[arch]
    count = len(re.findall(r"^CONFIG_[A-Z0-9_]+=y$", path.read_text(), re.M))
    if count > budget:
        raise ValueError(f"{path}: {count} built-in symbols exceeds {arch} budget {budget}")
    print(f"{arch}: {count} built-in symbols (budget {budget})")


def debugfs(image: Path, command: str) -> str:
    result = subprocess.run(["debugfs", "-R", command, str(image)], check=True,
                            capture_output=True)
    # Do not normalize CRLF: the contract marker is an exact byte promise.
    return result.stdout.decode("utf-8")


def check_rootfs(image: Path, builder: bool = False) -> None:
    for path in ("/etc", "/etc/mvm"):
        if "Type: directory" not in debugfs(image, f"stat {path}"):
            raise ValueError(f"{image}: {path} must be a directory, not a symlink")
    marker = "/etc/mvm/base-image-contract"
    stat = debugfs(image, f"stat {marker}")
    if "Type: regular" not in stat or debugfs(image, f"cat {marker}") != "1\n":
        raise ValueError(f"{image}: missing regular contract-1 marker")
    if re.search(r"Mode:\s+0*444\b", stat) is None:
        raise ValueError(f"{image}: base-image contract marker must be mode 0444")
    if "Inode:" in debugfs(image, "stat /init"):
        raise ValueError(f"{image}: base must not contain /init")
    for path in ("/mvm/runtime", "/mvm/sdk"):
        if "Type: directory" not in debugfs(image, f"stat {path}"):
            raise ValueError(f"{image}: missing runtime mount target {path}")
    for path in ("/etc/resolv.conf", "/etc/ssl/certs/ca-bundle.crt",
                 "/nix-path-registration"):
        if "Type: regular" not in debugfs(image, f"stat {path}"):
            raise ValueError(f"{image}: missing Linux-layer file {path}")
    identities = {"root": 0, "mvm-worker": 901, "mvm-dns": 987,
                  "mvm-crng": 988, "mvm-egress": 989}
    if builder:
        identities["mvm-builder"] = 902
        if debugfs(image, "cat /etc/mvm/builder-boot-abi") != "2\n":
            raise ValueError(f"{image}: builder boot ABI must be 2")
        for path in ("/nix-store", "/job", "/out", "/work", "/mvm-bins", "/closure-seed"):
            if "Type: directory" not in debugfs(image, f"stat {path}"):
                raise ValueError(f"{image}: missing builder mount target {path}")
    passwd = debugfs(image, "cat /etc/passwd")
    groups = debugfs(image, "cat /etc/group")
    for name, uid in identities.items():
        if re.search(rf"^{name}:x:{uid}:{uid}:", passwd, re.M) is None:
            raise ValueError(f"{image}: missing fixed identity {name}:{uid}")
        if re.search(rf"^{name}:x:{uid}:", groups, re.M) is None:
            raise ValueError(f"{image}: missing fixed group {name}:{uid}")
    # Conventional executable directories contain only Linux packages.
    # The image-owned rootless smoke probe is the one permitted mvm prefix.
    for directory in ("/bin", "/sbin", "/usr/bin", "/usr/sbin", "/usr/local/bin"):
        listing = debugfs(image, f"ls -p {directory}")
        names = re.findall(r"/(mvm-[^/]+)/", listing)
        if any(name != "mvm-rootless-probe" for name in names):
            raise ValueError(f"{image}: guest runtime executable in {directory}: {names}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arch", choices=("aarch64", "x86_64"), required=True)
    parser.add_argument("--kernel", type=Path)
    parser.add_argument("--format", choices=("elf", "image", "bzimage"))
    parser.add_argument("--kernel-config", type=Path)
    parser.add_argument("--rootfs", type=Path)
    parser.add_argument("--builder", action="store_true")
    args = parser.parse_args()
    if not any((args.kernel, args.kernel_config, args.rootfs)):
        parser.error("provide --kernel, --kernel-config or --rootfs")
    if args.kernel and not args.format:
        parser.error("--kernel requires --format")
    try:
        if args.kernel:
            check_kernel(args.kernel, args.arch, args.format)
        if args.kernel_config:
            check_budget(args.kernel_config, args.arch)
        if args.rootfs:
            check_rootfs(args.rootfs, args.builder)
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        print(f"Linux-layer check failed: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
