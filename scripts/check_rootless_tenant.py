#!/usr/bin/env python3
"""Check the generic rootless-tenant source contract without running mvm."""

from __future__ import annotations

import argparse
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ContractError(RuntimeError):
    pass


def text(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def require(source: str, path: str, tokens: tuple[str, ...]) -> None:
    missing = [token for token in tokens if token not in source]
    if missing:
        raise ContractError(f"{path} is missing: {', '.join(missing)}")


def check_rootless_contract() -> None:
    flake = text("flake.nix")
    wrapper = text("images/rootless-tenant/image.nix")
    tenant = text("images/default-tenant/image.nix")
    kernel = text("kernel/workload.nix")
    named_kernel = text("kernel/rootless.nix")

    require(
        flake,
        "flake.nix",
        ("rootlessTenant", "images/rootless-tenant/image.nix", "rootless-tenant"),
    )
    require(wrapper, "images/rootless-tenant/image.nix", ("rootless = true",))
    require(
        tenant,
        "images/default-tenant/image.nix",
        (
            "pkgs.crun",
            "pkgs.fuse-overlayfs",
            "MVM-ROOTLESS-READY",
            "test ! -e /dev/net/tun",
            "/sys/class/net/*",
            "unshare -Urmpf /bin/true",
            "/sys/fs/cgroup/mvm-workload",
        ),
    )

    required_symbols = (
        "NAMESPACES",
        "UTS_NS",
        "IPC_NS",
        "USER_NS",
        "PID_NS",
        "CGROUPS",
        "MEMCG",
        "BLK_CGROUP",
        "CGROUP_SCHED",
        "CGROUP_PIDS",
        "CPUSETS",
        "UNIX98_PTYS",
        "INOTIFY_USER",
        "FANOTIFY",
    )
    rootless_block = kernel.split("pkgs.lib.optionals rootless [", 1)[1].split("]", 1)[0]
    missing_symbols = [s for s in required_symbols if f'"{s}"' not in rootless_block]
    if missing_symbols:
        raise ContractError(
            "kernel/workload.nix rootless delta is missing: "
            + ", ".join(missing_symbols)
        )
    if '"NET_NS"' in rootless_block:
        raise ContractError("rootless kernel must not enable CONFIG_NET_NS")

    require(named_kernel, "kernel/rootless.nix", ("rootless = true",))
    implementation = wrapper.lower() + named_kernel.lower()
    forbidden = ("kubernetes", "k8s", "k3s", "cni")
    found = [token for token in forbidden if token in implementation]
    if found:
        raise ContractError(
            "rootless implementation contains consumer-specific naming: "
            + ", ".join(found)
        )


def check_resolved_kernel_config(path: Path) -> None:
    source = path.read_text(encoding="utf-8")
    required = (
        "NAMESPACES",
        "UTS_NS",
        "IPC_NS",
        "USER_NS",
        "PID_NS",
        "CGROUPS",
        "MEMCG",
        "BLK_CGROUP",
        "CGROUP_SCHED",
        "CGROUP_PIDS",
        "CPUSETS",
        "UNIX98_PTYS",
        "INOTIFY_USER",
        "FANOTIFY",
        "VSOCKETS",
        "VIRTIO_VSOCKETS",
    )
    failures = [symbol for symbol in required if f"CONFIG_{symbol}=y" not in source]
    if "CONFIG_NET_NS=y" in source or "CONFIG_NET_NS=m" in source:
        failures.append("NET_NS must be disabled")
    if failures:
        raise ContractError(
            f"{path}: resolved rootless kernel contract failed: "
            + ", ".join(failures)
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kernel-config", type=Path)
    args = parser.parse_args()
    try:
        check_rootless_contract()
        if args.kernel_config:
            check_resolved_kernel_config(args.kernel_config)
    except ContractError as exc:
        print(f"rootless-tenant contract failed: {exc}")
        return 1
    print("rootless-tenant contract: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
