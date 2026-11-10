import copy
import gzip
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/emit-base-compatibility.py"
SPEC = importlib.util.spec_from_file_location("emit_base_compatibility", SCRIPT)
producer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(producer)


def tar_bytes(marker=b"1\n"):
    body = io.BytesIO()
    with tarfile.open(fileobj=body, mode="w") as archive:
        entry = tarfile.TarInfo("./etc/mvm/base-image-contract")
        entry.size = len(marker)
        entry.mode = 0o444
        archive.addfile(entry, io.BytesIO(marker))
        worker = tarfile.TarInfo("./home/mvm-worker")
        worker.type = tarfile.DIRTYPE
        worker.uid = worker.gid = 901
        archive.addfile(worker)
    return gzip.compress(body.getvalue(), mtime=0)


class CandidateTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.assets = self.root / "assets"
        self.assets.mkdir()
        self.manifest = self.root / "image-set.json"
        self.output = self.root / "candidate.json"
        self.members = []
        for arch in ("x86_64", "aarch64"):
            for profile in ("default_tenant", "rootless_tenant"):
                prefix = f"{arch}-{profile}"
                kernel = self.record(prefix + ".vmlinux", b"kernel", {"kernel": "elf" if arch == "x86_64" else "image"})
                root = self.record(prefix + ".tar.gz", tar_bytes(), "tar_gz")
                self.members.extend([
                    {"role": {"workload_kernel": profile}, "target": {"arch": arch},
                     "boot_protocol": "linux_direct", "artifacts": [kernel]},
                    {"role": {"workload_rootfs": profile}, "target": {"arch": arch},
                     "artifacts": [root]},
                ])
        self.write_manifest()

    def record(self, name, content, fmt):
        (self.assets / name).write_bytes(content)
        return {"name": name, "sha256": hashlib.sha256(content).hexdigest(),
                "size": len(content), "format": fmt}

    def write_manifest(self, contract=1):
        self.manifest.write_text(json.dumps({
            "schema_version": 3, "compatibility": {"base_image_contract": contract},
            "members": self.members,
        }, indent=2))

    def run_cli(self, *extra):
        return subprocess.run([
            sys.executable, str(SCRIPT), "--manifest", str(self.manifest),
            "--artifacts", str(self.assets), "--arch", "x86_64",
            "--profile", "rootless_tenant", "--kernel-version", "6.12.4",
            "--libc-loader", "glibc=/lib64/ld-linux-x86-64.so.2",
            "--kernel-capability", "namespaces", "--output", str(self.output), *extra,
        ], capture_output=True, text=True)

    def test_exact_manifest_and_selected_production_artifacts(self):
        self.members.extend([
            {"role": {"workload_kernel": "rootless_tenant"}, "target": {"arch": "x86_64"},
             "build_mode": "dev", "boot_protocol": "linux_direct", "artifacts": []},
            {"role": {"workload_rootfs": "rootless_tenant"}, "target": {"arch": "x86_64"},
             "build_mode": "dev", "artifacts": []},
        ])
        self.write_manifest()
        result = self.run_cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("UNVERIFIED LOCAL DEVELOPMENT", result.stdout)
        self.assertIn("NOT inspected facts", result.stdout)
        candidate = json.loads(self.output.read_text())
        self.assertEqual(candidate, {
            "schema": "base-compatibility/v1",
            "image_set_manifest_sha256": hashlib.sha256(self.manifest.read_bytes()).hexdigest(),
            "arch": "x86_64", "profile": "rootless_tenant",
            "kernel": {key: self.members[2]["artifacts"][0][key] for key in ("name", "sha256", "size")},
            "composition_tar": {key: self.members[3]["artifacts"][0][key] for key in ("name", "sha256", "size")},
            "base_image_contract": 1, "boot_protocol": "linux_direct",
            "filesystem_abi": 1, "kernel_version": "6.12.4",
            "libc_loaders": [{"libc": "glibc", "path": "/lib64/ld-linux-x86-64.so.2"}],
            "kernel_capabilities": ["namespaces"],
        })
        self.assertNotIn("trust", candidate)
        self.assertNotIn("signature", candidate)

    def test_refuses_schema_selection_and_boot_mismatch(self):
        for change in ("schema", "contract", "duplicate", "missing", "boot", "dev_only"):
            with self.subTest(change=change):
                members = copy.deepcopy(self.members)
                if change == "schema":
                    self.manifest.write_text(json.dumps({"schema_version": 2, "compatibility":
                        {"base_image_contract": 1}, "members": members}))
                elif change == "contract":
                    self.write_manifest(True)
                else:
                    if change == "duplicate":
                        self.members.append(copy.deepcopy(self.members[2]))
                    elif change == "missing":
                        self.members.pop(2)
                    elif change == "boot":
                        self.members[2]["boot_protocol"] = "other"
                    else:
                        self.members[2]["build_mode"] = "dev"
                    self.write_manifest()
                result = self.run_cli()
                self.assertNotEqual(result.returncode, 0, change)
                self.assertFalse(self.output.exists())
                self.members = members
                self.write_manifest()

    def test_both_arches_and_profiles_select_matching_pair(self):
        for arch in ("x86_64", "aarch64"):
            for profile in ("default_tenant", "rootless_tenant"):
                with self.subTest(arch=arch, profile=profile):
                    value = producer.candidate(
                        self.manifest, self.assets, arch, profile, "6.12.4",
                        ["musl=/lib/ld-musl-aarch64.so.1"], ["virtio_vsock"],
                    )
                    self.assertTrue(value["kernel"]["name"].startswith(f"{arch}-{profile}"))
                    self.assertTrue(value["composition_tar"]["name"].startswith(f"{arch}-{profile}"))

    def test_refuses_ambiguous_manifest_json(self):
        self.manifest.write_bytes(self.manifest.read_bytes().replace(
            b'"schema_version": 3,', b'"schema_version": 3, "schema_version": 3,',
        ))
        result = self.run_cli()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("duplicate manifest key", result.stderr)
        self.assertFalse(self.output.exists())

    def test_refuses_modified_missing_or_unsafe_artifacts_and_contract(self):
        for change in ("hash", "size", "missing", "symlink", "traversal", "marker"):
            with self.subTest(change=change):
                record = self.members[3]["artifacts"][0]
                original = copy.deepcopy(record)
                path = self.assets / record["name"]
                data = path.read_bytes()
                try:
                    if change == "hash":
                        path.write_bytes(b"X" + data[1:])
                    elif change == "size":
                        record["size"] = True
                    elif change == "missing":
                        path.unlink()
                    elif change == "symlink":
                        path.unlink()
                        path.symlink_to(self.assets / self.members[2]["artifacts"][0]["name"])
                    elif change == "traversal":
                        record["name"] = "../image-set.json"
                    else:
                        content = tar_bytes(b"2\n")
                        path.write_bytes(content)
                        record["size"] = len(content)
                        record["sha256"] = hashlib.sha256(content).hexdigest()
                    self.write_manifest()
                    result = self.run_cli()
                    self.assertNotEqual(result.returncode, 0, (change, result.stdout, result.stderr))
                    self.assertFalse(self.output.exists())
                finally:
                    path.unlink(missing_ok=True)
                    path.write_bytes(data)
                    record.clear()
                    record.update(original)
                    self.write_manifest()

    def test_refuses_invalid_local_declarations_and_output_collision(self):
        for version in ("6.12", "6.12.4-rc1", "06.12.4", "6.12.4\n"):
            with self.subTest(version=version):
                with self.assertRaises(producer.Refusal):
                    producer.candidate(self.manifest, self.assets, "x86_64", "rootless_tenant",
                                       version, ["musl=/lib/ld-musl-x86_64.so.1"], [])
        for loader in ("glibc=relative", "other=/lib/ld.so", "musl=/lib/../ld.so"):
            with self.subTest(loader=loader):
                with self.assertRaises(producer.Refusal):
                    producer.candidate(self.manifest, self.assets, "x86_64", "rootless_tenant",
                                       "6.12.4", [loader], [])
        for capability in ("NET_NS", "network-device", "user_ns"):
            with self.subTest(capability=capability):
                with self.assertRaises(producer.Refusal):
                    producer.candidate(self.manifest, self.assets, "x86_64", "rootless_tenant",
                                       "6.12.4", ["glibc=/lib64/ld-linux.so.2"], [capability])
        for loaders, capabilities in (
            (["glibc=/lib/ld.so", "glibc=/lib64/ld.so"], []),
            (["glibc=/lib/ld.so"], ["virtio_vsock", "virtio_vsock"]),
        ):
            with self.assertRaises(producer.Refusal):
                producer.candidate(self.manifest, self.assets, "x86_64", "rootless_tenant",
                                   "6.12.4", loaders, capabilities)
        self.output.write_text("existing")
        result = self.run_cli()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.output.read_text(), "existing")


if __name__ == "__main__":
    unittest.main()
