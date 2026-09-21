#!/usr/bin/env python3
"""Enforce the permanent no-network-device image contract.

This check is deliberately repository-local. It inspects the image and launch
definitions that this repository owns and never reads or executes a sibling
`mvm` checkout.
"""

from __future__ import annotations

import argparse
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ContractError(RuntimeError):
    pass


def _text(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def check_kernel_contract() -> None:
    source = _text("kernel/base.nix")
    required = ("NETDEVICES", "VIRTIO_NET", "TUN", "VETH", "BRIDGE", "MACVLAN")
    missing = [symbol for symbol in required if f'"{symbol}"' not in source]
    if missing:
        raise ContractError(
            "kernel/base.nix does not explicitly disable: " + ", ".join(missing)
        )

    enables = source.split("baseEnables = [", 1)[1].split("]", 1)[0]
    enabled = [symbol for symbol in required if f'"{symbol}"' in enables]
    if enabled:
        raise ContractError(
            "kernel/base.nix enables forbidden network devices: " + ", ".join(enabled)
        )


def check_resolved_kernel_configs(paths: list[Path]) -> None:
    forbidden = ("NETDEVICES", "VIRTIO_NET", "TUN", "VETH", "BRIDGE", "MACVLAN")
    required = ("VSOCKETS", "VIRTIO_VSOCKETS")
    failures = []
    for path in paths:
        text = path.read_text(encoding="utf-8")
        for symbol in forbidden:
            if f"CONFIG_{symbol}=y" in text or f"CONFIG_{symbol}=m" in text:
                failures.append(f"{path}: CONFIG_{symbol} is enabled")
        for symbol in required:
            if f"CONFIG_{symbol}=y" not in text:
                failures.append(f"{path}: CONFIG_{symbol}=y is missing")
    if failures:
        raise ContractError("resolved kernel contract failed:\n  " + "\n  ".join(failures))


def check_qemu_wasm_contract() -> None:
    files = {
        "qemu-wasm/qemu-wasm.nix": _text("qemu-wasm/qemu-wasm.nix"),
        "qemu-wasm/qemu-wasm-smoke-image.nix": _text(
            "qemu-wasm/qemu-wasm-smoke-image.nix"
        ),
        "qemu-wasm/qemu-wasm-smoke-pack.nix": _text(
            "qemu-wasm/qemu-wasm-smoke-pack.nix"
        ),
    }
    forbidden = {
        "qemu-wasm/qemu-wasm.nix": ("--enable-slirp", "libslirp", "slirpSrc"),
        "qemu-wasm/qemu-wasm-smoke-image.nix": (
            '"NETDEVICES"',
            "eth0",
            "10.0.2.",
        ),
        "qemu-wasm/qemu-wasm-smoke-pack.nix": (
            "-netdev",
            "virtio-net",
            "-nic",
            "slirp",
        ),
    }
    violations = []
    for path, tokens in forbidden.items():
        for token in tokens:
            if token.lower() in files[path].lower():
                violations.append(f"{path}: {token}")
    if violations:
        raise ContractError(
            "QEMU-Wasm contains forbidden guest networking:\n  "
            + "\n  ".join(violations)
        )
    pack = files["qemu-wasm/qemu-wasm-smoke-pack.nix"]
    for required in ("-nodefaults", "-no-user-config", "-serial"):
        if required not in pack:
            raise ContractError(f"QEMU-Wasm launch plan is missing {required}")


def check_e2e_harness_contract() -> None:
    # Import locally so this standalone checker remains useful while a broken
    # harness is being diagnosed.
    import e2e_boot

    qemu = e2e_boot.qemu_command(
        binary="qemu-system-x86_64",
        kernel=Path("/artifacts/kernel.img"),
        rootfs=Path("/artifacts/rootfs.bin"),
        guest_cid=7,
    )
    e2e_boot.assert_no_network_devices(qemu)
    if "-nodefaults" not in qemu:
        raise ContractError("QEMU boot plan does not suppress implicit devices")
    if not any("vsock" in arg for arg in qemu):
        raise ContractError("QEMU boot plan has no vsock device")

    firecracker = e2e_boot.firecracker_config(
        kernel=Path("/artifacts/vmlinux"),
        rootfs=Path("/artifacts/rootfs.bin"),
        vsock_path=Path("/run/mvm-images-e2e.vsock"),
        guest_cid=7,
    )
    e2e_boot.assert_no_network_devices(firecracker)
    if "vsock" not in firecracker:
        raise ContractError("Firecracker boot plan has no vsock device")
    if "network-interfaces" in firecracker:
        raise ContractError("Firecracker boot plan contains network interfaces")


def run_all() -> None:
    check_kernel_contract()
    check_qemu_wasm_contract()
    check_e2e_harness_contract()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "section",
        nargs="?",
        choices=("kernel", "qemu-wasm", "e2e"),
        help="check one section instead of the complete contract",
    )
    parser.add_argument(
        "--kernel-config",
        action="append",
        type=Path,
        default=[],
        help="also verify a resolved Linux .config (repeatable)",
    )
    args = parser.parse_args()
    checks = {
        "kernel": check_kernel_contract,
        "qemu-wasm": check_qemu_wasm_contract,
        "e2e": check_e2e_harness_contract,
    }
    try:
        checks[args.section]() if args.section else run_all()
        if args.kernel_config:
            check_resolved_kernel_configs(args.kernel_config)
    except ContractError as exc:
        print(f"no-network-device contract failed: {exc}")
        return 1
    print("no-network-device contract: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
