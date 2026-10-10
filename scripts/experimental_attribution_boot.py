#!/usr/bin/env python3
"""Bounded, direct QEMU runner for the partial kernel hook viability probe."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

PASS = "ATTRIBUTION-PROBE:PASS:connect4-connect6-file_receive-partial"
LIFECYCLE_PASS = "ATTRIBUTION-PROBE:PASS:socket-generation-actual-use-revocation-partial"
BRIDGE_PASS = "ATTRIBUTION-PROBE:PASS:egress-label-bridge-development-only"
BRIDGE_CAPACITY = "ATTRIBUTION-PROBE:CAPACITY:2:boot-local-tombstones:no-reuse:exhaustion-closed"
BOOTSTRAP_CAPS = "ATTRIBUTION-PROBE:BOOTSTRAP-CAPS:NET_ADMIN12,PERFMON38,BPF39:no-SYS_ADMIN"
EXEC_PASS = "ATTRIBUTION-PROBE:PASS:native-ELF-task-storage-one-shot-exec-admission-partial"
EXEC_HOOKS = "ATTRIBUTION-PROBE:EXEC-HOOKS:19:allow=1:deny=18:PT_INTERP-native=1"
EXEC_UNSUPPORTED = (
    "ATTRIBUTION-PROBE:UNSUPPORTED:exec-byte-attestation,script-chains,"
    "production-exec-decision,concurrent-exec-revocation"
)
SERVER_PASS = "ATTRIBUTION-PROBE:PASS:accepted-server-fexit-generation-bidirectional-private-partial"
SERVER_HOOKS = (
    "ATTRIBUTION-PROBE:SERVER-HOOKS:bind=12:bind-deny=4:accept=4:"
    "accept-deny=8:send=6:send-deny=6"
)
SERVER_UNSUPPORTED = (
    "ATTRIBUTION-PROBE:UNSUPPORTED:normal1080-server-SEND,production-server-handler,"
    "host-FlowMux,concurrent-server-revocation,production-port-reuse"
)
BRIDGE_UNSUPPORTED = (
    "ATTRIBUTION-PROBE:UNSUPPORTED:production-slot-reuse,host-FlowMux,"
    "exec-identity,snapshot-restore,production-loader"
)
UNSUPPORTED = (
    "ATTRIBUTION-PROBE:UNSUPPORTED:production-egress-bridge,claim-protocol,"
    "verifier-faults,exec-identity,concurrent-teardown,non-TCP"
)


def command(arch: str, kernel: Path, rootfs: Path, accel: str) -> list[str]:
    if arch not in ("x86_64", "aarch64") or accel not in ("kvm", "tcg"):
        raise ValueError("unsupported architecture or accelerator")
    console = "ttyS0" if arch == "x86_64" else "ttyAMA0"
    machine = "q35" if arch == "x86_64" else "virt"
    return [
        f"qemu-system-{arch}", "-nodefaults", "-no-user-config", "-no-reboot",
        "-display", "none", "-monitor", "none", "-serial", "stdio",
        "-net", "none", "-machine", f"{machine},accel={accel}",
        "-cpu", "host" if accel == "kvm" else "max", "-m", "512M", "-smp", "1",
        "-kernel", str(kernel),
        "-append", f"console={console} root=/dev/vda ro rootfstype=ext4 "
                   "rootwait init=/init panic=1 loglevel=4",
        "-drive", f"id=probe,file={rootfs},format=raw,if=none,readonly=on",
        "-device", "virtio-blk-pci,drive=probe",
        "-device", "vhost-vsock-pci,guest-cid=9042",
    ]


def validate_output(output: str, returncode: int) -> None:
    lines = output.replace("\r", "").splitlines()
    if returncode != 0 or "ATTRIBUTION-PROBE:FAIL:" in output:
        raise ValueError(f"guest/VMM failed (exit {returncode})")
    markers = (PASS, LIFECYCLE_PASS, BRIDGE_PASS, BRIDGE_CAPACITY,
               BOOTSTRAP_CAPS, BRIDGE_UNSUPPORTED, UNSUPPORTED,
               EXEC_PASS, EXEC_HOOKS, EXEC_UNSUPPORTED,
               SERVER_PASS, SERVER_HOOKS, SERVER_UNSUPPORTED)
    if any(lines.count(marker) != 1 for marker in markers):
        raise ValueError("missing/duplicate exact partial-success and unsupported markers")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arch", required=True, choices=("x86_64", "aarch64"))
    parser.add_argument("--kernel", required=True, type=Path)
    parser.add_argument("--rootfs", required=True, type=Path)
    parser.add_argument("--accel", choices=("kvm", "tcg"), default="kvm")
    parser.add_argument("--timeout", type=int, default=90)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.timeout <= 300:
        parser.error("--timeout must be between 1 and 300 seconds")
    argv = command(args.arch, args.kernel.resolve(), args.rootfs.resolve(), args.accel)
    if args.dry_run:
        print(json.dumps(argv))
        return 0
    try:
        if sys.platform != "linux":
            raise ValueError("boot requires Linux; no platform fallback")
        for path in (args.kernel, args.rootfs):
            if not path.is_file():
                raise ValueError(f"missing artifact: {path}")
        # Vsock is mandatory. Never silently omit it or add a network device.
        with open("/dev/vhost-vsock", "rb+"):
            pass
        result = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, errors="replace", timeout=args.timeout)
        print(result.stdout, end="")
        validate_output(result.stdout, result.returncode)
    except subprocess.TimeoutExpired as exc:
        output = exc.stdout or b""
        print(output.decode(errors="replace") if isinstance(output, bytes) else output,
              end="")
        print("ATTRIBUTION-HOST:FAIL:timeout", file=sys.stderr)
        return 1
    except (OSError, ValueError) as exc:
        print(f"ATTRIBUTION-HOST:FAIL:{exc}", file=sys.stderr)
        return 1
    print("ATTRIBUTION-HOST:PASS:partial-hook-viability-only")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
