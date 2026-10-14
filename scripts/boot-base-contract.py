#!/usr/bin/env python3
"""Boot actual workload roots with a separate, test-only contract initramfs.

Manifest mode binds evidence to assembled candidate bytes (not authentication).
Without --manifest, --arch and --expected-contract explicitly request diagnostic
build evidence derived from the canonical producer inventory. --plan never boots
or writes evidence.
"""
from __future__ import annotations

import argparse
import gzip
import importlib.util
import json
from pathlib import Path
import re
import sys

import e2e_boot as boot


SPEC = importlib.util.spec_from_file_location(
    "contract_member_specs", Path(__file__).with_name("assemble-release.py")
)
assert SPEC and SPEC.loader
inventory = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = inventory
SPEC.loader.exec_module(inventory)
READY = "MVM-IMAGES-BASE-CONTRACT-READY"
REFUSED = "MVM-IMAGES-BASE-CONTRACT-REFUSED"


def describe(path: Path) -> dict:
    if not path.is_file() or path.is_symlink() or path.stat().st_size == 0:
        raise boot.E2EError(f"missing, empty or symlink artifact: {path}")
    return {"name": path.name, "sha256": inventory.sha256_file(path),
            "size": path.stat().st_size}


def artifact_path(directory: Path, record: dict) -> Path:
    name = record["name"]
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", name):
        raise boot.E2EError(f"artifact must be a plain filename: {name!r}")
    path = directory / name
    actual = describe(path)
    if any(actual[key] != record[key] for key in ("sha256", "size")):
        raise boot.E2EError(f"artifact digest/size mismatch: {name}")
    return path.resolve()


def build_members(directory: Path, arch: str) -> list[dict]:
    members = []
    for member in inventory.member_specs():
        if not isinstance(member.role, dict) or member.target["arch"] != arch:
            continue
        members.append({
            "role": member.role, "target": member.target,
            "build_mode": member.build_mode, "boot_protocol": "linux_direct",
            "artifacts": [
                {**describe(directory / artifact.name), "format": artifact.format}
                for artifact in member.artifacts
            ],
        })
    return members


def probe_description(path: Path, arch: str) -> dict:
    """Check the static BusyBox ELF architecture in the actual newc payload."""
    record = describe(path)
    payload = gzip.decompress(path.read_bytes())
    offset = 0
    machines = {"x86_64": 62, "aarch64": 183}
    while offset + 110 <= len(payload):
        header = payload[offset:offset + 110]
        if header[:6] != b"070701":
            break
        size = int(header[54:62], 16)
        namesize = int(header[94:102], 16)
        name = payload[offset + 110:offset + 110 + namesize].rstrip(b"\0")
        start = (offset + 110 + namesize + 3) & ~3
        data = payload[start:start + size]
        if name in (b"bin/busybox", b"./bin/busybox"):
            if (len(data) < 20 or data[:6] != b"\x7fELF\x02\x01" or
                    int.from_bytes(data[18:20], "little") != machines[arch]):
                raise boot.E2EError(f"probe BusyBox architecture mismatch: {arch}")
            return record
        offset = (start + size + 3) & ~3
    raise boot.E2EError("probe requires a newc archive containing static BusyBox")


def select_pairs(members: list[dict], arches: tuple[str, ...]) -> list[tuple]:
    kernels = {}
    roots = []
    for member in members:
        role = member["role"]
        if not isinstance(role, dict):
            continue
        arch = member["target"]["arch"]
        if arch not in arches:
            continue
        mode = member.get("build_mode")
        if "workload_kernel" in role:
            key = (arch, role["workload_kernel"], mode)
            if key in kernels or member.get("boot_protocol") != "linux_direct":
                raise boot.E2EError(f"ambiguous or non-direct kernel: {key}")
            kernels[key] = member
        if "workload_rootfs" in role:
            roots.append(((arch, role["workload_rootfs"], mode), member))
    expected = {
        (m.target["arch"], m.role["workload_rootfs"], m.build_mode)
        for m in inventory.member_specs()
        if isinstance(m.role, dict) and "workload_rootfs" in m.role
        and m.target["arch"] in arches
    }
    keys = [key for key, _ in roots]
    if len(keys) != len(set(keys)) or set(keys) != expected:
        raise boot.E2EError("workload roots do not match canonical producer inventory")
    pairs = []
    for key, root in roots:
        if key not in kernels:
            raise boot.E2EError(f"missing matching profile/arch/build-mode kernel: {key}")
        pairs.append((key, root, kernels[key]))
    return pairs


def only_artifact(member: dict, fmt: object) -> dict:
    matches = [a for a in member["artifacts"] if a["format"] == fmt]
    if len(matches) != 1:
        raise boot.E2EError(f"expected one {fmt!r} artifact")
    return matches[0]


def execute(args: argparse.Namespace) -> dict:
    arches = (args.arch,) if args.arch else inventory.ARCHES
    local_contract = inventory.base_image_contract(inventory.IMAGES_ROOT)
    if args.manifest:
        manifest_bytes = args.manifest.read_bytes()
        manifest = json.loads(manifest_bytes)
        schema = manifest.get("schema_version")
        declared_contract = manifest.get("compatibility", {}).get("base_image_contract")
        if (type(schema) is not int or schema != 3 or
                type(declared_contract) is not int or declared_contract != local_contract):
            raise boot.E2EError("candidate requires schema 3/base_image_contract 1")
        members = manifest["members"]
        binding = {"scope": "assembled-candidate",
                   "manifest_sha256": inventory.sha256_bytes(manifest_bytes)}
    else:
        if args.arch is None or args.expected_contract != local_contract:
            raise boot.E2EError("build diagnostics require --arch and --expected-contract 1")
        # Filter before examining files: each PR runner stages only its native arch.
        members = build_members(args.artifacts, args.arch)
        binding = {"scope": "build-diagnostic"}
    probes = {}
    for value in args.probe:
        arch, sep, name = value.partition("=")
        if not sep or arch not in inventory.ARCHES or arch in probes:
            raise boot.E2EError("--probe must be unique ARCH=PATH")
        path = Path(name).resolve()
        if path.name != f"base-contract-probe-{arch}.cpio.gz":
            raise boot.E2EError("probe filename must identify its architecture")
        probes[arch] = path
    if args.witness is not None:
        inputs = {
            (args.artifacts / artifact["name"]).resolve()
            for member in members for artifact in member["artifacts"]
        } | set(probes.values())
        if args.manifest:
            inputs.add(args.manifest.resolve())
        if args.witness.resolve() in inputs:
            raise boot.E2EError("witness must not overwrite any input")
        if not args.plan:
            # A failed boot must not leave evidence from an earlier invocation.
            args.witness.unlink(missing_ok=True)
    plans = []
    for (arch, profile, mode), root, kernel in select_pairs(members, arches):
        if arch not in probes:
            raise boot.E2EError(f"missing architecture-matched probe for {arch}")
        # Check every artifact on both selected members, including sealing sidecars.
        for member in (root, kernel):
            for record in member["artifacts"]:
                artifact_path(args.artifacts, record)
        root_record = only_artifact(root, "ext4")
        kernel_record = only_artifact(kernel, {"kernel": "elf" if arch == "x86_64" else "image"})
        probe = probe_description(probes[arch], arch)
        command = boot.qemu_command(
            binary=f"qemu-system-{arch}", arch=arch,
            kernel=artifact_path(args.artifacts, kernel_record),
            rootfs=artifact_path(args.artifacts, root_record),
            guest_cid=7, accel=args.accel, rootfs_type="ext4",
            init="/mvm-images-contract-probe", initrd=probes[arch],
            read_only=True, require_vsock=True,
        )
        plans.append({"arch": arch, "profile": profile, "build_mode": mode,
                      "rootfs": root_record, "kernel": kernel_record,
                      "probe": probe, "vmm_plan": command})
    evidence = {**binding, "base_image_contract": local_contract,
                "ready_marker": READY, "boots": plans}
    if args.plan:
        return {**evidence, "evidence": False}
    if args.witness is None:
        raise boot.E2EError("actual boots require --witness")
    for plan in plans:
        boot._require_binary(plan["vmm_plan"][0])
        boot.run_until_ready(plan["vmm_plan"], args.timeout, READY,
                             exact_marker=True, refusal_marker=REFUSED)
    # Bind success to the same input bytes observed before boot, not just to
    # their names. QEMU's read-only drive protects against guest writes; these
    # checks also refuse concurrent host-side changes before evidence is saved.
    for _, root, kernel in select_pairs(members, arches):
        for member in (root, kernel):
            for record in member["artifacts"]:
                artifact_path(args.artifacts, record)
    for plan in plans:
        if describe(probes[plan["arch"]]) != plan["probe"]:
            raise boot.E2EError("probe changed during boot")
    if args.manifest and inventory.sha256_file(args.manifest) != binding["manifest_sha256"]:
        raise boot.E2EError("candidate manifest changed during boot")
    evidence["evidence"] = True
    args.witness.parent.mkdir(parents=True, exist_ok=True)
    args.witness.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
    return evidence


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--arch", choices=inventory.ARCHES)
    parser.add_argument("--expected-contract", type=int)
    parser.add_argument("--probe", action="append", required=True)
    parser.add_argument("--witness", type=Path)
    parser.add_argument("--accel", choices=("tcg", "kvm"), default="tcg")
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument("--plan", action="store_true")
    try:
        print(json.dumps(execute(parser.parse_args()), indent=2, sort_keys=True))
    except (boot.E2EError, OSError, ValueError, KeyError, TypeError) as exc:
        print(f"base contract boot refused: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
