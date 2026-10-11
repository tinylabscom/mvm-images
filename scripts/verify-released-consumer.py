#!/usr/bin/env python3
"""Authenticate an actually published CLI and its installed-boot evidence.

Pins select evidence; they never override verification. Missing releases,
expired artifacts, incomplete boots or unavailable signature/provenance checks
refuse publication. This script does not build or boot mvm source.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import zipfile

REPOSITORY = "tinylabscom/mvm"
WORKFLOW = ".github/workflows/release.yml"
JOB = "published-installed-boot"
ARTIFACT = "published-installed-boot-v1"
REPORT = "installed-boot.json"
ISSUER = "https://token.actions.githubusercontent.com"
BOOTS = {
    ("x86_64-unknown-linux-gnu", "x86_64", "firecracker"),
    ("aarch64-apple-darwin", "aarch64", "hvf"),
}


class Refusal(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise Refusal(message)


def command(*args, output=None):
    result = subprocess.run(args, stdout=output or subprocess.PIPE,
                            stderr=subprocess.PIPE, check=False)
    require(result.returncode == 0,
            f"{args[0]} {args[1]} failed: {result.stderr.decode(errors='replace').strip()}")
    return result.stdout


def api(path):
    return json.loads(command("gh", "api", "--hostname", "github.com",
                              f"repos/{REPOSITORY}/{path}"))


def timestamp(value):
    require(isinstance(value, str), "missing evidence timestamp")
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def assets_for(tag):
    return {
        "mvmctl-x86_64-unknown-linux-gnu.tar.gz",
        "mvmctl-aarch64-apple-darwin.tar.gz",
        f"mvm-guest-bins-{tag}.tar.gz",
    }


def validate_pins(tag, commit, run_id):
    require(re.fullmatch(r"v\d+\.\d+\.\d+", tag or ""), "pin an actual stable CLI release tag")
    require(re.fullmatch(r"[0-9a-f]{40}", commit or ""), "pin the resolved CLI release commit")
    require(re.fullmatch(r"[1-9]\d*", run_id or ""), "pin an authoritative release run ID")


def validate_run(release, run, jobs, tag, commit, run_id):
    require(release.get("tag_name") == tag and release.get("draft") is False
            and release.get("prerelease") is False, "not a published stable pinned CLI release")
    published = timestamp(release.get("published_at"))
    require(str(run.get("id")) == run_id and run.get("head_sha") == commit
            and run.get("head_branch") == tag and run.get("event") == "push"
            and run.get("path") == WORKFLOW and run.get("status") == "completed"
            and run.get("conclusion") == "success"
            and run.get("repository", {}).get("full_name") == REPOSITORY,
            "run is not the successful authoritative release tag-push run")
    matching = [job for job in jobs if job.get("name") == JOB]
    require(len(matching) == 1, "missing or duplicate installed-release boot job")
    job = matching[0]
    require(job.get("status") == "completed" and job.get("conclusion") == "success"
            and timestamp(job.get("started_at")) >= published,
            "installed-boot job must succeed after publication")
    return job


def validate_report(report, tag, commit, run_id, attempt):
    require(isinstance(report, dict), "installed-boot report must be an object")
    require(type(report.get("schema_version")) is int and report["schema_version"] == 1,
            "unsupported installed-boot report schema")
    cli = report.get("cli", {})
    require(isinstance(cli, dict), "missing report CLI identity")
    require(cli.get("tag") == tag and cli.get("commit") == commit, "report CLI pins disagree")
    expected = {"repository": REPOSITORY, "path": WORKFLOW, "run_id": run_id,
                "run_attempt": str(attempt), "event": "push", "ref": f"refs/tags/{tag}"}
    workflow = report.get("workflow", {})
    require(isinstance(workflow, dict), "missing report workflow identity")
    require(all(workflow.get(key) == value for key, value in expected.items()),
            "report workflow provenance disagrees")
    rows = report.get("assets", [])
    require(isinstance(rows, list) and len(rows) == 3, "report must bind all three released archives")
    digests = {}
    for row in rows:
        require(isinstance(row, dict) and row.get("name") in assets_for(tag),
                "unexpected report archive")
        name = row["name"]
        require(name not in digests and re.fullmatch(r"[0-9a-f]{64}", row.get("sha256", "")),
                "duplicate archive or invalid report digest")
        digests[name] = row["sha256"]
    require(set(digests) == assets_for(tag), "missing released archive")
    witnessed = set()
    rows = report.get("boots", [])
    require(isinstance(rows, list), "missing boot outcomes")
    for row in rows:
        require(isinstance(row, dict), "invalid boot outcome")
        key = tuple(row.get(field) for field in ("host_target", "guest_arch", "backend"))
        require(key not in witnessed and row.get("result") == "success",
                "duplicate or unsuccessful boot outcome")
        witnessed.add(key)
    require(BOOTS <= witnessed, "missing Linux Firecracker or Darwin HVF installed boot")
    return digests


def verify_attestation(path, tag, commit, bundle=None):
    identity = f"https://github.com/{REPOSITORY}/{WORKFLOW}@refs/tags/{tag}"
    args = ["gh", "attestation", "verify", str(path), "--repo", REPOSITORY,
            "--hostname", "github.com", "--signer-workflow", f"{REPOSITORY}/{WORKFLOW}",
            "--source-ref", f"refs/tags/{tag}", "--source-digest", commit,
            "--cert-identity", identity, "--cert-oidc-issuer", ISSUER]
    if bundle is not None:
        args.extend(("--bundle", str(bundle)))
    command(*args)


def verify_blob(path, bundle, tag):
    identity = f"https://github.com/{REPOSITORY}/{WORKFLOW}@refs/tags/{tag}"
    command("cosign", "verify-blob", "--bundle", str(bundle),
            "--certificate-identity", identity, "--certificate-oidc-issuer", ISSUER, str(path))


def verify_signed(path, bundle, tag, commit):
    verify_blob(path, bundle, tag)
    verify_attestation(path, tag, commit)


def checksum_table(body):
    result = {}
    for line in body.splitlines():
        match = re.fullmatch(r"([0-9a-f]{64}) [ *]([A-Za-z0-9._-]+)", line)
        require(match is not None, "malformed signed checksum table")
        digest, name = match.groups()
        require(name not in result, "duplicate signed checksum filename")
        result[name] = digest
    require(result, "empty signed checksum table")
    return result


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def release_download(assets, name, directory):
    matches = [asset for asset in assets if asset.get("name") == name]
    require(len(matches) == 1 and matches[0].get("state") == "uploaded",
            f"missing or ambiguous published asset: {name}")
    path = directory / name
    with path.open("xb") as stream:
        command("gh", "api", "--hostname", "github.com", "-H", "Accept: application/octet-stream",
                f"repos/{REPOSITORY}/releases/assets/{matches[0]['id']}", output=stream)
    require(path.stat().st_size > 0, f"empty published asset: {name}")
    return path


def verify(args):
    validate_pins(args.tag, args.commit, args.run_id)
    require(os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN"),
            "authenticated GitHub access is required")
    require(not args.out.exists(), "consumer installation directory must not already exist")
    args.out.mkdir(parents=True)
    release = api(f"releases/tags/{args.tag}")
    # Resolve annotated tags through the authenticated Git object API.
    obj = api(f"git/ref/tags/{args.tag}")["object"]
    for _ in range(8):
        if obj.get("type") != "tag":
            break
        obj = api(f"git/tags/{obj['sha']}")["object"]
    require(obj.get("type") == "commit" and obj.get("sha") == args.commit,
            "release tag does not resolve to the pinned commit")
    run = api(f"actions/runs/{args.run_id}")
    attempt = run.get("run_attempt")
    require(type(attempt) is int and attempt > 0, "missing authoritative run attempt")
    jobs = api(f"actions/runs/{args.run_id}/attempts/{attempt}/jobs?per_page=100")
    require(jobs.get("total_count", 101) <= 100, "cannot exhaustively admit release jobs")
    job = validate_run(release, run, jobs.get("jobs", []), args.tag, args.commit, args.run_id)
    listing = api(f"actions/runs/{args.run_id}/artifacts?per_page=100")
    require(listing.get("total_count", 101) <= 100, "cannot exhaustively admit run artifacts")
    matches = [item for item in listing.get("artifacts", []) if item.get("name") == ARTIFACT]
    require(len(matches) == 1 and matches[0].get("expired") is False,
            "missing, expired or ambiguous installed-boot artifact")
    artifact = matches[0]
    require(timestamp(artifact.get("created_at")) >= timestamp(job.get("started_at"))
            and timestamp(artifact.get("created_at")) <= timestamp(job.get("completed_at")),
            "installed-boot artifact was not produced by the admitted attempt/job")
    archive = args.out / "evidence.zip"
    with archive.open("xb") as stream:
        command("gh", "api", "--hostname", "github.com",
                f"repos/{REPOSITORY}/actions/artifacts/{artifact['id']}/zip",
                output=stream)
    require(artifact.get("digest") == f"sha256:{sha256(archive)}",
            "downloaded report artifact disagrees with authenticated API digest")
    with zipfile.ZipFile(archive) as zipped:
        for name in (REPORT, args.report_bundle):
            require(zipped.namelist().count(name) == 1, f"missing/duplicate report artifact {name}")
            require(zipped.getinfo(name).file_size <= 8 * 1024 * 1024, "oversized evidence")
            (args.out / name).write_bytes(zipped.read(name))
    report_path = args.out / REPORT
    # The report bundle is a build-provenance attestation, not a blob signature.
    verify_attestation(report_path, args.tag, args.commit, args.out / args.report_bundle)
    digests = validate_report(json.loads(report_path.read_text()), args.tag, args.commit,
                              args.run_id, attempt)
    assets = release.get("assets", [])
    checksums = release_download(assets, args.checksums, args.out)
    bundle = release_download(assets, args.checksums + args.bundle_suffix, args.out)
    verify_signed(checksums, bundle, args.tag, args.commit)
    table = checksum_table(checksums.read_text())
    for name, digest in digests.items():
        require(table.get(name) == digest, "report digest disagrees with signed published checksums")
        path = release_download(assets, name, args.out)
        bundle = release_download(assets, name + args.bundle_suffix, args.out)
        require(sha256(path) == digest, f"published archive digest mismatch: {name}")
        verify_signed(path, bundle, args.tag, args.commit)
    runtime_name = f"mvm-guest-bins-{args.tag}.tar.gz"
    proof = release_download(assets, runtime_name + ".sha256", args.out)
    proof_bundle = release_download(assets, runtime_name + ".sha256.bundle", args.out)
    verify_blob(proof, proof_bundle, args.tag)
    require(checksum_table(proof.read_text()).get(runtime_name) == digests[runtime_name],
            "published runtime checksum proof disagrees")
    install(args, digests)


def installer_environment(args, cli_digest):
    # The authenticated release installer is the only installation rule.
    # Inherited download/verifier/skip overrides must not weaken that rule.
    env = {key: value for key, value in os.environ.items()
           if not key.startswith("MVM_") and key not in ("GH_HOST", "GITHUB_API_URL",
                                                        "GITHUB_SERVER_URL")}
    prefix = args.out.resolve() / "installed"
    env.update(MVM_VERSION=args.tag, MVM_INSTALL_DIR=str(prefix / "bin"),
               MVM_INSTALL_LIB_DIR=str(prefix / "lib/mvm"),
               MVM_TRUSTED_ARCHIVE_SHA256=cli_digest, MVM_SKIP_BOOTSTRAP="1")
    return env


def install(args, digests):
    """Run the authenticated shipped installer, then verify what it installed."""
    require(sys.platform == "linux" and os.uname().machine == "x86_64",
            "candidate verifier installation requires the configured Linux x86_64 runner")
    cli_name = "mvmctl-x86_64-unknown-linux-gnu.tar.gz"
    top = "mvmctl-x86_64-unknown-linux-gnu"
    unpacked = args.out.resolve() / "archive"
    with tarfile.open(args.out / cli_name, "r:gz") as archive:
        members = archive.getmembers()
        names = set()
        for member in members:
            path = Path(member.name)
            require(not path.is_absolute() and ".." not in path.parts and path.parts
                    and path.parts[0] == top and (member.isfile() or member.isdir())
                    and path.as_posix() not in names, "unsafe or duplicate CLI archive member")
            names.add(path.as_posix())
        archive.extractall(unpacked, members=members, filter="data")
    installer = unpacked / top / "install.sh"
    expected_binary = unpacked / top / "mvmctl"
    require(installer.is_file() and expected_binary.is_file(), "release lacks installer or CLI")
    result = subprocess.run(["sh", str(installer)], cwd=installer.parent,
                            env=installer_environment(args, digests[cli_name]), check=False)
    require(result.returncode == 0, "authenticated release installer failed")
    binary = (args.out / "installed/bin/mvmctl").resolve(strict=True)
    require(binary.is_relative_to((args.out / "installed").resolve())
            and sha256(binary) == sha256(expected_binary), "installed CLI differs from verified release")
    runtime_name = f"mvm-guest-bins-{args.tag}.tar.gz"
    runtime_dir = binary.parent / "guest-runtime"
    for suffix in ("", ".sha256", ".bundle", ".sha256.bundle"):
        installed = runtime_dir / (runtime_name + suffix)
        require(installed.is_file()
                and installed.resolve().is_relative_to((args.out / "installed").resolve())
                and sha256(installed) == sha256(args.out / (runtime_name + suffix)),
                f"installed runtime/proof differs from authenticated published asset: {suffix}")
    (args.out / "mvmctl").symlink_to(binary)
    print(f"authenticated installed CLI {args.tag} at {args.commit}; evidence run {args.run_id}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--checksums", default="checksums-sha256.txt")
    parser.add_argument("--bundle-suffix", default=".bundle")
    parser.add_argument("--report-bundle", default="installed-boot.json.bundle")
    args = parser.parse_args()
    try:
        for value in (args.checksums, args.report_bundle):
            require(re.fullmatch(r"[A-Za-z0-9._-]+", value), "unsafe evidence filename")
        require(re.fullmatch(r"\.[A-Za-z0-9._-]+", args.bundle_suffix), "unsafe bundle suffix")
        verify(args)
    except (Refusal, OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile, tarfile.TarError) as error:
        print(f"released-consumer: refusing publication: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
