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
    rootless_image = text("images/rootless-tenant/image.nix")
    default_image = text("images/default-tenant/image.nix")
    rootless_kernel = text("kernel/rootless.nix")
    default_kernel = text("kernel/workload.nix")
    e2e_harness = text("scripts/e2e_boot.py")
    build_workflow = text(".github/workflows/build.yml")

    require(
        flake,
        "flake.nix",
        ("rootlessTenant", "images/rootless-tenant/image.nix", "rootless-tenant"),
    )
    require(
        rootless_image,
        "images/rootless-tenant/image.nix",
        (
            "../../kernel/rootless.nix",
            "pkgs.crun",
            "pkgs.fuse-overlayfs",
            "MVM-ROOTLESS-READY",
            "test ! -e /dev/net/tun",
            "/sys/class/net/*",
            "unshare -Urmpf /bin/true",
            "/sys/fs/cgroup/mvm-workload",
        ),
    )
    leaked_default_tokens = [
        token
        for token in ("pkgs.crun", "pkgs.fuse-overlayfs", "MVM-ROOTLESS-READY")
        if token in default_image
    ]
    if leaked_default_tokens:
        raise ContractError(
            "default-tenant must remain independent of rootless tooling: "
            + ", ".join(leaked_default_tokens)
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
    missing_symbols = [s for s in required_symbols if f'"{s}"' not in rootless_kernel]
    if missing_symbols:
        raise ContractError(
            "kernel/rootless.nix is missing: "
            + ", ".join(missing_symbols)
        )
    require(rootless_kernel, "kernel/rootless.nix", ('requiredExtraDisables = [ "NET_NS" ];',))
    require(
        e2e_harness,
        "scripts/e2e_boot.py",
        (
            "mvm.runtime_data=/dev/vdb",
            "mvm.runtime_source_policy=required_overlay",
            '"is_read_only": True',
        ),
    )
    require(
        build_workflow,
        ".github/workflows/build.yml",
        ("runtime-overlay.default", "--runtime-overlay"),
    )
    for symbol in ("NAMESPACES", "CGROUPS"):
        if f'"{symbol}"' not in default_kernel:
            raise ContractError(
                f"kernel/workload.nix must retain the default {symbol} cut"
            )

    implementation = rootless_image.lower() + rootless_kernel.lower()
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
