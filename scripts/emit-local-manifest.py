#!/usr/bin/env python3
"""Describe a locally built image set in mvm's image-set manifest schema.

Usage:

  scripts/emit-local-manifest.py --mvm-checkout <dir> --arch <aarch64|x86_64> \\
      --builder-cache-contract <n> --out <new dir> \\
      --artifact <role> <format> <path> [--artifact ...] \\
      [--capability <role> <capability> ...] [--images-checkout <dir>]

Images built with `--override-input mvm path:<dir>` come from two working
trees, neither of them a release. This records exactly which: the commit and
working-tree state of the mvm-images checkout (by default the one this script
is in) and of the mvm checkout, next to the digest and size of every artifact,
the guest architecture and the role each artifact plays.

The output is a directory holding a copy of each artifact under its manifest
name and `image-set.json`, in the schema `mvm_core::image_set` parses for a
released set. Only the producer differs, and it is what keeps the two apart:
a release names the repository, workflow and tag that signed it, while this
names only `local_checkouts`. It carries no repository, workflow, tag,
revocation channel, pack hash or SBOM, so nothing in it can be read as a claim
to be a release, and mvm classifies it `local-dev`.

A tree that is not clean is fingerprinted the way mvm fingerprints it — the
status listing, the diff against HEAD and every untracked file — so mvm can
tell whether the checkouts still match the manifest when it reads it. Both
identities are read before the artifacts are copied and again after, and a
change in between is refused rather than attributed to either reading.

Nothing is built here, and nothing is discovered: both checkouts and every
artifact are named by the caller.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

SCHEMA_VERSION = 1
# Semver, as the schema requires, and never a version any release is tagged.
LOCAL_SET_VERSION = "0.0.0-local"
MANIFEST_NAME = "image-set.json"

ARCHES = ("x86_64", "aarch64")

# name -> (schema role, boots directly, published once for every arch)
ROLES = {
    "builder_vm": ("builder_vm", True, False),
    "workload_kernel": ("workload_kernel", True, False),
    "workload_rootfs": ("workload_rootfs", False, False),
    "runtime_overlay": ("runtime_overlay", False, False),
    "sdk_sidecar_glibc": ({"sdk_sidecar": "glibc"}, False, False),
    "sdk_sidecar_musl": ({"sdk_sidecar": "musl"}, False, False),
    "stage0_bootstrap_kernel": ("stage0_bootstrap_kernel", True, False),
    "qemu_wasm_smoke_pack": ("qemu_wasm_smoke_pack", False, True),
}

KERNEL_FORMATS = ("raw", "elf", "image", "image_gz", "image_bz2", "image_zstd", "pe", "pe_gz")
PLAIN_FORMATS = ("ext4", "verity_hash_tree", "verity_root_hash", "tar_gz", "text", "json")
CAPABILITIES = ("virtio_vsock", "virtio_blk", "dm_verity")

# Files every mvm-images checkout carries, as mvm checks them when one is
# selected.
IMAGES_MARKERS = (
    "flake.nix",
    "flake.lock",
    "kernel/flake.nix",
    "images/builder-vm/image.nix",
    "images/default-tenant/image.nix",
    "images/runtime-overlay/image.nix",
    "images/initramfs/image.nix",
)
AGENT_PROTOCOL_SOURCE = "crates/mvm-agentd/src/vsock/mod.rs"
MVM_MARKERS = (
    "Cargo.toml",
    "Cargo.lock",
    "nix/flake.nix",
    "crates/mvm-build/Cargo.toml",
    AGENT_PROTOCOL_SOURCE,
)
FLAKE_LOCKS = ("flake.lock", "kernel/flake.lock")

# Variables that would make `git -C <dir>` answer for another repository.
GIT_REDIRECT_ENV = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_COMMON_DIR",
    "GIT_NAMESPACE",
)

ARTIFACT_NAME = re.compile(r"[A-Za-z0-9._-]{1,255}")


class Refusal(Exception):
    """A reason not to emit a manifest, for the operator."""


def git(root: Path, *args: str) -> bytes:
    env = {k: v for k, v in os.environ.items() if k not in GIT_REDIRECT_ENV}
    env["GIT_OPTIONAL_LOCKS"] = "0"
    proc = subprocess.run(
        ["git", "-C", str(root), *args], env=env, capture_output=True, check=False
    )
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", "replace").strip()
        raise Refusal(f"git {' '.join(args)} in {root} failed: {detail}")
    return proc.stdout


def _section(hasher, label: bytes, body: bytes) -> None:
    # Length-prefixed, so no section's bytes can be read as another's.
    hasher.update(label)
    hasher.update(len(body).to_bytes(8, "little"))
    hasher.update(body)


# The status listing and diff a fingerprint covers, exactly as mvm requests
# them. Every option git configuration could change is spelled out, so two
# hosts with different settings fingerprint one tree the same way.
STATUS_ARGS = (
    "status",
    "--porcelain=v1",
    "-z",
    "--untracked-files=all",
    "--no-renames",
    "--ignore-submodules=none",
)
DIFF_ARGS = (
    "diff",
    "HEAD",
    "--binary",
    "--full-index",
    "--no-ext-diff",
    "--no-textconv",
    "--no-color",
    "--no-renames",
    "--no-relative",
    "--src-prefix=a/",
    "--dst-prefix=b/",
    "--unified=3",
    "--inter-hunk-context=0",
    "--diff-algorithm=myers",
    "--indent-heuristic",
    "--ignore-submodules=none",
    "-O/dev/null",
    "--",
)


def worktree_state(root: Path) -> dict:
    """Clean, or dirty with the fingerprint mvm computes for the same tree."""
    status = git(root, *STATUS_ARGS)
    if not status:
        return {"state": "clean"}
    hasher = hashlib.sha256()
    _section(hasher, b"status", status)
    _section(hasher, b"diff", git(root, *DIFF_ARGS))
    untracked = git(root, "ls-files", "--others", "--exclude-standard", "-z")
    for name in (n for n in untracked.split(b"\0") if n):
        path = os.path.join(os.fsencode(root), name)
        _section(hasher, b"path", name)
        mode = os.lstat(path).st_mode
        if stat.S_ISLNK(mode):
            _section(hasher, b"link", os.readlink(path))
        elif stat.S_ISREG(mode):
            with open(path, "rb") as f:
                _section(hasher, b"file", f.read())
    return {"state": "dirty", "fingerprint": hasher.hexdigest()}


def repo_identity(root: Path) -> dict:
    commit = git(root, "rev-parse", "--verify", "HEAD^{commit}").decode().strip()
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise Refusal(f"{root}: HEAD is {commit!r}, not a full commit id")
    return {"commit": commit, "worktree": worktree_state(root)}


def checkout_root(given: str, what: str, markers: tuple[str, ...]) -> Path:
    """The canonical root of an explicitly named checkout.

    Symlinks are resolved, the result must be the root of its own git work
    tree, and every marker must be a regular file inside it.
    """
    root = Path(os.path.realpath(given))
    if not root.is_dir():
        raise Refusal(f"{what} {given}: not a directory")
    top = git(root, "rev-parse", "--show-toplevel").decode().rstrip("\r\n")
    top = Path(os.path.realpath(top))
    if top != root:
        raise Refusal(f"{what} {root}: not the root of its git checkout (the root is {top})")
    for marker in markers:
        mode = os.lstat(root / marker).st_mode if os.path.lexists(root / marker) else None
        if mode is None or not stat.S_ISREG(mode):
            raise Refusal(f"{what} {root}: not an {what} checkout (no regular file {marker})")
    return root


def guest_agent_protocol(mvm: Path) -> dict:
    """The protocol range of the guest agent compiled from this mvm checkout."""
    source = (mvm / AGENT_PROTOCOL_SOURCE).read_text()

    def constant(name: str) -> int:
        found = re.findall(rf"^pub const {name}: u32 = (\d+);$", source, re.MULTILINE)
        if len(found) != 1:
            raise Refusal(
                f"{mvm / AGENT_PROTOCOL_SOURCE}: expected one `pub const {name}: u32`, "
                f"found {len(found)}"
            )
        return int(found[0])

    low = constant("MIN_SUPPORTED_PROTOCOL_VERSION")
    high = constant("PROTOCOL_VERSION")
    if low < 1 or low > high:
        raise Refusal(f"guest agent protocol range {low}..={high} is not a valid range")
    return {"min": low, "max": high}


def artifact_format(spec: str):
    if spec.startswith("kernel:"):
        kernel = spec.removeprefix("kernel:")
        if kernel in KERNEL_FORMATS:
            return {"kernel": kernel}
    elif spec in PLAIN_FORMATS:
        return spec
    choices = ", ".join([f"kernel:{k}" for k in KERNEL_FORMATS] + list(PLAIN_FORMATS))
    raise Refusal(f"unknown artifact format {spec!r} (one of {choices})")


def artifact_name(role: str, arch: str, given: str) -> str:
    stem = role.replace("_", "-")
    prefix = stem if ROLES[role][2] else f"{stem}-{arch}"
    name = f"{prefix}-{os.path.basename(given)}"
    if not ARTIFACT_NAME.fullmatch(name):
        raise Refusal(f"{given}: {name!r} is not a usable artifact name")
    return name


def regular_file(given: str) -> Path:
    """The file an artifact path names. Links are followed here, deliberately:
    a Nix output is mostly links into the store, and what is recorded is the
    copy, which is always a regular file."""
    resolved = Path(os.path.realpath(given))
    try:
        mode = os.stat(resolved).st_mode
    except FileNotFoundError:
        raise Refusal(f"{given}: no such file") from None
    if not stat.S_ISREG(mode):
        raise Refusal(f"{given}: not a regular file")
    return resolved


def copy_and_hash(source: Path, dest: Path) -> tuple[str, int]:
    hasher = hashlib.sha256()
    size = 0
    with open(source, "rb") as src, open(dest, "xb") as out:
        while chunk := src.read(1 << 20):
            hasher.update(chunk)
            out.write(chunk)
            size += len(chunk)
    os.chmod(dest, 0o644)
    return hasher.hexdigest(), size


def sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            hasher.update(chunk)
    return hasher.hexdigest()


def is_within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def output_dir(given: str, checkouts: list[Path]) -> Path:
    """Where the set goes: a new directory outside both checkouts, since
    writing into either would change the identity being recorded."""
    out = Path(os.path.realpath(given))
    for root in checkouts:
        if is_within(out, root):
            raise Refusal(f"--out {out} is inside the checkout {root}")
    if os.path.lexists(out) and (not out.is_dir() or any(out.iterdir())):
        raise Refusal(f"--out {out} already exists")
    if not out.parent.is_dir():
        raise Refusal(f"--out {out}: {out.parent} is not a directory")
    return out


def issued_at() -> str:
    epoch = os.environ.get("SOURCE_DATE_EPOCH")
    moment = (
        datetime.datetime.fromtimestamp(int(epoch), datetime.timezone.utc)
        if epoch
        else datetime.datetime.now(datetime.timezone.utc)
    )
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def build_members(args, staging: Path) -> list[dict]:
    members: dict[str, dict] = {}
    names: set[str] = set()
    for role, fmt, given in args.artifact:
        if role not in ROLES:
            raise Refusal(f"unknown role {role!r} (one of {', '.join(ROLES)})")
        schema_role, bootable, independent = ROLES[role]
        name = artifact_name(role, args.arch, given)
        if name in names:
            raise Refusal(f"two artifacts would both be named {name}")
        names.add(name)
        fmt_value = artifact_format(fmt)
        digest, size = copy_and_hash(regular_file(given), staging / name)
        if size == 0:
            raise Refusal(f"{given}: empty; the schema refuses a zero-size artifact")
        member = members.setdefault(
            role,
            {
                "role": schema_role,
                "target": "arch_independent" if independent else {"arch": args.arch},
                **({"boot_protocol": "linux_direct"} if bootable else {}),
                "artifacts": [],
                "required_capabilities": [],
            },
        )
        member["artifacts"].append(
            {"name": name, "format": fmt_value, "sha256": digest, "size": size}
        )
    for role, capability in args.capability:
        if role not in members:
            raise Refusal(f"--capability names {role!r}, which has no --artifact")
        if capability not in CAPABILITIES:
            raise Refusal(
                f"unknown capability {capability!r} (one of {', '.join(CAPABILITIES)})"
            )
        caps = members[role]["required_capabilities"]
        if capability not in caps:
            caps.append(capability)
    return list(members.values())


def emit(args) -> Path:
    images_given = args.images_checkout or str(Path(__file__).resolve().parent.parent)
    images = checkout_root(images_given, "mvm-images", IMAGES_MARKERS)
    mvm = checkout_root(args.mvm_checkout, "mvm", MVM_MARKERS)
    if args.builder_cache_contract < 1:
        raise Refusal("--builder-cache-contract must be a positive integer")
    if not args.artifact:
        raise Refusal("no --artifact given; a set has at least one member")
    out = output_dir(args.out, [images, mvm])

    before = {"images": repo_identity(images), "mvm": repo_identity(mvm)}
    protocol = guest_agent_protocol(mvm)
    locks = [
        {"reference": f"mvm-images:{lock}", "lock_hash": sha256_file(images / lock)}
        for lock in FLAKE_LOCKS
    ]

    staging = Path(tempfile.mkdtemp(prefix=f".{out.name}.", dir=out.parent))
    try:
        members = build_members(args, staging)
        after = {"images": repo_identity(images), "mvm": repo_identity(mvm)}
        if after != before:
            raise Refusal(
                "a checkout changed while the set was being recorded; "
                f"was {json.dumps(before)}, now {json.dumps(after)}"
            )
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "set_version": LOCAL_SET_VERSION,
            "issued_at": issued_at(),
            "producer": {"local_checkouts": before},
            "mvm_source_commit": before["mvm"]["commit"],
            "compatibility": {
                "guest_agent_protocol": protocol,
                "builder_cache_contract": args.builder_cache_contract,
            },
            "nix_inputs": {"flake_locks": locks, "source_revisions": []},
            "members": members,
        }
        (staging / MANIFEST_NAME).write_text(json.dumps(manifest, indent=2) + "\n")
        if out.is_dir():
            out.rmdir()
        os.rename(staging, out)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return out / MANIFEST_NAME


def parse_args(argv: list[str]):
    parser = argparse.ArgumentParser(
        description="Describe a locally built image set in mvm's manifest schema."
    )
    parser.add_argument("--mvm-checkout", required=True, help="the local mvm checkout")
    parser.add_argument(
        "--images-checkout",
        help="the mvm-images checkout (default: the one this script is in)",
    )
    parser.add_argument("--arch", required=True, choices=ARCHES, help="guest architecture")
    parser.add_argument(
        "--builder-cache-contract",
        required=True,
        type=int,
        help="the builder image cache contract the paired mvm host expects",
    )
    parser.add_argument("--out", required=True, help="new directory for the set")
    parser.add_argument(
        "--artifact",
        nargs=3,
        action="append",
        default=[],
        metavar=("ROLE", "FORMAT", "PATH"),
        help="one built file of one role; repeat for every file",
    )
    parser.add_argument(
        "--capability",
        nargs=2,
        action="append",
        default=[],
        metavar=("ROLE", "CAPABILITY"),
        help="a guest device the role needs; repeat as required",
    )
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    try:
        print(emit(args))
    except Refusal as refusal:
        print(f"emit-local-manifest: {refusal}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
