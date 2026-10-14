#!/usr/bin/env python3
"""Boot the standalone smoke image directly with QEMU or Firecracker.

No mvm process, CLI, library or sibling checkout participates. The VMM device
list is explicit and contains block, serial and vsock only.

The guest boots `init=` a program the smoke image itself owns. The harness
never boots mkGuest's shell `/init`, attaches a runtime overlay, or passes an
`mvm.*` kernel parameter: the guest runtime is mvm's test surface, and a boot
witness here covers the kernel, the base root and the devices.
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
# The qemu-wasm smoke image's own busybox init.
DEFAULT_INIT = "/bin/init"
# mkGuest's shell init, which no boot witness here may depend on.
FORBIDDEN_INIT = "/init"
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


def _init_arg(init: str) -> str:
    if not init.startswith("/") or any(c.isspace() for c in init):
        raise E2EError(f"init must be an absolute path without whitespace: {init!r}")
    if init == FORBIDDEN_INIT:
        raise E2EError(
            "/init is mkGuest's shell init; boot an init the smoke image owns"
        )
    return f"init={init}"


def host_supports_vhost_vsock() -> bool:
    """Return True if the host appears to provide an openable /dev/vhost-vsock device.

    We try to open the device for read/write; permission failures or missing nodes
    are treated as "no support" so the e2e harness can run on hosted runners that
    either lack the device or do not grant access.
    """
    path = Path("/dev/vhost-vsock")
    try:
        fd = os.open(str(path), os.O_RDWR)
    except (FileNotFoundError, PermissionError, OSError):
        return False
    else:
        try:
            os.close(fd)
        except OSError:
            pass
        return True


def qemu_command(
    *,
    binary: str,
    kernel: Path,
    rootfs: Path,
    guest_cid: int,
    accel: str = "kvm",
    rootfs_type: str = "ext2",
    init: str = DEFAULT_INIT,
    arch: str = "x86_64",
    initrd: Path | None = None,
    read_only: bool = False,
    require_vsock: bool = False,
) -> list[str]:
    if accel not in ("kvm", "tcg"):
        raise E2EError(f"unsupported QEMU accelerator: {accel}")
    if arch not in ("x86_64", "aarch64"):
        raise E2EError(f"unsupported architecture: {arch}")
    console = "ttyS0" if arch == "x86_64" else "ttyAMA0"
    init_arg = _init_arg(init)
    if initrd is not None:
        init_arg = "rd" + init_arg
    cmdline = (
        f"console={console} root=/dev/vda {'ro' if read_only else 'rw'} "
        f"rootfstype={rootfs_type} rootwait "
        f"panic=1 {init_arg} loglevel=4"
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
        f"{'q35' if arch == 'x86_64' else 'virt'},accel={accel}",
        "-cpu",
        "host" if accel == "kvm" else "max",
        "-m",
        "256M",
        "-kernel",
        str(kernel),
        "-append",
        cmdline,
        "-drive",
        f"id=rootfs,file={rootfs},format=raw,if=none,readonly={'on' if read_only else 'off'}",
        "-device",
        "virtio-blk-pci,drive=rootfs",
    ]
    if initrd is not None:
        command.extend(["-initrd", str(initrd)])
    # Attach vhost-vsock only when the host exposes a usable /dev/vhost-vsock.
    # On GitHub-hosted runners this node is often absent or inaccessible; in that
    # case omit the device so the guest can still boot under TCG and emit the
    # readiness marker we rely on for the smoke test.
    if host_supports_vhost_vsock():
        command.extend(["-device", f"vhost-vsock-pci,guest-cid={guest_cid}"])
    else:
        if require_vsock:
            raise E2EError("contract boot requires usable /dev/vhost-vsock")
        print(
            "warning: /dev/vhost-vsock not available or not accessible; running without vhost-vsock",
            file=sys.stderr,
        )

    assert_no_network_devices(command)
    return command


def firecracker_config(
    *,
    kernel: Path,
    rootfs: Path,
    vsock_path: Path,
    guest_cid: int,
    rootfs_type: str = "ext2",
    init: str = DEFAULT_INIT,
) -> dict[str, Any]:
    cmdline = (
        f"console=ttyS0 root=/dev/vda rw rootfstype={rootfs_type} rootwait "
        f"panic=1 {_init_arg(init)} loglevel=4 reboot=k pci=off"
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


def write_witness(
    path: Path,
    *,
    backend: str,
    ready_marker: str,
    rootfs_name: str,
    rootfs_type: str,
    vmm_plan: Any,
) -> None:
    """Persist evidence only after the VMM has emitted its readiness marker."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "backend": backend,
                "ready_marker": ready_marker,
                "rootfs_name": rootfs_name,
                "rootfs_type": rootfs_type,
                "vmm_plan": vmm_plan,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


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


def run_until_ready(
    command: list[str], timeout: float, ready_marker: str, *,
    exact_marker: bool = False, refusal_marker: str | None = None,
) -> None:
    assert_no_network_devices(command)
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=0,
    )
    assert process.stdout is not None
    selector = selectors.DefaultSelector()
    deadline = time.monotonic() + timeout
    captured: list[str] = []
    pending = b""
    output_closed = False
    try:
        os.set_blocking(process.stdout.fileno(), False)
        selector.register(process.stdout, selectors.EVENT_READ)
        while time.monotonic() < deadline:
            wait = min(0.25, max(0, deadline - time.monotonic()))
            if output_closed:
                time.sleep(wait)
            else:
                selector.select(timeout=wait)
            # One bounded raw read per turn: no text-wrapper prefetch and no
            # blocking on a missing newline. Drain queued bytes even after exit.
            try:
                chunk = os.read(process.stdout.fileno(), 65536)
            except BlockingIOError:
                chunk = None
            status = process.poll()
            finished = chunk == b"" or (chunk is None and status is not None)
            if chunk:
                pending += chunk
            lines = pending.split(b"\n")
            pending = lines.pop()
            lines = [line + b"\n" for line in lines]
            # A final unterminated line counts at EOF, but never while the
            # child is alive: an exact marker must be a whole serial line.
            if finished and pending:
                lines.append(pending)
                pending = b""
            for raw_line in lines:
                line = raw_line.decode("utf-8", errors="replace")
                sys.stdout.write(line)
                sys.stdout.flush()
                captured.append(line)
                if refusal_marker is not None and refusal_marker in line:
                    raise E2EError(f"guest refused contract: {line.strip()}")
                if (line.rstrip("\r\n") == ready_marker if exact_marker else ready_marker in line):
                    return
            if finished and status is not None:
                raise E2EError(
                    f"VMM exited {status} before {ready_marker!r}\n"
                    + "".join(captured[-80:])
                )
            if finished and not output_closed:
                selector.unregister(process.stdout)
                output_closed = True
        partial = pending.decode("utf-8", errors="replace")
        sys.stdout.write(partial)
        sys.stdout.flush()
        raise E2EError(
            f"timed out after {timeout:.0f}s waiting for {ready_marker!r}\n"
            + "".join(captured[-80:])
            + partial
        )
    finally:
        try:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
        finally:
            selector.close()
            process.stdout.close()


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
            init=args.init,
        )
    return firecracker_config(
        kernel=kernel,
        rootfs=rootfs,
        vsock_path=Path("/tmp/mvm-images-e2e.vsock"),
        guest_cid=args.guest_cid,
        rootfs_type=args.rootfs_type,
        init=args.init,
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
    parser.add_argument(
        "--init",
        default=DEFAULT_INIT,
        help="absolute path of the guest init the smoke image owns",
    )
    parser.add_argument("--ready-marker", default=READY_MARKER)
    parser.add_argument(
        "--witness",
        type=Path,
        help="write successful boot evidence as JSON after observing the ready marker",
    )
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
                    init=args.init,
                )
                run_until_ready(command, args.timeout, args.ready_marker)
                executed_plan: Any = command
            else:
                binary = _require_binary(args.binary or "firecracker")
                config = firecracker_config(
                    kernel=kernel,
                    rootfs=writable_rootfs,
                    vsock_path=temp_path / "vsock.sock",
                    guest_cid=args.guest_cid,
                    rootfs_type=args.rootfs_type,
                    init=args.init,
                )
                config_path = temp_path / "config.json"
                config_path.write_text(json.dumps(config), encoding="utf-8")
                run_until_ready(
                    [binary, "--no-api", "--config-file", str(config_path)],
                    args.timeout,
                    args.ready_marker,
                )
                executed_plan = config
        if args.witness is not None:
            write_witness(
                args.witness,
                backend=args.backend,
                ready_marker=args.ready_marker,
                rootfs_name=args.rootfs_name,
                rootfs_type=args.rootfs_type,
                vmm_plan=executed_plan,
            )
    except (E2EError, OSError) as exc:
        print(f"e2e boot failed: {exc}", file=sys.stderr)
        return 1

    print(f"{args.backend}: reached {args.ready_marker}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
