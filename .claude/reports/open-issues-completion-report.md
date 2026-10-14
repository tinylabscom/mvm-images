# Implementation report — open image-producer issues

**Plan:** `.claude/plans/address-open-issues.md`
**Branch:** `fix/64-artifact-base-contract`
**Base:** current remote main `80d0a0484aeb2a5a240f7d30a5ac5cfad4745623`
**Status:** PARTIAL — producer code reviewed and host-validated; CI, paired and release evidence pending

## Ownership and coordination

- This session owns the remaining mvm-images #64 artifact-marker correctness,
  producer boot-witness infrastructure, and #67 Python producer integration/PR
  and platform gates.
- The original PS-21 session explicitly handed off the Python producer and
  agreed not to edit it concurrently. Its mvm consumer remains in PR #4436,
  branch `feat/3730-client-boundary-completion`; backend migration and final
  consumer execution evidence are still in progress.
- The PS-15 release session retains CLI/runtime packaging/publication ownership.
  It has been asked for actual compatible release and installed-boot evidence.
  No publication or new release pins are authorized by this report.
- The earlier migration session was notified that its landed #65/#66/#68 work
  is preserved and asked about native runner/evidence ownership.

## Agreed paired contracts

- New local sets remain schema 3/base-image contract 1, images-only checkout
  identity and Linux-only roles. Do not restore the old producer's schema-2
  runtime/source fields.
- Clean identity is exactly `{"state":"clean"}`. Dirty identity is exactly
  `{"state":"dirty","fingerprint_version":2,"fingerprint":"<lowercase sha256>"}`.
  The PS-21 owner checked the actual strict Rust parser/serializer, including
  refusal of explicit null, old dirty versions and unknown versions.
- Partial local sets without a workload root retain the checkout's expected
  compatibility declaration. Every supplied workload root must provide actual
  strict marker evidence; invalid roots never fall back to source metadata.
- Local artifact inspection is in-process on both producer implementations.
  Release/build inspection may use explicitly provisioned read-only debugfs.

## Implemented so far

- Release assembler derives its declaration from all six actual workload ext4
  roots selected through `member_specs()`, preserving all 17 release members.
- `scripts/ext4_contract.py` supplies one pure strict marker rule and a
  fail-closed debugfs adapter, including semantic errors on successful tool exit.
- Release assembly checks hashes around marker inspection and before manifest
  emission; release CI provides e2fsprogs through the existing nixpkgs pin.
- `scripts/local_ext4_contract.py` reads actual guest inodes without mounting,
  extracting, following symlinks or launching a utility. It shares the marker
  rule, requires pinned `ext4==1.4.1`, and refuses corrupt/unsupported state.
- A fixed digest-checked ext4 test artifact allows cross-platform local-reader
  tests without a filesystem creation subprocess.
- The standalone test-owned boot payload and QEMU harness now cover both
  architectures, observing a bound guest vsock driver and loopback-only network
  inventory before mounting the actual base read-only and checking the marker.
- Build CI runs three native published-root probes per architecture and checks
  rootless dev without adding publication members. Release CI boots all six
  roots against the assembled manifest before signing, with separate diagnostic
  probe/witness artifacts and mandatory vsock.
- Boot evidence refuses host-side mutation of the manifest, kernels, roots,
  sidecars or probes; schema/contract values must be bare JSON integers.
- README documents the isolated pinned environment, fixture-tool requirements,
  V2 migration and actual-artifact evidence rule.
- Recovered V2 checkout observation/config/index/encoding and in-process
  repository fixtures, retaining schema 3/base 1 and images-only identities.
  All four literal encoding vectors and the real dirty fixture remain pinned.
- Local emission now inspects the actual copied workload ext4 roots, refuses
  disagreement/invalid supplied roots without fallback, and rechecks every
  staged artifact digest before publication. Partial sets with no workload root
  retain the agreed source compatibility declaration.
- Pinned the Python dependency closure and provisioned Python 3.12 explicitly
  for ordinary and Linux/Windows filesystem CI. Ordinary CI installs e2fsprogs
  for its real fixture tests; neither platform fingerprint job invokes a VMM.

## Verified source handoff

Read-only recovery source:
`/Users/auser/work/tinylabs/mvmco/mvm-images/.delta/worktrees/cyk9aedhfj1x/mvm-images`,
branch `feat/ps21-worktree-fingerprint-v2`, HEAD `9cb4525`.
Changes are uncommitted, not a landed or final validated producer commit.

Recover only identity/encoding/fixture/dependency changes from
`scripts/emit-local-manifest.py`, `scripts/tests/test_local_mvm.py`,
`scripts/requirements-local-manifest.txt` and lint CI. Leave unrelated
`.ignore`/`.gitignore` edits in that source checkout untouched.

The paired owner confirmed these existing literal vectors:

| Vector | SHA-256 |
| --- | --- |
| Empty canonical records | `f05485947d595bce2f26829620e3dfcdd28c7248b6dc0d20681c1ad2699b1ecb` |
| Raw untracked path | `06e6284c46206320bd0679940642175026ef97888a63afd2e166c5119ad88787` |
| Conflict/raw/link | `7ff6f04e29602de8984dde4663b9b41edd4b441d37c66e74d0c477d0b175171d` |
| Gitlink | `e535f6bf8dc4488cf36988106591011f5fdcbe778163423d006fc3bfe62c1cd8` |
| Shared real dirty fixture | `62287e21e3b30dc2978c2e7e05f65e9770e6a641048161493425b0799c734bae` |

Real fixture HEAD: `d40e7ed3d340d03c7ad6910387c60943c0ff3d0d`.
These are agreed test inputs, not a claim final cross-language execution passed.

## Validation results to date

- Artifact-validation worker:
  `PATH=/opt/homebrew/opt/e2fsprogs/sbin:$PATH just release-check`:
  111 Python tests and five BDD scenarios passed; no test skips.
  Focused assembler, Linux-layer and affected workflow suites also passed.
- Parent:
  pinned isolated environment
  `python -m unittest discover -s scripts/tests -p test_local_ext4_contract.py -v`:
  four tests passed, including real ext4 read, read-only/no-process behavior,
  altered-marker/corrupt-image refusal and dependency-version refusal.
- Parent combined artifact/boot integration:
  `PATH="<pinned isolated environment>/bin:/opt/homebrew/opt/e2fsprogs/sbin:$PATH" just release-check`:
  132 Python tests and five BDD scenarios passed before the two additional
  boot-input mutation/bare-integer regressions.
- Those two regressions were then validated with
  `python3 -m unittest discover -s scripts/tests -p test_boot_base_contract.py -v`:
  all 15 tests passed. Nix parsing of the probe and root flake passed.
  `just e2e-base-contract --help`, affected workflow tests, action pins and
  `git diff --check` also passed.
- Definitive pinned pre-V2 gate in the durable ignored workspace `.venv`:
  `PATH="$PWD/.venv/bin:/opt/homebrew/opt/e2fsprogs/sbin:$PATH" just release-check`:
  134 Python tests and five BDD scenarios passed. Verified Dulwich 0.24.10
  has no native object extension in this environment.
- Fresh independent artifact-trust review found no concrete must-fix findings.
  It passed 32 focused tests plus eight direct real-filesystem checks; it
  excluded the then-pending V2/boot/local-emitter integration.
  Parent retained its suggested real ancestor-symlink/special-mode regression:
  all five local-reader tests now pass (added after the 134-test snapshot).
- The host-presence check warned `/dev/vhost-vsock` is unavailable. The host is
  Darwin, with no configured Linux Nix builder found. No Linux builds, byte
  reproduction, VMM boots or installed-consumer executions were run here.

Counts above belong to their named snapshots, not the final integrated suite.

### Latest integrated producer validation

After recovering V2 and integrating copied-root inspection:

- `PURE=1 .venv/bin/python -m pip install -r scripts/requirements-local-manifest.txt`
  completed with the declared pins; `pip check` passed.
- `.venv/bin/python -m unittest discover -s scripts/tests -p test_local_mvm.py -v`:
  **59 executed passes, one APFS raw-name capability skip (60 tests total)**.
  Portable mode/link/deletion tests and literal raw-byte vectors still execute.
- `PATH="$PWD/.venv/bin:/opt/homebrew/opt/e2fsprogs/sbin:$PATH" just release-check`:
  **168 executed passes, one capability skip (169 tests total)**, five BDD
  scenarios passed; action pins and static networking/rootless contracts passed.
- Nix parsing of the probe and flake, `git diff --check`, `just manifest --help`
  and `just e2e-base-contract --help` passed in the declared environment.
  A first manifest-help invocation outside that environment failed with missing
  Dulwich; rerunning with the documented environment active succeeded.
- Linux/Windows CI, paired Rust execution, Nix Linux builds, reproduction,
  actual VMM boots and release publication remain unrun here.

### Review fixes and final flag-free policy

The combined review found four in-scope blockers, all fixed with regressions:
bounded raw serial reads now handle coalesced/partial lines; Git-compatible
booleans validate before clean-result shortcuts; actual empty `sdir` extensions
hydrate sparse indexes; system context no longer guesses POSIX compiled paths.

The user approved fail-closed handling but requested no manual environment setup.
The paired Rust owner agreed the unreleased V2 default internally excludes host
system config/attributes while retaining repository and declared HOME/XDG/global
rules. Explicit overrides remain independent and invalid/unsupported requests
refuse. One frozen context is shared throughout each repository lookup; Windows
prefix discovery occurs only for contributing explicit sources/resources.
The coordination contract is `scripts/checkout-identity-v2.md`.

Final host gate: **188 tests total, 187 executed passes, one explicit APFS
raw-filename capability skip**, and five BDD scenarios passed. Action-pin
self-test, dependency consistency, Nix parse checks and `git diff --check` passed.
An actual CLI local-manifest invocation against this working checkout, with all
three system controls removed, emitted schema 3/base contract 1 and V2 dirty
identity using a real ext4 test artifact. This is tooling evidence, not a Nix
Linux build or a signed set.

Independent follow-up closed the context-once finding against current parent
source: **76 tests total, 75 executed passes, one capability skip**. No remaining
concrete host-implementation blockers were found in that scoped review.

## Deviations and decisions

- A1 originally proposed reusing debugfs for local roots. The paired
  no-subprocess boundary requires an in-process local reader. Selected the
  pinned MIT `ext4` library, rather than implementing a new filesystem parser
  or adding an AGPL filesystem dependency. Its dependency closure is pinned in
  the producer environment; no mvm source/native runtime input is added.
- Test-only boot payloads must stay outside publication inventory and SLSA
  member subjects. They inspect the actual read-only candidate roots; they are
  not replacement runtime roles.

## Remaining gates

- Obtain actual both-architecture build/boot evidence on capable runners for
  the integrated probe/harness/workflow changes.
- Complete the independent final combined security/technical review and resolve
  any new concrete findings from CI or paired validation; the current combined
  host review and its corrections are complete.
- Open reviewable producer PR(s), obtain Linux/Windows identity evidence and
  both-architecture build/reproduction/boot evidence, then land normally.
- Confirm final Rust consumer schema-3/artifact-reader and vector parity.
- Wait for actual compatible published CLI + signed runtime + authenticated
  installed Linux Firecracker/Darwin HVF report; only then configure real pins
  and request protected image publication. Consumer lock advance comes last.

No issue acceptance checkbox is marked complete by this report.

## PR #69 first CI and platform corrections

Initial immutable handoff: `a01ad750ac8c0fec0231050d72560af3e8b3993a`,
draft https://github.com/tinylabscom/mvm-images/pull/69.

- Lint run `38498950303`: ordinary Linux tests, action pins and Linux
  fingerprint job succeeded; Windows fingerprint job failed.
- Build run `38498950233`: both architecture jobs built the builder artifacts,
  then refused the expected absent `/init` because Ubuntu debugfs prefixes its
  precise missing-path diagnostic with the operand rather than the command.
  No contract boots ran in those failed jobs.
- Reproduce run `38498950229`: all six canonical role/architecture jobs reported
  success for this exact initial commit. This is scoped CI evidence, not a live
  release or installed-product result.

Corrections retain strict validation:

- Optional lookup permits only the precise command- or operand-prefixed
  not-found diagnostic for its requested path, with no stdout; required
  marker errors and unrelated diagnostics still refuse.
- The local reader compares metadata within each query family: opening/final
  descriptor `fstat` and initial/final pathname `lstat`, preserving the opening
  device/inode gate. Heterogeneous Windows timestamp precision no longer causes
  false mutation refusal. Real mutation/replacement and staged digest checks
  remain enforced.

Corrected host `just release-check`: **192 total, 191 executed passes, one APFS
capability skip**, five BDD scenarios passed; `git diff --check` passed.
Native Windows and both-architecture build/boot reruns remain required.
