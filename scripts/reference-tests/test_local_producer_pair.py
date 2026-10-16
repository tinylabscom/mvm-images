"""Independent reference runner for the f5ed151 producer, not old schema-2 trees.

Prepare only f5ed151 scripts into a private source directory, then run:
  python test_local_producer_pair.py SOURCE_SCRIPTS PACKET_JSON MANIFESTS_JSON ROOTFS_JSON
No mvm source, host runtime, builder, VMM or executable fixture is involved.
--capture prints fresh manifests. --capture-observations emits the updated
observation packet, including real raw-name states on supporting filesystems.
Ordinary verification never replaces or updates expected observations.
"""
import argparse
import hashlib
import errno
import importlib.util
from importlib.metadata import version
import json
import os
from pathlib import Path
import sys
import tempfile
import zlib
from unittest import mock

REVISION = "f5ed15188114f2e3b7e27717640f0cbd37f53914"
SOURCE_HASH = "45b93439cdebeba9c2d84749ec7c9afcfbe2c46802a756d84febb8b441583387"
DATA_HASH = "d622678f8d8288aa2b124ee589b0979b086ed302efc2e907ad8ed0dd877cb40b"
MANIFESTS_HASH = "9057fd0265d35d547ae8d0bad0f6e8f2d0d1783434941701f3f9044806ad635f"
SOURCES = {
    "emit-local-manifest.py": SOURCE_HASH,
    "local_ext4_contract.py": "be996fa352157dda034f6b2e41917522f4b4c7eb681712356ab800cccf1c0aa7",
    "ext4_contract.py": "23b51291566a3d2c2e68b3ce6e18c4baba2e436f59a2eba0fc781297842fa795",
    "tests/test_local_mvm.py": "f8be49e3de18d1c6f2d87627cbf25e38b0d32223257ffde00c17dafdf49f23cc",
    "tests/ext4_fixture.py": "d5d4e640117b42272e2bb205e075f881ec3ee6975e0929ab71d419d462e1cf6c",
}
DEPENDENCIES = {
    "dulwich": "0.24.10",
    "ext4": "1.4.1",
    "urllib3": "2.6.3",
    "cachetools": "7.1.1",
    "crcmod": "1.7",
}


def rootfs_data(path):
    packet = path.read_bytes()
    assert hashlib.sha256(packet).hexdigest() == (
        "d9855dd4f4e5bf1d9005fc553c750a0b9ca31a325c9d8a1c2d151ffe70108a96")
    data = json.loads(packet)
    assert data["version"] == 1 and data["format"] == "ext4"
    assert data["encoding"] == "zlib-hex"
    assert data["size"] == 36864 and data["compressed_size"] == 285
    assert len(data["data"]) == 570
    compressed = bytes.fromhex(data["data"])
    assert hashlib.sha256(compressed).hexdigest() == data["compressed_sha256"] == (
        "f0ec33287174cad091375c671830d9ad1db9d835b25584f6c380bec42052526b")
    decoder = zlib.decompressobj()
    body = decoder.decompress(compressed, 36865)
    assert decoder.eof and not decoder.unused_data and len(body) == 36864
    assert hashlib.sha256(body).hexdigest() == data["sha256"] == (
        "17a0d044d997f556f231fed641fbe41ccf82638857aa11fdb6cc9a48f11765f9")
    return body


def matrix_files(root, files):
    for encoded, kind, body in files:
        name = bytes.fromhex(encoded)
        assert not name.startswith(b"/") and all(
            part not in (b"", b".", b"..", b".git") for part in name.split(b"/"))
        path = os.path.join(os.fsencode(root), name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if kind == "file":
            with open(path, "wb") as stream:
                stream.write(bytes.fromhex(body))
        else:
            assert kind == "symlink" and os.name == "posix"
            if os.path.lexists(path):
                os.unlink(path)
            os.symlink(bytes.fromhex(body), path)


def observation_matrix(fixture, packet, parent, capture):
    for case in packet["observation_cases"]:
        if capture:
            case.pop("unsupported_on_capture", None)
        if case["unix"] and os.name != "posix":
            continue  # Explicitly unsupported platform, not reported as passed.
        common = fixture.committed_repo(parent / case["name"], {
            bytes.fromhex(p).decode("ascii"): bytes.fromhex(b).decode("ascii")
            for p, kind, b in case["initial"] if kind == "file"
        })
        root = common
        if case["linked"]:
            root = parent / (case["name"] + "-linked")
            root.mkdir()
            admin = common / ".git/worktrees/witness"
            admin.mkdir(parents=True)
            with fixture.Repo(str(common)) as repo:
                (admin / "HEAD").write_bytes(repo.head() + b"\n")
            (admin / "commondir").write_text("../..\n")
            (admin / "gitdir").write_text(str(root / ".git") + "\n")
            (root / ".git").write_text(f"gitdir: {admin}\n")
            (admin / "index").write_bytes((common / ".git/index").read_bytes())
            matrix_files(root, case["initial"])
        common_before = fixture.eml.repo_identity(common)
        observed = []
        for step in case["steps"]:
            try:
                matrix_files(root, step["files"])
            except OSError as error:
                if case["name"] != "non-utf8-name" or error.errno != errno.EILSEQ:
                    raise
                if capture:
                    case["unsupported_on_capture"] = "EILSEQ"
                else:
                    assert case.get("unsupported_on_capture") == "EILSEQ"
                print("UNSUPPORTED filesystem: non-utf8-name (actual EILSEQ)", file=sys.stderr)
                break
            if step["stage"]:
                assert not case["linked"]
                with fixture.Repo(str(root)) as repo:
                    index = repo.open_index()
                    for path, kind, body in step["files"]:
                        assert kind == "file"
                        name = bytes.fromhex(path)
                        blob = fixture.Blob.from_string(bytes.fromhex(body))
                        repo.object_store.add_object(blob)
                        index[name] = fixture.index_entry_from_stat(
                            os.lstat(os.path.join(os.fsencode(root), name)), blob.id)
                    index.write()
            observed.append(fixture.eml.repo_identity(root))
        if case["name"] == "ignored-files":
            assert observed[0] == observed[1] == observed[2] != observed[3]
        else:
            assert all(a != b for a, b in zip(observed, observed[1:]))
        if case["linked"]:
            assert fixture.eml.repo_identity(common) == common_before
        if capture:
            case["expected"] = observed
        else:
            assert observed == case["expected"], case["name"]


def run(source, packet, rootfs, capture_observations=False):
    assert sys.version_info >= (3, 12), "reference runner requires Python >= 3.12"
    for package, expected in DEPENDENCIES.items():
        assert version(package) == expected, f"reference dependency mismatch: {package}"
    source = source.resolve()
    for name, expected in SOURCES.items():
        assert hashlib.sha256((source / name).read_bytes()).hexdigest() == expected, name
    assert packet["version"] == 1
    assert packet["producer"] == {
        "revision": REVISION, "path": "scripts/emit-local-manifest.py",
        "sha256": SOURCE_HASH,
    }
    sys.path[:0] = [str(source), str(source / "tests")]
    spec = importlib.util.spec_from_file_location("reference_fixture", source / "tests/test_local_mvm.py")
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    eml = fixture.eml
    for vector in packet["vectors"]:
        index = [(bytes.fromhex(p), mode, stage, flags, bytes.fromhex(oid))
                 for p, mode, stage, flags, oid in vector["index"]]
        files = lambda name: [(bytes.fromhex(p), mode, bytes.fromhex(body))
                              for p, mode, body in vector[name]]
        actual = eml.fingerprint_v2(bytes.fromhex(vector["head"]), index,
                                    files("working"), files("untracked"))
        assert actual == vector["output"]
    cases = []
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        os.environ["HOME"] = str(root / "home")
        os.environ["XDG_CONFIG_HOME"] = str(root / "xdg")
        os.environ["SOURCE_DATE_EPOCH"] = "1"
        os.environ["PATH"] = ""
        for key in list(os.environ):
            if key.upper().startswith("GIT_"):
                del os.environ[key]
        observation_matrix(fixture, packet, root, capture_observations)
        repo = fixture.dirty_fixture(root / "repo")
        assert eml.repo_identity(repo) == {
            "commit": packet["repository"]["commit"],
            "worktree": packet["repository"]["dirty"],
        }
        for dirty in (False, True):
            for arch in ("x86_64", "aarch64"):
                for profile in ("default", "rootless"):
                    name = f"{str(dirty).lower()}-{arch}-{profile}"
                    images = fixture.images_checkout(root / name, boot_abi="2\n")
                    if dirty:
                        (images / "flake.nix").write_bytes(b"changed\n")
                    artifact = root / "rootfs.ext4"
                    artifact.write_bytes(rootfs)
                    out = root / (name + "-set")
                    args = eml.parse_args([
                        "--images-checkout", str(images), "--arch", arch,
                        "--builder-cache-contract", "1", "--out", str(out),
                        "--artifact", f"{profile}_tenant_workload_rootfs", "ext4", str(artifact),
                    ])
                    eml.emit(args)
                    manifest = json.loads((out / "image-set.json").read_bytes())
                    cases.append({"name": name, "manifest": manifest})
                    # Force a real source edit during actual producer copying,
                    # not a mocked identity or timing-dependent background race.
                    tracked = images / bytes.fromhex(packet["mutation"]["path"]).decode("ascii")
                    original = tracked.read_bytes()
                    before = eml.repo_identity(images)
                    copy = eml.copy_and_hash
                    def mutate(source, destination):
                        result = copy(source, destination)
                        tracked.write_bytes(bytes.fromhex(packet["mutation"]["body"]))
                        return result
                    refused = root / (name + "-refused")
                    args.out = str(refused)
                    with mock.patch.object(eml, "copy_and_hash", mutate):
                        try:
                            eml.emit(args)
                        except eml.Refusal as error:
                            assert "changed while the set was being recorded" in str(error)
                        else:
                            raise AssertionError("mutating producer unexpectedly succeeded")
                    after = eml.repo_identity(images)
                    assert after != before and not refused.exists()
                    assert not list(root.glob(f".{refused.name}.*"))
                    if capture_observations:
                        packet["mutation"]["after"] = after
                    else:
                        assert after == packet["mutation"]["after"]
                    tracked.write_bytes(original)
    return cases


if __name__ == "__main__":
    if not __debug__:
        raise SystemExit("reference runner requires assertions; do not use Python -O")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="exact prepared f5ed151 scripts directory")
    parser.add_argument("packet", type=Path, help="hash-pinned baseline neutral observation packet")
    parser.add_argument("manifests", type=Path, help="hash-pinned same-subject manifest capture")
    parser.add_argument("rootfs", type=Path, help="hash-pinned neutral rootfs packet")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--capture", action="store_true", help="emit fresh manifest JSON to stdout")
    mode.add_argument("--capture-observations", action="store_true",
                      help="emit a new observation packet; never overwrite the input")
    args = parser.parse_args()
    source, path, manifests, rootfs = args.source, args.packet, args.manifests, args.rootfs
    data = path.read_bytes()
    assert hashlib.sha256(data).hexdigest() == DATA_HASH
    expected = manifests.read_bytes()
    assert hashlib.sha256(expected).hexdigest() == MANIFESTS_HASH
    packet = json.loads(data)
    cases = run(source, packet, rootfs_data(rootfs), args.capture_observations)
    if args.capture:
        print(json.dumps(cases, separators=(",", ":"), sort_keys=True))
    elif args.capture_observations:
        assert cases == json.loads(expected), "observation capture changed same-subject manifests"
        output = (json.dumps(packet, indent=2) + "\n").encode()
        print(output.decode(), end="")
        print(f"Captured observation packet SHA256 {hashlib.sha256(output).hexdigest()}",
              file=sys.stderr)
    else:
        assert cases == json.loads(expected)
        print("4 V2 vectors, shared observation matrix, 8 same-subject manifests and mutation refusals: PASS")
