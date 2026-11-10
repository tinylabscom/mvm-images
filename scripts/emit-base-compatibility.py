#!/usr/bin/env python3
"""Emit an unsigned, local-development base-compatibility/v1 candidate."""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import re
import sys


SPEC = importlib.util.spec_from_file_location(
    "base_compatibility_inventory", Path(__file__).with_name("assemble-release.py")
)
assert SPEC and SPEC.loader
inventory = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = inventory
SPEC.loader.exec_module(inventory)
CAPABILITIES = frozenset({
    "virtio_vsock", "virtio_blk", "dm_verity", "pty",
    "namespaces", "cgroup_v2", "fuse", "overlayfs",
})


class Refusal(ValueError):
    pass


def unique_keys(pairs: list[tuple[str, object]]) -> dict:
    value = {}
    for key, item in pairs:
        if key in value:
            raise Refusal(f"duplicate manifest key: {key}")
        value[key] = item
    return value


def invalid_constant(value: str) -> None:
    raise Refusal(f"non-JSON constant: {value}")


def artifact(member: dict, fmt: object, directory: Path) -> dict:
    records = member.get("artifacts")
    if not isinstance(records, list):
        raise Refusal("member artifacts must be a list")
    matches = [record for record in records if isinstance(record, dict) and record.get("format") == fmt]
    if len(matches) != 1:
        raise Refusal(f"expected exactly one {fmt!r} artifact")
    record = matches[0]
    name, digest, size = (record.get(key) for key in ("name", "sha256", "size"))
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", name):
        raise Refusal(f"artifact must have a plain filename: {name!r}")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise Refusal(f"invalid artifact digest: {name}")
    if type(size) is not int or size <= 0:
        raise Refusal(f"invalid artifact size: {name}")
    path = directory / name
    if not path.is_file() or path.is_symlink() or path.stat().st_size != size:
        raise Refusal(f"missing, symlink or size mismatch: {name}")
    if inventory.sha256_file(path) != digest:
        raise Refusal(f"artifact digest mismatch: {name}")
    return {"name": name, "sha256": digest, "size": size}


def declarations(version: str, loaders: list[str], capabilities: list[str]) -> tuple[list[dict], list[str]]:
    if not re.fullmatch(r"(?:0|[1-9][0-9]{0,4})\.(?:0|[1-9][0-9]{0,4})\.(?:0|[1-9][0-9]{0,4})", version):
        raise Refusal("kernel_version must be strict major.minor.patch")
    if not 1 <= len(loaders) <= 2:
        raise Refusal("provide 1–2 libc loader declarations")
    parsed = []
    for loader in loaders:
        libc, sep, path = loader.partition("=")
        if (not sep or libc not in ("musl", "glibc") or len(path) > 256
                or not path.startswith("/") or any(part in ("", ".", "..") for part in path[1:].split("/"))
                or any(not re.fullmatch(r"[A-Za-z0-9._+-]+", part) for part in path[1:].split("/"))):
            raise Refusal(f"invalid libc loader declaration: {loader!r}")
        parsed.append({"libc": libc, "path": path})
    if len({item["libc"] for item in parsed}) != len(parsed):
        raise Refusal("duplicate libc loader declaration")
    if (len(capabilities) > 8 or len(set(capabilities)) != len(capabilities)
            or any(value not in CAPABILITIES for value in capabilities)):
        raise Refusal("kernel_capabilities must be unique names from the closed v1 allowlist")
    return parsed, capabilities


def candidate(manifest_path: Path, directory: Path, arch: str, profile: str,
              version: str, loaders: list[str], capabilities: list[str]) -> dict:
    parsed_loaders, parsed_capabilities = declarations(version, loaders, capabilities)
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise Refusal("image-set manifest must be a regular file, not a symlink")
    original = manifest_path.read_bytes()
    try:
        manifest = json.loads(
            original, object_pairs_hook=unique_keys, parse_constant=invalid_constant,
        )
        if (type(manifest.get("schema_version")) is not int or manifest["schema_version"] != 3
                or type(manifest["compatibility"]["base_image_contract"]) is not int
                or manifest["compatibility"]["base_image_contract"] != 1):
            raise Refusal("requires schema 3/base_image_contract 1")
        members = manifest["members"]
        if not isinstance(members, list):
            raise Refusal("manifest members must be a list")
    except (ValueError, TypeError, KeyError, AttributeError) as error:
        raise Refusal(f"invalid schema 3 image-set manifest: {error}") from error
    selected = []
    for role in ("workload_kernel", "workload_rootfs"):
        matching = [member for member in members if isinstance(member, dict)
                    and member.get("role") == {role: profile}
                    and member.get("target") == {"arch": arch}
                    and "build_mode" not in member]
        if len(matching) != 1:
            raise Refusal(f"expected one production {role} for {arch}/{profile}")
        selected.append(matching[0])
    kernel_member, root_member = selected
    if kernel_member.get("boot_protocol") != "linux_direct":
        raise Refusal("kernel must declare linux_direct boot")
    kernel = artifact(kernel_member, {"kernel": "elf" if arch == "x86_64" else "image"}, directory)
    tar = artifact(root_member, "tar_gz", directory)
    tar_path = directory / tar["name"]
    try:
        inventory.inspect_composition_tar(tar_path, 1)
    except (inventory.Refusal, OSError) as error:
        raise Refusal(f"composition tar contract failed: {error}") from error
    # Refuse changed bytes after inspection, not merely a hash that matched before it.
    for record in (kernel, tar):
        if inventory.sha256_file(directory / record["name"]) != record["sha256"]:
            raise Refusal(f"artifact changed during inspection: {record['name']}")
    if manifest_path.read_bytes() != original:
        raise Refusal("image-set manifest changed during inspection")
    return {
        "schema": "base-compatibility/v1",
        "image_set_manifest_sha256": inventory.sha256_bytes(original),
        "arch": arch, "profile": profile,
        "kernel": kernel, "composition_tar": tar,
        "base_image_contract": 1, "boot_protocol": "linux_direct",
        "filesystem_abi": 1, "kernel_version": version,
        "libc_loaders": parsed_loaders, "kernel_capabilities": parsed_capabilities,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--arch", choices=inventory.ARCHES, required=True)
    parser.add_argument("--profile", choices=("default_tenant", "rootless_tenant"), required=True)
    parser.add_argument("--kernel-version", required=True)
    parser.add_argument("--libc-loader", action="append", default=[], metavar="LIBC=/absolute/path")
    parser.add_argument("--kernel-capability", action="append", default=[], metavar="NAME")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if not args.artifacts.is_dir():
            raise Refusal("artifact directory does not exist")
        directory = args.artifacts.resolve()
        if args.output.resolve() == args.manifest.resolve() or directory in args.output.resolve().parents:
            raise Refusal("output must be separate from the manifest and artifact directory")
        if args.output.exists() or args.output.is_symlink():
            raise Refusal("output already exists")
        result = candidate(args.manifest, directory, args.arch, args.profile,
                           args.kernel_version, args.libc_loader, args.kernel_capability)
        args.output.write_text(json.dumps(result, sort_keys=True, indent=2) + "\n")
    except (Refusal, OSError) as error:
        parser.exit(1, f"base-compatibility candidate refused: {error}\n")
    print(f"UNVERIFIED LOCAL DEVELOPMENT CANDIDATE: {args.output}\n"
          "Kernel version, libc loaders and capabilities are declarations, NOT inspected facts.\n"
          "No signature or production trust; a protected pipeline must independently inspect "
          "built bytes, boot-verify and sign the exact descriptor.")


if __name__ == "__main__":
    main()
