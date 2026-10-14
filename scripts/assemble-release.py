#!/usr/bin/env python3
"""Assemble one complete, consumer-verifiable mvm image-set release."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

# This script is also loaded by file path to reuse the producer member table.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from ext4_contract import read_base_image_contract

REPOSITORY = "tinylabscom/mvm-images"
WORKFLOW = ".github/workflows/release.yml"
ZERO_SHA256 = "0" * 64
ARCHES = ("x86_64", "aarch64")
IMAGES_ROOT = Path(__file__).resolve().parents[1]


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
    # Which build of the role this member carries; None is the production
    # build. Only the workload bases publish a dev variant.
    build_mode: str | None = None


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
                        Artifact(f"builder-vm-{arch}.kernel.config", "text"),
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
                    f"default-workload-kernel-dev-{arch}",
                    {"workload_kernel": "default_tenant"},
                    target,
                    (Artifact(f"default-microvm-dev-vmlinux-{arch}", kernel),),
                    ("virtio_vsock",),
                    True,
                    build_mode="dev",
                ),
                Member(
                    f"default-workload-rootfs-dev-{arch}",
                    {"workload_rootfs": "default_tenant"},
                    target,
                    # The dev build is unsealed: no dm-verity sidecars. The
                    # member carries the meta sidecar too, so a consumer that
                    # fetches the dev slot gets the complete install a pair
                    # build produces.
                    (
                        Artifact(f"default-microvm-dev-rootfs-{arch}.ext4", "ext4"),
                        Artifact(f"default-microvm-dev-meta-{arch}.json", "json"),
                    ),
                    ("virtio_blk",),
                    build_mode="dev",
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


def base_image_contract(images_root: Path) -> int:
    """Read the locally owned contract also baked into every base root."""
    path = images_root / "images" / "base-image-contract.nix"
    body = path.read_text().strip()
    if body != "1":
        raise Refusal(f"unsupported base-image contract in {path}: {body!r}")
    return int(body)


def builder_cache_contract(images_root: Path) -> int:
    path = images_root / "images" / "builder-vm" / "cache-contract.nix"
    body = path.read_text().strip()
    if not body.isdecimal() or int(body) < 1:
        raise Refusal(f"invalid builder cache contract in {path}")
    return int(body)


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


def write_provenance_subjects(path: Path, subjects: list[tuple[str, str]]) -> None:
    """Write the build-provenance subjects as a sha256sum-format checksums file.

    The release workflow hands this file to the attestation step, so the set it
    attests is exactly the set this script assembled: every member artifact,
    every member's pack manifest and SBOM, and the `image-set.json` root that
    commits to all of them. A new member is attested by being added to
    `member_specs`, with no second list to keep in step.
    """
    names = [name for name, _ in subjects]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise Refusal(f"provenance subject named twice: {', '.join(duplicates)}")
    path.write_text("".join(f"{digest}  {name}\n" for name, digest in subjects))


def assemble(args: argparse.Namespace) -> None:
    assets = args.artifacts.resolve()
    if not assets.is_dir():
        raise Refusal(f"artifact directory does not exist: {assets}")
    subjects_path = args.subjects.resolve()
    # Keep the attestation input outside the payload directory. Publication
    # derives its asset allowlist from this file, never from diagnostic outputs.
    if assets in subjects_path.parents:
        raise Refusal(f"provenance subjects must be written outside {assets}")
    if not subjects_path.parent.is_dir():
        raise Refusal(f"directory for provenance subjects does not exist: {subjects_path.parent}")
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

    # One table owns the workload selection, including both dev roots. Hash
    # before inspection and compare every later use and the final staged bytes.
    inspected = {}
    contracts = set()
    for member in members:
        if not isinstance(member.role, dict) or "workload_rootfs" not in member.role:
            continue
        roots = [artifact for artifact in member.artifacts if artifact.format == "ext4"]
        if len(roots) != 1:
            raise Refusal(f"{member.slug}: expected exactly one ext4 workload root")
        path = assets / roots[0].name
        before = sha256_file(path)
        try:
            contracts.add(read_base_image_contract(path))
        except (ValueError, OSError, subprocess.CalledProcessError) as error:
            raise Refusal(f"cannot inspect {path.name}: {error}") from error
        if sha256_file(path) != before:
            raise Refusal(f"artifact changed during contract inspection: {path.name}")
        inspected[path.name] = before
    if contracts != {base_image_contract(IMAGES_ROOT)}:
        raise Refusal(f"inconsistent workload base-image contracts: {contracts}")
    contract = next(iter(contracts))

    flake_lock = args.flake_lock.resolve()
    require_file(flake_lock)
    flake_hash = sha256_file(flake_lock)
    cache = builder_cache_contract(IMAGES_ROOT)
    boot_abi = builder_boot_abi(Path(__file__).resolve().parent.parent)
    release_url = f"https://github.com/{REPOSITORY}/releases/download/{args.tag}"

    rendered_members = []
    subjects: list[tuple[str, str]] = []
    for member in members:
        sbom_name, sbom_hash = sbom_for(member, assets, args.issued_at, args.tag)
        pack_name, pack_hash = pack_manifest(
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
        if member.build_mode is not None:
            rendered["build_mode"] = member.build_mode
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
        subjects.extend(
            (artifact["name"], artifact["sha256"]) for artifact in rendered["artifacts"]
        )
        # Not `pack_hash`: that is the digest of the manifest with its own
        # hash field zeroed, and the attestation names the published bytes.
        subjects.append((pack_name, sha256_file(assets / pack_name)))
        subjects.append((sbom_name, sbom_hash))

    manifest = {
        "schema_version": 3,
        "set_version": args.tag.removeprefix("image-set/v"),
        "issued_at": args.issued_at,
        "producer": {
            "repository": REPOSITORY,
            "workflow": WORKFLOW,
            "release_tag": args.tag,
            "source_commit": args.source_commit,
        },
        "compatibility": {
            "base_image_contract": contract,
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
    for member in rendered_members:
        for artifact in member["artifacts"]:
            name = artifact["name"]
            if name in inspected and (
                artifact["sha256"] != inspected[name]
                or sha256_file(assets / name) != inspected[name]
            ):
                raise Refusal(f"artifact changed after contract inspection: {name}")
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
    subjects.append(("image-set.json", manifest_hash))
    write_provenance_subjects(subjects_path, subjects)
    print(f"assembled {len(rendered_members)} members in {assets}")
    print(f"image-set.json sha256 {manifest_hash}")
    print(f"{len(subjects)} provenance subjects in {subjects_path}")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--artifacts", type=Path, required=True)
    result.add_argument("--tag", required=True)
    result.add_argument("--source-commit", required=True)
    result.add_argument("--issued-at", required=True)
    result.add_argument("--flake-lock", type=Path, default=Path("flake.lock"))
    result.add_argument(
        "--subjects",
        type=Path,
        required=True,
        help="where to write the build-provenance subjects (sha256sum format), "
        "outside the artifact directory",
    )
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
