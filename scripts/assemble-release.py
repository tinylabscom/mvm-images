#!/usr/bin/env python3
"""Assemble one complete, consumer-verifiable mvm image-set release."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path


REPOSITORY = "tinylabscom/mvm-images"
WORKFLOW = ".github/workflows/release.yml"
ZERO_SHA256 = "0" * 64
ARCHES = ("x86_64", "aarch64")


class Refusal(RuntimeError):
    """A candidate cannot be published without violating the release contract."""


@dataclass(frozen=True)
class Artifact:
    name: str
    format: object


@dataclass(frozen=True)
class Member:
    slug: str
    role: object
    target: object
    artifacts: tuple[Artifact, ...]
    capabilities: tuple[str, ...] = ()
    bootable: bool = False


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def compact_json(value: object) -> bytes:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=True).encode()


def member_specs() -> tuple[Member, ...]:
    members: list[Member] = []
    for arch in ARCHES:
        kernel = {"kernel": "elf" if arch == "x86_64" else "image"}
        target = {"arch": arch}
        members.extend(
            [
                Member(
                    f"builder-vm-{arch}",
                    "builder_vm",
                    target,
                    (
                        Artifact(f"builder-vm-vmlinux-{arch}", kernel),
                        Artifact(f"builder-vm-rootfs-{arch}.ext4", "ext4"),
                    ),
                    ("virtio_vsock", "virtio_blk"),
                    True,
                ),
                Member(
                    f"default-workload-kernel-{arch}",
                    {"workload_kernel": "default_tenant"},
                    target,
                    (Artifact(f"default-microvm-vmlinux-{arch}", kernel),),
                    ("virtio_vsock",),
                    True,
                ),
                Member(
                    f"default-workload-rootfs-{arch}",
                    {"workload_rootfs": "default_tenant"},
                    target,
                    (
                        Artifact(f"default-microvm-rootfs-{arch}.ext4", "ext4"),
                        Artifact(
                            f"default-microvm-rootfs-{arch}.verity", "verity_hash_tree"
                        ),
                        Artifact(
                            f"default-microvm-rootfs-{arch}.roothash", "verity_root_hash"
                        ),
                    ),
                    ("virtio_blk", "dm_verity"),
                ),
                Member(
                    f"rootless-workload-kernel-{arch}",
                    {"workload_kernel": "rootless_tenant"},
                    target,
                    (Artifact(f"rootless-microvm-vmlinux-{arch}", kernel),),
                    ("virtio_vsock",),
                    True,
                ),
                Member(
                    f"rootless-workload-rootfs-{arch}",
                    {"workload_rootfs": "rootless_tenant"},
                    target,
                    (
                        Artifact(f"rootless-microvm-rootfs-{arch}.ext4", "ext4"),
                        Artifact(
                            f"rootless-microvm-rootfs-{arch}.verity", "verity_hash_tree"
                        ),
                        Artifact(
                            f"rootless-microvm-rootfs-{arch}.roothash", "verity_root_hash"
                        ),
                    ),
                    ("virtio_blk", "dm_verity"),
                ),
                Member(
                    f"runtime-overlay-{arch}",
                    "runtime_overlay",
                    target,
                    (Artifact(f"runtime-overlay-{arch}.tar.gz", "tar_gz"),),
                ),
                Member(
                    f"sdk-sidecar-{arch}-glibc",
                    {"sdk_sidecar": "glibc"},
                    target,
                    (Artifact(f"sdk-sidecar-{arch}-glibc.tar.gz", "tar_gz"),),
                ),
                Member(
                    f"sdk-sidecar-{arch}-musl",
                    {"sdk_sidecar": "musl"},
                    target,
                    (Artifact(f"sdk-sidecar-{arch}-musl.tar.gz", "tar_gz"),),
                ),
                Member(
                    f"initramfs-{arch}",
                    "initramfs",
                    target,
                    (Artifact(f"initramfs-{arch}.tar.gz", "tar_gz"),),
                ),
                Member(
                    f"stage0-bootstrap-kernel-{arch}",
                    "stage0_bootstrap_kernel",
                    target,
                    (Artifact(f"stage0-vmlinux-{arch}", kernel),),
                    ("virtio_vsock",),
                    True,
                ),
            ]
        )
    members.append(
        Member(
            "qemu-wasm-smoke-pack",
            "qemu_wasm_smoke_pack",
            "arch_independent",
            (Artifact("qemu-wasm-smoke-pack.tar.gz", "tar_gz"),),
        )
    )
    return tuple(members)


def parse_protocols(mvm_source: Path) -> tuple[int, int, int]:
    agent = (mvm_source / "crates/mvm-agentd/src/vsock/mod.rs").read_text()
    builder = (mvm_source / "crates/mvm-build/src/builder_vm.rs").read_text()

    def one(source: str, pattern: str, what: str) -> int:
        matches = re.findall(pattern, source, re.MULTILINE)
        if len(matches) != 1:
            raise Refusal(f"expected exactly one {what} constant, found {len(matches)}")
        return int(matches[0])

    low = one(
        agent,
        r"^pub const MIN_SUPPORTED_PROTOCOL_VERSION: u32 = (\d+);$",
        "minimum guest-agent protocol",
    )
    high = one(
        agent,
        r"^pub const PROTOCOL_VERSION: u32 = (\d+);$",
        "current guest-agent protocol",
    )
    cache = one(
        builder,
        r"^pub const BUILDER_VM_CACHE_CONTRACT_VERSION: u32 = (\d+);$",
        "builder cache contract",
    )
    if low < 1 or low > high:
        raise Refusal(f"invalid guest-agent protocol range {low}..={high}")
    return low, high, cache


def builder_boot_abi(images_root: Path) -> int:
    """The builder boot ABI this repository builds its builder image to.

    Read from `images/builder-vm/boot-abi.nix` rather than written out here, so
    the image, the release assembly and the local emitter cannot disagree about
    how a consumer must supply PID 1.
    """
    path = images_root / "images" / "builder-vm" / "boot-abi.nix"
    try:
        text = path.read_text()
    except OSError as exc:
        raise Refusal(f"cannot read the builder boot ABI at {path}: {exc}") from exc
    values = re.findall(r"^\s*(\d+)\s*$", text, re.MULTILINE)
    if len(values) != 1:
        raise Refusal(
            f"{path} must hold exactly one bare integer, found {len(values)}"
        )
    return int(values[0])


def pinned_mvm_commit(lock_path: Path) -> str:
    lock = json.loads(lock_path.read_text())
    try:
        revision = lock["nodes"]["mvm"]["locked"]["rev"]
    except (KeyError, TypeError) as error:
        raise Refusal(f"{lock_path}: no locked mvm revision") from error
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise Refusal(f"{lock_path}: mvm revision is not a full commit")
    return revision


def require_file(path: Path) -> None:
    if not path.is_file():
        raise Refusal(f"required release artifact is missing: {path.name}")
    if path.stat().st_size == 0:
        raise Refusal(f"required release artifact is empty: {path.name}")


def ensure_stage0_aliases(assets: Path) -> None:
    for arch in ARCHES:
        source = assets / f"vmlinux-{arch}-builder"
        destination = assets / f"stage0-vmlinux-{arch}"
        require_file(source)
        if destination.exists() and sha256_file(destination) != sha256_file(source):
            raise Refusal(f"{destination.name} does not match {source.name}")
        if not destination.exists():
            shutil.copyfile(source, destination)


def sbom_for(member: Member, assets: Path, issued_at: str, tag: str) -> tuple[str, str]:
    name = f"sbom-{member.slug}.spdx.json"
    files = []
    relationships = []
    for index, artifact in enumerate(member.artifacts, start=1):
        path = assets / artifact.name
        file_id = f"SPDXRef-File-{index}"
        files.append(
            {
                "fileName": artifact.name,
                "SPDXID": file_id,
                "checksums": [{"algorithm": "SHA256", "checksumValue": sha256_file(path)}],
            }
        )
        relationships.append(
            {
                "spdxElementId": "SPDXRef-DOCUMENT",
                "relationshipType": "DESCRIBES",
                "relatedSpdxElement": file_id,
            }
        )
    document = {
        "spdxVersion": "SPDX-2.3",
        "dataLicense": "CC0-1.0",
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": f"mvm-images-{member.slug}",
        "documentNamespace": (
            f"https://github.com/{REPOSITORY}/releases/download/{tag}/{name}"
        ),
        "creationInfo": {"created": issued_at, "creators": [f"Tool: {WORKFLOW}"]},
        "files": files,
        "relationships": relationships,
    }
    path = assets / name
    path.write_bytes(compact_json(document))
    return name, sha256_file(path)


def pack_manifest(
    member: Member,
    assets: Path,
    issued_at: str,
    tag: str,
    flake_hash: str,
    sbom_name: str,
    sbom_hash: str,
) -> tuple[str, str]:
    arch = member.target["arch"] if isinstance(member.target, dict) else "x86_64"
    identity = f"https://github.com/{REPOSITORY}/{WORKFLOW}@refs/tags/{tag}"
    files = [
        {
            "path": artifact.name,
            "sha256": sha256_file(assets / artifact.name),
            "size_bytes": (assets / artifact.name).stat().st_size,
        }
        for artifact in member.artifacts
    ]
    root_hash = files[0]["sha256"]
    outputs = {
        "pack_hash": ZERO_SHA256,
        "files": files,
        "rootfs_hash": root_hash,
    }
    manifest = {
        "schema_version": 1,
        "kind": "image_project",
        "target_arch": arch,
        "backend_compatibility": ["firecracker", "libkrun", "qemu", "hvf"],
        "required_host_capabilities": [],
        "policy_compatibility": {
            "policy_hash": sha256_bytes(f"{arch}-linux".encode()),
            "local_rebuild_required": False,
            "allowed_channels": ["stable"],
        },
        "inputs": {
            "flake_locks": [{"reference": ".", "lock_hash": flake_hash}],
            "derivations": [],
            "nar_hashes": [],
            "oci_images": [],
            "setup_commands": [],
            "source_revisions": [],
            "toolchain_versions": {},
        },
        "outputs": outputs,
        "provenance": {
            "builder_identity": identity,
            "build_environment_identity": "github-hosted",
            "build_timestamp": issued_at,
            "reproducibility": "not_checked",
            "sbom": {
                "uri": f"https://github.com/{REPOSITORY}/releases/download/{tag}/{sbom_name}",
                "sha256": sbom_hash,
            },
            "signature_bundle": {
                "format": "sigstore",
                "payload": "manifest_v1",
                "signatures": [],
            },
        },
        "trust": {
            "signing_key_id": sha256_bytes(identity.encode())[:32],
            "expires_at": "2099-12-31T23:59:59Z",
            "revocation_channel": (
                f"https://github.com/{REPOSITORY}/releases/download/revocations/revocations.json"
            ),
            "channel_identity": "stable",
        },
    }
    outputs["pack_hash"] = sha256_bytes(compact_json(manifest))
    name = f"pack-{member.slug}.json"
    (assets / name).write_bytes(compact_json(manifest))
    return name, outputs["pack_hash"]


def assemble(args: argparse.Namespace) -> None:
    assets = args.artifacts.resolve()
    if not assets.is_dir():
        raise Refusal(f"artifact directory does not exist: {assets}")
    if not re.fullmatch(r"image-set/v[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?", args.tag):
        raise Refusal("tag must be image-set/v<semver>")
    if not re.fullmatch(r"[0-9a-f]{40}", args.source_commit):
        raise Refusal("source commit must be 40 lowercase hex characters")
    try:
        parsed_time = dt.datetime.fromisoformat(args.issued_at.replace("Z", "+00:00"))
    except ValueError as error:
        raise Refusal("issued-at must be an ISO-8601 timestamp") from error
    if parsed_time.tzinfo is None:
        raise Refusal("issued-at must include a timezone")

    ensure_stage0_aliases(assets)
    members = member_specs()
    for member in members:
        for artifact in member.artifacts:
            require_file(assets / artifact.name)

    flake_lock = args.flake_lock.resolve()
    require_file(flake_lock)
    flake_hash = sha256_file(flake_lock)
    mvm_commit = pinned_mvm_commit(flake_lock)
    low, high, cache = parse_protocols(args.mvm_source.resolve())
    boot_abi = builder_boot_abi(Path(__file__).resolve().parent.parent)
    release_url = f"https://github.com/{REPOSITORY}/releases/download/{args.tag}"

    rendered_members = []
    for member in members:
        sbom_name, sbom_hash = sbom_for(member, assets, args.issued_at, args.tag)
        _, pack_hash = pack_manifest(
            member,
            assets,
            args.issued_at,
            args.tag,
            flake_hash,
            sbom_name,
            sbom_hash,
        )
        rendered = {
            "role": member.role,
            "target": member.target,
        }
        if member.bootable:
            rendered["boot_protocol"] = "linux_direct"
        rendered.update(
            {
                "artifacts": [
                    {
                        "name": artifact.name,
                        "format": artifact.format,
                        "sha256": sha256_file(assets / artifact.name),
                        "size": (assets / artifact.name).stat().st_size,
                    }
                    for artifact in member.artifacts
                ],
                "required_capabilities": list(member.capabilities),
                "pack_hash": pack_hash,
                "sbom": {"uri": f"{release_url}/{sbom_name}", "sha256": sbom_hash},
            }
        )
        rendered_members.append(rendered)

    manifest = {
        "schema_version": 2,
        "set_version": args.tag.removeprefix("image-set/v"),
        "issued_at": args.issued_at,
        "producer": {
            "repository": REPOSITORY,
            "workflow": WORKFLOW,
            "release_tag": args.tag,
            "source_commit": args.source_commit,
        },
        "mvm_source_commit": mvm_commit,
        "compatibility": {
            "guest_agent_protocol": {"min": low, "max": high},
            "builder_cache_contract": cache,
            "builder_boot_abi": boot_abi,
        },
        "nix_inputs": {
            "flake_locks": [{"reference": ".", "lock_hash": flake_hash}],
            "source_revisions": [],
        },
        "revocation_channel": (
            f"https://github.com/{REPOSITORY}/releases/download/revocations/revocations.json"
        ),
        "members": rendered_members,
    }
    manifest_path = assets / "image-set.json"
    manifest_path.write_bytes(compact_json(manifest))
    manifest_hash = sha256_file(manifest_path)
    (assets / "images.lock").write_text(
        "\n".join(
            [
                "schema_version = 1",
                f'repository = "{REPOSITORY}"',
                f'release_tag = "{args.tag}"',
                'manifest_asset = "image-set.json"',
                f'manifest_sha256 = "{manifest_hash}"',
                "",
                "[signing_identity]",
                f'workflow = "{WORKFLOW}"',
                f'tag_ref = "refs/tags/{args.tag}"',
                "",
            ]
        )
    )
    print(f"assembled {len(rendered_members)} members in {assets}")
    print(f"image-set.json sha256 {manifest_hash}")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--artifacts", type=Path, required=True)
    result.add_argument("--tag", required=True)
    result.add_argument("--source-commit", required=True)
    result.add_argument("--issued-at", required=True)
    result.add_argument("--mvm-source", type=Path, required=True)
    result.add_argument("--flake-lock", type=Path, default=Path("flake.lock"))
    return result


def main() -> int:
    try:
        assemble(parser().parse_args())
    except (OSError, json.JSONDecodeError, Refusal) as error:
        print(f"assemble-release: refusing candidate: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
