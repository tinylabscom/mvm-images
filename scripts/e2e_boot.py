#!/usr/bin/env python3
"""Boot the standalone smoke image directly with QEMU or Firecracker.

No mvm process, CLI, library or sibling checkout participates. The VMM device
list is explicit and contains block, serial and vsock only.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import selectors
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any


READY_MARKER = "QEMU-WASM-SMOKE-READY"
FORBIDDEN_PLAN_TOKENS = (
    "virtio-net",
    "e1000",
    "rtl8139",
    "vmxnet",
    "-netdev",
    "-nic",
    "tap",
    "tun",
    "slirp",
    "passt",
    "vpnkit",
)


class E2EError(RuntimeError):
    pass


def qemu_command(
    *,
    binary: str,
    kernel: Path,
    rootfs: Path,
    guest_cid: int,
    accel: str = "kvm",
    rootfs_type: str = "ext2",
) -> list[str]:
    if accel not in ("kvm", "tcg"):
        raise E2EError(f"unsupported QEMU accelerator: {accel}")
    cmdline = (
        f"console=ttyS0 root=/dev/vda rw rootfstype={rootfs_type} rootwait "
        "panic=1 init=/init loglevel=4"
    )
    command = [
        binary,
        "-nodefaults",
        "-no-user-config",
        "-no-reboot",
        "-display",
        "none",
        "-monitor",
        "none",
        "-serial",
        "stdio",
        "-machine",
        f"q35,accel={accel}",
        "-cpu",
        "host" if accel == "kvm" else "max",
        "-m",
        "256M",
        "-kernel",
        str(kernel),
        "-append",
        cmdline,
        "-drive",
        f"id=rootfs,file={rootfs},format=raw,if=none,readonly=off",
        "-device",
        "virtio-blk-pci,drive=rootfs",
        "-device",
        f"vhost-vsock-pci,guest-cid={guest_cid}",
    ]
    assert_no_network_devices(command)
    return command


def firecracker_config(
    *,
    kernel: Path,
    rootfs: Path,
    vsock_path: Path,
    guest_cid: int,
    rootfs_type: str = "ext2",
) -> dict[str, Any]:
    cmdline = (
        f"console=ttyS0 root=/dev/vda rw rootfstype={rootfs_type} rootwait "
        "panic=1 init=/init loglevel=4 reboot=k pci=off"
    )
    config: dict[str, Any] = {
        "boot-source": {
            "kernel_image_path": str(kernel),
            "boot_args": cmdline,
        },
        "drives": [
            {
                "drive_id": "rootfs",
                "path_on_host": str(rootfs),
                "is_root_device": True,
                "is_read_only": False,
            }
        ],
        "machine-config": {"vcpu_count": 1, "mem_size_mib": 256},
        "vsock": {
            "guest_cid": guest_cid,
            "uds_path": str(vsock_path),
        },
    }
    assert_no_network_devices(config)
    return config


def assert_no_network_devices(plan: Any) -> None:
    rendered = json.dumps(plan, sort_keys=True).lower()
    violations = [token for token in FORBIDDEN_PLAN_TOKENS if token in rendered]
    if "network-interfaces" in rendered:
        violations.append("network-interfaces")
    if violations:
        raise E2EError("forbidden network device in VMM plan: " + ", ".join(violations))


def _require_file(path: Path, label: str) -> Path:
    resolved = path.resolve()
    if not resolved.is_file():
        raise E2EError(f"{label} does not exist: {resolved}")
    return resolved


def _require_binary(binary: str) -> str:
    found = shutil.which(binary)
    if found is None:
        raise E2EError(f"required VMM binary is not on PATH: {binary}")
    return found


def run_until_ready(command: list[str], timeout: float, ready_marker: str) -> None:
    assert_no_network_devices(command)
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    assert process.stdout is not None
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    deadline = time.monotonic() + timeout
    captured: list[str] = []
    try:
        while time.monotonic() < deadline:
            for key, _ in selector.select(timeout=0.25):
                line = key.fileobj.readline()
                if not line:
                    continue
                sys.stdout.write(line)
                sys.stdout.flush()
                captured.append(line)
                if ready_marker in line:
                    return
            status = process.poll()
            if status is not None:
                raise E2EError(
                    f"VMM exited {status} before {ready_marker!r}\n"
                    + "".join(captured[-80:])
                )
        raise E2EError(
            f"timed out after {timeout:.0f}s waiting for {ready_marker!r}\n"
            + "".join(captured[-80:])
        )
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


def _plan(args: argparse.Namespace) -> Any:
    kernel = args.artifacts / ("kernel.img" if args.backend == "qemu" else "vmlinux")
    rootfs = args.artifacts / args.rootfs_name
    if args.backend == "qemu":
        return qemu_command(
            binary=args.binary or "qemu-system-x86_64",
            kernel=kernel,
            rootfs=rootfs,
            guest_cid=args.guest_cid,
            accel=args.accel,
            rootfs_type=args.rootfs_type,
        )
    return firecracker_config(
        kernel=kernel,
        rootfs=rootfs,
        vsock_path=Path("/tmp/mvm-images-e2e.vsock"),
        guest_cid=args.guest_cid,
        rootfs_type=args.rootfs_type,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("backend", choices=("qemu", "firecracker"))
    parser.add_argument(
        "artifacts", type=Path, help="directory with kernel.img, vmlinux and a rootfs image"
    )
    parser.add_argument("--binary", help="VMM executable (defaults by backend)")
    parser.add_argument("--guest-cid", type=int, default=7)
    parser.add_argument("--timeout", type=float, default=60)
    parser.add_argument("--accel", choices=("kvm", "tcg"), default="kvm")
    parser.add_argument("--rootfs-type", choices=("ext2", "ext4"), default="ext2")
    parser.add_argument("--rootfs-name", default="rootfs.bin")
    parser.add_argument("--ready-marker", default=READY_MARKER)
    parser.add_argument("--plan", action="store_true", help="print the VMM plan without booting")
    args = parser.parse_args()

    try:
        plan = _plan(args)
        if args.plan:
            print(json.dumps(plan, indent=2))
            return 0

        kernel_name = "kernel.img" if args.backend == "qemu" else "vmlinux"
        kernel = _require_file(args.artifacts / kernel_name, "kernel")
        rootfs = _require_file(args.artifacts / args.rootfs_name, "rootfs")
        with tempfile.TemporaryDirectory(prefix="mvm-images-e2e-") as temp:
            temp_path = Path(temp)
            writable_rootfs = temp_path / "rootfs.bin"
            shutil.copyfile(rootfs, writable_rootfs)
            os.chmod(writable_rootfs, 0o600)
            if args.backend == "qemu":
                binary = _require_binary(args.binary or "qemu-system-x86_64")
                command = qemu_command(
                    binary=binary,
                    kernel=kernel,
                    rootfs=writable_rootfs,
                    guest_cid=args.guest_cid,
                    accel=args.accel,
                    rootfs_type=args.rootfs_type,
                )
                run_until_ready(command, args.timeout, args.ready_marker)
            else:
                binary = _require_binary(args.binary or "firecracker")
                config = firecracker_config(
                    kernel=kernel,
                    rootfs=writable_rootfs,
                    vsock_path=temp_path / "vsock.sock",
                    guest_cid=args.guest_cid,
                    rootfs_type=args.rootfs_type,
                )
                config_path = temp_path / "config.json"
                config_path.write_text(json.dumps(config), encoding="utf-8")
                run_until_ready(
                    [binary, "--no-api", "--config-file", str(config_path)],
                    args.timeout,
                    args.ready_marker,
                )
    except (E2EError, OSError) as exc:
        print(f"e2e boot failed: {exc}", file=sys.stderr)
        return 1

    print(f"{args.backend}: reached {args.ready_marker}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
