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


# Host-facing devices the permanent contract forbids in EVERY posture,
# including the in-guest-datapath kernel: that posture re-enables
# cluster-internal adapters (bridge/veth under NETDEVICES) but never a
# device that faces the host or the network.
HOST_FACING_DEVICES = ("VIRTIO_NET", "TUN", "MACVLAN")


def check_resolved_kernel_configs(paths: list[Path]) -> None:
    forbidden = ("NETDEVICES", "VIRTIO_NET", "TUN", "VETH", "BRIDGE", "MACVLAN")
    required = ("VSOCKETS", "VIRTIO_VSOCKETS")
    failures = []
    for path in paths:
        text = path.read_text(encoding="utf-8")
        if "datapath" in path.name:
            check_datapath_resolved_config(path, text, failures)
            continue
        for symbol in forbidden:
            if f"CONFIG_{symbol}=y" in text or f"CONFIG_{symbol}=m" in text:
                failures.append(f"{path}: CONFIG_{symbol} is enabled")
        for symbol in required:
            if f"CONFIG_{symbol}=y" not in text:
                failures.append(f"{path}: CONFIG_{symbol}=y is missing")
    if failures:
        raise ContractError("resolved kernel contract failed:\n  " + "\n  ".join(failures))


def check_datapath_resolved_config(path: Path, text: str, failures: list[str]) -> None:
    """The in-guest-datapath posture: cluster-internal adapters on, host-
    facing devices off, vsock intact. Distinguished from the base contract
    by the config's name (the publisher names it `datapath.config`)."""
    for symbol in ("NET_NS", "NETDEVICES", "BRIDGE", "VETH", "NETFILTER"):
        if f"CONFIG_{symbol}=y" not in text:
            failures.append(f"{path}: datapath config has CONFIG_{symbol} off")
    for symbol in HOST_FACING_DEVICES:
        if f"CONFIG_{symbol}=y" in text or f"CONFIG_{symbol}=m" in text:
            failures.append(f"{path}: datapath config enables CONFIG_{symbol}")
    for symbol in ("VSOCKETS", "VIRTIO_VSOCKETS"):
        if f"CONFIG_{symbol}=y" not in text:
            failures.append(f"{path}: CONFIG_{symbol}=y is missing")


def check_datapath_contract() -> None:
    """`kernel/datapath.nix` is the one posture allowed an in-guest
    datapath. It must request the cluster-internal symbols explicitly and
    must not exempt or enable any host-facing device: the permanent
    no-network-device guarantee is that no guest boots a NIC, TAP, TUN or
    macvlan — a bridge between in-guest namespaces is not that."""
    source = _text("kernel/datapath.nix")
    enables = source.split("extraEnables = [", 1)[1].split("]", 1)[0]
    exemptions = source.split("disableExemptions = [", 1)[1].split("]", 1)[0]
    for symbol in ("NET_NS", "BRIDGE", "VETH", "NETFILTER"):
        if f'"{symbol}"' not in enables:
            raise ContractError(
                f"kernel/datapath.nix does not enable the datapath symbol: {symbol}"
            )
    for symbol in HOST_FACING_DEVICES:
        if f'"{symbol}"' in exemptions:
            raise ContractError(
                f"kernel/datapath.nix exempts a host-facing device: {symbol}"
            )
    # Every exemption must pair with an enable request (the same rule the
    # nix eval enforces) — an arch-dependent no-op exemption is a mistake.
    for symbol in ("BRIDGE", "NETDEVICES", "POSIX_MQUEUE", "VETH"):
        if f'"{symbol}"' not in exemptions:
            raise ContractError(
                f"kernel/datapath.nix lost its datapath exemption: {symbol}"
            )
    for symbol in ("BRIDGE", "NETDEVICES", "VETH"):
        if f'"{symbol}"' not in enables:
            raise ContractError(
                f"kernel/datapath.nix exempts {symbol} without enabling it"
            )


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
        accel="tcg",
        rootfs_type="ext4",
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
        rootfs_type="ext4",
    )
    e2e_boot.assert_no_network_devices(firecracker)
    if "vsock" not in firecracker:
        raise ContractError("Firecracker boot plan has no vsock device")
    if "network-interfaces" in firecracker:
        raise ContractError("Firecracker boot plan contains network interfaces")


def run_all() -> None:
    check_kernel_contract()
    check_datapath_contract()
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
    def kernel_checks() -> None:
        check_kernel_contract()
        check_datapath_contract()

    checks = {
        "kernel": kernel_checks,
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
