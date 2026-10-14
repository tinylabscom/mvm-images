# Plan: finish the open mvm-images issues

## Outcome and scope

Finish issues [#49](https://github.com/tinylabscom/mvm-images/issues/49),
[#64](https://github.com/tinylabscom/mvm-images/issues/64), and
[#67](https://github.com/tinylabscom/mvm-images/issues/67) without reimplementing
landed work or confusing code/test progress with publication evidence.

This is a backlog execution plan, not authorization to merge, publish, run an
installer, change repository settings, or advance a lock. Recheck the issue
comments, PR heads and dependency state before execution.

### Snapshot used for this plan

- Local main: `25ddab3`; Linux-layer migration PR
  [#65](https://github.com/tinylabscom/mvm-images/pull/65) landed as
  `60007a5ecdd8387b19f6a9e21fc01e95e9dffda3`.
- Signed-instruction follow-up
  [#66](https://github.com/tinylabscom/mvm-images/pull/66) is merged.
- During planning, installer-gate PR
  [#68](https://github.com/tinylabscom/mvm-images/pull/68) merged as
  `80d0a0484aeb2a5a240f7d30a5ac5cfad4745623`, now remote main. The attached
  checkout remains at `25ddab3`; reconcile with current main before implementing
  either workstream. Do not repeat the merged installer fix.
- mvm runtime-packaging PR
  [#4238](https://github.com/tinylabscom/mvm/pull/4238) is merged as
  `d1005d3189f2df344bbed629244c6a0f03e1f20a`.
  mvm #4105 and #4106 are closed. These are code milestones, not a released CLI.
- The latest published CLI found is `v0.22.0`; no compatible published
  CLI/full-runtime pair was found. Linux-only publication remains blocked.
- [mvm #4103](https://github.com/tinylabscom/mvm/issues/4103), shipping the
  guest runtime as a signed CLI release asset, remains open.
- #67 comments describe an isolated Python/Rust V2 implementation and focused
  validation. This main checkout still uses Git subprocesses and V1 fingerprints.
  Treat the comments as a handoff to reconcile, not proof the feature landed.
- The paired consumer work is now open
  [mvm PR #4436](https://github.com/tinylabscom/mvm/pull/4436). Its draft V2
  document contains a schema-version discrepancy that must be settled before
  implementation; it is not a landed protocol.
- `.claude/code-reviews/issue-49-readiness.md` is historical evidence for named
  commits. Its advice to continue PR #65 and its draft/open upstream statuses
  are no longer current.

## Priority and execution graph

| Priority | Work | Recommended owner | Dependency / completion gate |
| --- | --- | --- | --- |
| Landed; verify integration | Retain installer-gate fix #68 on current main | Images release owner, paired mvm installer owner | Confirm companion installer stages all runtime proofs; real released-installer evidence still needed |
| P1 | Complete artifact-derived contract checks and boot witnesses for #64 | Images producer owner | Paired consumer agreement; real built-rootfs and boot evidence |
| P1, parallel | Reconcile and land existing V2 fingerprint work for #67 | PS-21 Python owner and mvm #4412 owner | Shared contract/vectors, platform validation, paired review |
| P2, externally blocked | Complete release-readiness evidence for #49/#64 | mvm release owner and images release owner | Compatible published CLI + signed runtime + installed boots |
| Last | Publish Linux-only set, then advance consumer lock | Authorized release owners | Every gate above; protected release workflow succeeds |

#67 does not depend on an image release and must not add an mvm merge-queue edge.
#49/#64 do not need PS-21 completion merely to publish. The workstreams can run
in parallel, but both touch `scripts/emit-local-manifest.py` and its tests:
coordinate those edits explicitly rather than maintaining divergent emitters.

## Inherited constraints / non-goals

- Build only canonical Linux definitions. No mvm source input, source mirror,
  runtime role, source-drift procedure, or application-specific base.
- Preserve schema 3/base contract 1, builder boot ABI 2, fixed identities,
  generic default/rootless profiles, both architectures and all 17 producer
  members including dev variants.
- Keep NIC-less, vsock-only networking and the separate rootless kernel floor.
- Preserve action pins, immutable releases, provenance, cosign identity,
  revocation and byte-for-byte reproduction. Derive subjects from the assembler
  member table, not a duplicated workflow inventory.
- Leave historical schema-2 sets and the consumer's current lock untouched.
- No lock regeneration or image publication is part of #67.
- No changes to signed instruction files are needed for this plan.
- Do not rewrite already-landed #65 or restart the fingerprint implementation
  described in the paired issues.

## Workstream A — finish #64's actual-artifact contract

### Context to read

- `images/base-image-contract.nix`; `images/mk-rootfs.nix:82-83`:
  canonical declaration and exact marker bytes/mode.
- `scripts/check_linux_layer.py:39-85`: existing ext4 inspection and fixed
  identity/runtime-exclusion checks.
- `scripts/assemble-release.py:63-170,173-180,450`: sole member inventory,
  source-derived contract and release compatibility output.
- `scripts/emit-local-manifest.py:218-223,379-401`: local source-derived
  contract and compatibility output.
- `.github/workflows/build.yml:45-91`: built-root checks and current
  x86_64-only rootless smoke.
- `images/rootless-tenant/image.nix:112-154`; `scripts/e2e_boot.py`:
  probe-owned init and direct-VMM harness.
- `scripts/tests/test_assemble_release.py`,
  `scripts/tests/test_local_mvm.py`, `scripts/tests/test_linux_layer.py`.
- Issue #64 and mvm's base-contract consumer/ADR-054.

### A1. Close the source-versus-artifact gap

The marker is already generated, protocol metadata is already removed, and
build CI checks rootfs bytes. However, both manifest emitters currently read
the Nix source declaration rather than the supplied built workload roots.
#64 explicitly requires reading the built marker and refusing disagreement.

1. Keep the Nix declaration as the expected value, but obtain the emitted
   contract from every actual workload rootfs supplied to the emitter.
2. Factor one strict marker-validation rule: regular file, exact supported
   decimal/newline bytes, no symlink, no missing/malformed marker. Reuse the
   existing ext4 checker where its execution boundary fits.
3. In the release assembler, enumerate roots from `member_specs()` and refuse
   any disagreement between roots or with the declaration before generating
   signed candidate metadata. Include default production/dev and rootless
   published roots on both architectures: six workload ext4 artifacts.
   Rootless dev is buildable but not part of the 17-member publication table;
   check that supported mode in build CI without adding a publication member.
4. In the local emitter, validate every supplied workload rootfs; keep the
   existing ability to describe a partial local set. For a set containing no
   workload rootfs, explicitly preserve and document its compatibility rule
   with the strict Rust consumer rather than silently inventing a new one.
5. Coordinate the local marker-reading boundary with mvm #4412's typed,
   subprocess-free Rust producer. Do not introduce a Python/Git delegate there.
   Python #67 prohibits Git subprocesses; it is not permission to change the
   shared manifest contract independently.
6. Replace dummy-rootfs fixtures where necessary with valid artifact fixtures.
   Test absent marker, symlink/non-file marker, CRLF, extra bytes, unsupported
   version, mixed versions, and a source declaring 1 while supplied bytes do not.
   Refusal must preserve existing output and clean private staging.
7. Provision the artifact inspector explicitly in release CI (for example,
   pinned `e2fsprogs`/`debugfs` through the existing Nix tool boundary), and
   add a workflow regression test. Do not assume an ambient runner package.

Validation:

```sh
python3 -m unittest discover -s scripts/tests -p test_assemble_release.py -v
python3 -m unittest discover -s scripts/tests -p test_local_mvm.py -v
python3 -m unittest discover -s scripts/tests -p test_linux_layer.py -v
just release-check
```

### A2. Witness the contract during boot

1. Add a probe assertion and machine-readable witness for the mounted base
   marker matching the candidate manifest's declared contract.
2. Cover every published workload rootfs on both architectures, including dev.
   Preserve direct VMM invocation, explicit devices, vsock, no NIC, probe-owned
   init, no runtime overlay and no mvm-built bytes.
3. Do not modify sealed production bases to bake test runtime helpers. Use a
   probe-owned boot payload and test the actual candidate rootfs read-only.
   The existing rootless `smoke` output alone is not evidence for every
   published rootfs.
4. Retain UID 901, namespaces, delegated cgroup v2, OCI-tool and loopback-only
   rootless assertions. Add real aarch64 coverage; current build workflow only
   boots the rootless probe on x86_64.
5. Update producer docs and both-architecture workflow/tests together if the
   witness surface changes; do not add image roles.

Validation: focused harness tests, `just release-check`, real artifact checks
using `scripts/check_linux_layer.py`, and direct-VMM boots on configured Linux
runners. Record exact kernels/rootfs hashes, manifest digest, VMM/device plan,
assertions, runner architecture, and any skip. A successful offline ext4 read
does not satisfy the boot-witness item.

**#64 done:** both emitters derive agreement from supplied artifact bytes;
strict consumer parity passes; required boots witness the declared marker;
reviewed code lands. Publish only with Workstream C's release gate.

## Workstream B — finish #67 without duplicating the handoff

### Context to read

- `scripts/emit-local-manifest.py:118-215`: current Git subprocess probe,
  textual V1 status/diff fingerprint and root discovery.
- `scripts/tests/test_local_mvm.py`: current fixtures, identity/refusal tests.
- `README.md:84-87`; `justfile:90-95,177-181`: local-dev contract and entrypoints.
- Latest comments on #67 and paired
  [mvm #4412](https://github.com/tinylabscom/mvm/issues/4412).
- [mvm PR #4436](https://github.com/tinylabscom/mvm/pull/4436), particularly
  `crates/mvm-build/src/image_source/checkout-identity-v2.md` and strict parser
  changes. Read the exact reviewed head, not just its description.
- Parent completion gate
  [mvm #4416](https://github.com/tinylabscom/mvm/issues/4416).

### B1. Reconcile the existing implementation

Obtain the exact feature branch/commit or worktree handoff from its current
owner. Diff it against current main: the reported 89-test snapshot predates
this checkout's 102-test surface. Preserve the landed Linux-only schema,
images-only identity, retired-role refusals and base-contract work.

Read the agreed normative V2 encoding and identical vector files from both
implementations. Do not choose another encoding/library from scratch.
Before adopting the handoff, resolve the draft document's statement that the
image-set schema "remains 2": this producer and its repository contract require
schema 3. Recommended resolution is to correct the stale protocol wording,
not downgrade this producer. Obtain paired owner confirmation.

Confirm the draft's exact dirty record shape
`{"state":"dirty","fingerprint_version":2,"fingerprint":"<lowercase sha256>"}`
and clean shape `{"state":"clean"}` against the actual strict consumer.
Do not introduce an alternative scheme field or emit extra null/version fields
on clean records. These details remain gated on protocol agreement.

Reported Python dependency is pure-Python Dulwich `0.24.10`; declare/pin it
and provide a reproducible test/runtime environment, including CI and the
documented `just manifest` workflow. No ambient package assumption.

### B2. Complete behavior and migration checks

- Replace HEAD/index/worktree/root discovery Git invocations with in-process
  APIs and affected Git-created fixtures with in-process repositories.
- Preserve staged/unstaged/conflict/mode, symlink target bytes, binary/raw-byte
  paths, untracked/ignore, linked worktrees, submodules and config precedence.
  Preserve repository-redirection isolation; inaccessible/unsupported states
  refuse rather than appearing clean.
- Emit the exact explicit scheme metadata accepted by the strict Rust consumer.
  Preserve safe legacy clean identities; reject V1 dirty identities with a
  rebuild instruction. Never reinterpret an old digest as V2.
- Preserve artifact hashing/copying, output-root restrictions, private staging,
  before/after mutation refusal, timestamps and cleanup.
- Run identical byte-level vectors in Python and Rust, plus actual filesystem
  cases. Keep mode/link/deletion tests even when a platform cannot create a
  raw-byte filename; report capability skips explicitly.
- Witness Python and Rust manifest parity through the actual strict consumer
  for both architectures and unchanged/changed checkout refusal.
- Replace the current mock-only decisive mutation test with an actual
  checkout mutation between the production emitter's before/after observations.
  Add an explicit no-Git-subprocess regression check over the identity code
  and affected fixtures; library imports alone do not prove the boundary.

### B3. Validate and land the paired work

Run focused Python tests and the full ordinary gates in the pinned environment:

```sh
python3 -m unittest discover -s scripts/tests -p test_local_mvm.py -v
just release-check
git diff --check
```

The paired mvm owner runs its exact focused suites, strict-consumer tests,
dependency policy and required full/platform gates. Obtain Linux and Windows
real-filesystem evidence, not only the reported macOS pass. Fresh mvm #4412
comments supersede earlier focused passes: the latest reported full Rust probe
suite still has two failures (malformed config acceptance and fixture/global
config interference). Fix them without ignored tests or relaxed fail-closed
behavior. Audit native/FFI and vendored licensing in the owning mvm repository;
Python success does not complete those paired gates.

Review and land atomic coordinated changes with explicit wire compatibility
ordering. Update #67 acceptance only with exact commits, vector digest,
commands and platform results. No image build, publication, pin advance or
new merge-queue dependency is required to close this producer issue.

## Workstream C — finish #49 release readiness

### C1. Retain the merged installer-gate fix and verify integration

PR #68 is merged; start implementation from current main rather than create
a second fix. It fetches the installer at the
authenticated release commit instead of assuming an installer exists inside
the CLI archive. Check the companion mvm installer stages the complete signed
runtime quartet and that missing/empty/wrong-commit installers refuse.

Read `scripts/verify-released-consumer.py`,
`scripts/tests/test_released_consumer.py`, and the PR diff. Run:

```sh
python3 -m unittest discover -s scripts/tests -p test_released_consumer.py -v
just release-check
```

Retain the merged regression tests. Fixture passes do not prove real
released-installer interoperability.

### C2. Collect release evidence in the required order

1. Confirm the compatible consumer changes are in the exact CLI release commit.
2. mvm's release owner publishes the real compatible CLI and signed full
   runtime. A merged packaging PR, bare tag or proposed version is insufficient.
   Track the remaining published-asset work in mvm #4103.
3. Obtain authenticated post-publication installed-CLI boot evidence for that
   CLI/runtime pair, including required native backend/platform coverage.
   The existing consumer gate specifically requires installed Linux
   Firecracker and Darwin HVF boots; generic smoke or TCG reports do not satisfy it.
4. With approval, configure actual `IMAGE_CONSUMER_TAG`,
   `IMAGE_CONSUMER_COMMIT`, and `IMAGE_CONSUMER_RUN_ID` only from that evidence.
   Prove cross-repository artifact/contents/attestation access using the real
   release credentials; supply any read token via Actions secrets.
5. Build and reproduce the exact image release candidate on x86_64 and aarch64.
   Preserve all 17 producer members, default dev outputs and the smoke pack.
6. Authenticate/install that same released CLI/runtime pair through the fixed
   gate, then verify the signed complete candidate. This is the dedicated
   release-consumer gate, not an image test launching mvmctl.
7. Only after all prerequisites, request authorized protected publication.
   `.github/workflows/release.yml:23-34` requires Build and Reproduce;
   `:109-126` requires the authenticated released verifier.
8. After the immutable signed set actually publishes and verifies, the mvm
   owner advances its digest-pinned `images.lock`. Preserve historical sets.

### C3. Treat skipped native boots as missing evidence

PR #68 reports successful Build and Image-owned CI checks, but the Firecracker
job in run `38341210670` skipped artifact download, install, boot and witness
upload after KVM preflight. It is not Firecracker boot evidence.

Arrange capable approved Linux/KVM runners and obtain actual native boots.
Do not substitute QEMU TCG/macOS TCG for KVM/HVF or infer complete platform
coverage from a green aggregate job. Reuse historical build/reproduction
reports only for their named commits; rerun changed candidate lanes.

**#49 done:** remaining producer fixes land; compatible released CLI/runtime
and installed boots exist; both-architecture build/reproduction and required
native evidence pass; real authenticated publication gate succeeds; ordered
publication/consumer rollout is recorded. Update the issue rather than mark
its old unchecked implementation list complete solely from source inspection.

## Validation evidence from planning

Actually run on this Darwin checkout:

- `just release-check`: action pins passed; NIC-less and rootless static
  contracts passed; 102 Python tests passed; five BDD scenarios passed.
- Host `/dev/vhost-vsock` is unavailable; checks reported the skipped presence
  check. These results are fast host validation, not Linux build/boot evidence.
- GitHub issues/comments, PR state/check summaries, published CLI releases,
  and the Firecracker job's individual step statuses were inspected.

Not run during planning: Linux Nix builds, byte reproduction, real VMM boots,
paired Rust tests, installer execution, release workflow/publication, or
consumer lock changes.

## Questions to settle at execution handoff

The architecture and publication order are already settled upstream; no new
product/architecture choice is needed for this backlog plan. Before each
implementation starts, obtain these concrete inputs:

1. Exact #67 feature handoff, normative contract/vector revisions and paired
   consumer commit. Resolve PR #4436's schema-2 wording versus this producer's
   schema 3 and confirm `fingerprint_version: 2` on dirty records only.
   Recommended: resume owned work, preserve schema 3, and correct stale draft
   wording after paired owner confirmation; do not restart the implementation.
2. One artifact-marker inspection interface and partial-local-set rule shared
   with the Rust producer. Recommended: preserve partial sets and reuse strict
   existing validation semantics; explicitly agree the no-rootfs case.
3. Capable Linux/KVM/aarch64 runners and native backend owners.
   Recommended: use existing approved execution boundaries; never bypass
   evidence requirements because the current host is Darwin.
4. Real release pins/report once published. These do not exist in this plan
   and must not be fabricated.

Confidence: high in prioritization/dependency order; medium in one-pass
execution until the fingerprint handoff, artifact-reader boundary and capable
runners are resolved. Release completion time is externally constrained, so
no date is promised.
