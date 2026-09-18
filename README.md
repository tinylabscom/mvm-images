# mvm-images

The system-image train for [mvm](https://github.com/tinylabscom/mvm): the guest
images an mvm host boots, built, verified, signed and published here, and
consumed there through one digest-pinned lock file.

This repository is being populated by the migration described in
[`specs/plans/2026-09-16-image-repository-extraction.md`](https://github.com/tinylabscom/mvm/blob/main/specs/plans/2026-09-16-image-repository-extraction.md)
in the `mvm` repository. Governance comes first, sources follow, and the trust
root moves last. Until that migration reaches its publication workstream, the
canonical images are still published from `mvm`'s `boot-image/vN` releases, and
nothing here publishes anything.

## What this repository will own

| Role | Artifacts |
|---|---|
| Builder VM | kernel and rootfs the Nix build jobs run inside |
| Workload kernel | the kernel a workload microVM boots |
| Workload rootfs | the verity-sealed default rootfs, its hash tree and root hash |
| Runtime overlay | the guest runtime binaries overlaid at launch |
| SDK sidecars | the in-guest host-services library, one per C library |
| Stage 0 seeds | the bootstrap kernel and Nix seed a cold host starts from |
| QEMU/WebAssembly smoke pack | the browser-tier smoke artifacts |

Guest architectures: `x86_64` and `aarch64`. Artifacts describe the *guest* —
architecture, boot protocol, format, required devices — never the host
operating system. A host backend declares which of those contracts it can
satisfy, so Firecracker on Linux and HVF on macOS consume the same bytes.

## What it does not own

Host CLI and runtime source, the guest agent, artifact acquisition and
verification code, admission, and the code that boots a builder — Stage 0
orchestration, the builder runner and the VMM drivers — all stay in `mvm`.
Stage 0's *seed inputs* move here; the code that runs Stage 0 does not.

## Release contract

One release publishes one atomic image set. The manifest is the root object and
individual assets are never selected by asking GitHub for "latest". A set that
is missing an architecture or a role does not publish.

Consumers pin a set by digest in `mvm`'s checked-in image lock, which records
the repository, the immutable release tag, the manifest digest and the expected
signing identity. Rolling back is a lock change to a previously verified set,
never a mutation of a published one.

Releases are immutable. A bad set is superseded by publishing revocation
metadata and a new set, never by deleting or overwriting an existing one.

### Cadence and retention

Image sets are published when their inputs change — a kernel bump, a Nix input
update, a guest ABI change — rather than on a calendar. Published releases and
their assets are retained indefinitely, because old `mvm` versions resolve the
exact set they were built against.

## Security

- No secrets and no customer data, ever, in this repository or its artifacts.
  The only credentials any workflow holds are the short-lived OIDC tokens
  Sigstore keyless signing mints.
- Signing happens only in a protected release environment, from a protected tag
  namespace. An untrusted branch cannot mint the allow-listed release identity.
- Third-party actions are pinned by immutable commit SHA.
  `scripts/check-action-pins.sh` refuses anything else on every pull request.
- Report a suspected compromise of a published artifact or signing identity as
  described in [SECURITY.md](SECURITY.md). Response is to publish revocation
  metadata and a superseding set, then move consumers by lock update.

## Working on images

Paired local development with `mvm` is a supported workflow, not an escape
hatch. The intended checkout layout is siblings:

```text
mvmco/
  mvm/
  mvm-images/
```

Locally built images are content-addressed, carry both repositories' commits and
dirty state, and are reported as a development trust tier. A release binary and
production admission refuse them.
