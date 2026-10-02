#!/usr/bin/env python3
"""Verify that the current user can open and use the Linux KVM device."""

import argparse
import fcntl
import os
import sys


KVM_GET_API_VERSION = 0xAE00
EXPECTED_KVM_API_VERSION = 12


def probe_kvm(path: str = "/dev/kvm") -> None:
    """Raise OSError when KVM cannot be opened, and RuntimeError when unusable."""
    fd = os.open(path, os.O_RDWR | os.O_CLOEXEC)
    try:
        version = fcntl.ioctl(fd, KVM_GET_API_VERSION)
    finally:
        os.close(fd)
    if version != EXPECTED_KVM_API_VERSION:
        raise RuntimeError(
            f"KVM_GET_API_VERSION returned {version}, expected {EXPECTED_KVM_API_VERSION}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", default="/dev/kvm")
    args = parser.parse_args()

    try:
        probe_kvm(args.device)
    except (OSError, RuntimeError) as error:
        print(f"KVM is inaccessible to the current user: {error}", file=sys.stderr)
        return 1
    print(f"KVM is accessible through {args.device}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
