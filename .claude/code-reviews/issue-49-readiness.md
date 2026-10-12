# Issue #49 readiness review

Reviewed producer PR #65 at `1dcbbc45eafd6cd53f803a81cce97675abcb14b6`
against main `8455cc26d6bfc7e3ecc8ed6ef692f62dc670e6d2`.
This is a targeted readiness assessment, not release authorization or a
claim of exhaustive review.

## Change statistics

- Files changed: 56
- Lines added: 1,761
- Lines deleted: 4,354

## Recommendation

Continue issue #49 through the existing PR #65, rather than implement a
second migration on main. Do not publish a Linux-only set until the
compatible CLI and its authenticated installed-boot evidence exist.
The PR description's local-only validation section is now behind its CI
results; retain its publication blockers but refresh the validation evidence.

PR #57 is independent: it changes only the immutable checkout pin in
`.github/workflows/sign-instructions.yml`, from v6 to v7.0.1, preserving
`persist-credentials: false`. GitHub's v7.0.1 tag resolves to its proposed
`3d3c42e5aac5ba805825da76410c181273ba90b1` commit, which other workflows
already use. Its reported checks pass. Recommend landing it separately
after approval; it does not resolve #49.

## Producer coverage

The implementation removes the mvm flake input, mirrored sources and
source-drift procedures, runtime-overlay/initramfs/sidecar outputs and
protocol extraction. It composes roots locally, declares base-image
contract 1 and builder boot ABI 2, uses local kernel checks and budgets,
and obtains the candidate verifier from an authenticated released CLI
instead of building mvm source.

The QEMU/Wasm pack remains locally owned. Probe-owned boot inits and the
runtime-overlay-free harness were already on main.

Upstream code prerequisites have also progressed beyond the PR description:
mvm #4104, #4105 and #4107 are closed; merged mvm #4349 supplies schema-3
and base-contract handling. mvm #4106 remains open for residual shell-init
cleanup, but the producer witness dependency was removed by mvm-images #61.
The remaining release-completion PR, mvm #4238, is still draft.
The latest actual CLI release is v0.22.0, whose assets have no guest-runtime
archive; neither a bare v0.23.0 tag nor a proposed v0.23.1 is release evidence.

An independent focused review found no verified functional defects in
flake wiring, local root composition, kernel packaging or the reproduction
changes. Its eight focused Python tests passed. This does not establish
real published-installer interoperability.

## Validation actually performed

Local `just release-check` passed:

- Immutable workflow action pins.
- NIC-less and rootless static contracts.
- 100 Python tests.
- Five standalone BDD scenarios.

Inspected GitHub CI for this exact PR head:

- Build run `38182667329`: Linux members and kernel checks passed on
  x86_64 and aarch64; the x86_64 rootless QEMU TCG boot and Chromium
  smoke-pack boot steps succeeded. The aarch64 rootless boot step is
  intentionally skipped.
- Reproduce run `38182667278`: all six role/architecture jobs succeeded.
  Downloaded logs contain `rebuild: bit-identical` for builder, rootless
  and default roots on both architectures, including both default and dev.
- Image-owned CI run `38182667281`: QEMU TCG smoke boots succeeded on
  Linux and macOS. The Firecracker job succeeded only at preflight:
  download, install, boot and witness-upload steps were skipped because
  KVM was unavailable. This is not Firecracker boot evidence.

No local Linux build, reproduction or actual VMM boot was run on this
Darwin host. Existing CI evidence is identified separately above.
QEMU TCG on macOS is not native HVF evidence.

## Remaining readiness work

1. Establish a published compatible consumer, signed complete runtime and
   the exact installed-boot report/installer interoperability required by
   `scripts/verify-released-consumer.py`. Unit fixtures do not prove this.
2. Configure real `IMAGE_CONSUMER_TAG`, `IMAGE_CONSUMER_COMMIT` and
   `IMAGE_CONSUMER_RUN_ID` pins only after that evidence exists. Verify
   cross-repository contents, artifact and attestation access with the
   actual release credentials. Do not invent pins or bypass the gate.
3. Obtain native-backend evidence on capable runners; do not substitute
   the skipped Firecracker job or macOS TCG for it.
4. Re-sign changed instruction text through the authorized workflow.
   That workflow documents signing on main and importing its generated
   bundles in a follow-up PR; unchanged bundles do not authenticate the
   changed text.
5. Publish the Linux-only set only after compatible release evidence,
   then advance the consumer image lock. Preserve historical signed sets.

The reviewed PR head's release workflow depended on Build, not Reproduce.
The continuation makes Reproduce callable and requires both jobs to succeed
before publication. Regression tests first failed on the original workflow,
confirming the missing dependency. The earlier reproduction results above
remain evidence for the reviewed head, not the new workflow changes.

Continuation validation: `just release-check` passed 102 Python tests,
five BDD scenarios and the static contracts; `git diff --check` passed.
Actionlint's workflow checks passed with ShellCheck disabled. The full
actionlint invocation reported the existing unused `digest` variable in the
publication asset loop, unrelated to this change. An independent review
approved the workflow dependency and its concurrency, permissions,
artifact selectors and default failure propagation.
No live release workflow execution was performed for the continuation.

No PR was merged, release published, remote review posted or lock advanced
as part of this assessment.
