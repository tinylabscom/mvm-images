# mvm-images

The system-image train for [mvm](https://github.com/tinylabscom/mvm): the guest
images an mvm host boots, built, verified, signed and published here, and
consumed there through one digest-pinned lock file.

This repository is being populated by the migration described in
[`specs/plans/2026-09-16-image-repository-extraction.md`](https://github.com/tinylabscom/mvm/blob/main/specs/plans/2026-09-16-image-repository-extraction.md)
in the `mvm` repository. Governance comes first, sources follow, and the trust
root moves last. The image sources are here now and build in CI; until the
migration reaches its publication workstream, the canonical images are still
published from `mvm`'s `boot-image/vN` releases, and nothing here publishes
anything.

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

## Layout

```text
flake.nix, flake.lock   the one flake: every image, one mvm pin
images/
  builder-vm/image.nix       builder VM kernel + rootfs, Stage 0 rootfs, kernel attrs
  default-tenant/image.nix   default workload microVM (verity-sealed prod, dev)
  runtime-overlay/image.nix  runtime overlay and the glibc/musl SDK sidecars
  initramfs/image.nix        universal initramfs
kernel/                 builder and workload kernel configs; also a standalone flake
qemu-wasm/              QEMU/WebAssembly engine, smoke image and browser pack
packaging/              static checks run on staged artifacts
scripts/                kernel, host-binary, QEMU-wasm and drift tooling
sources/                what was copied from mvm, and the documented rewrites
SOURCES.md              provenance of every copied file
```

The root flake exposes each image role as an attribute set carrying the
attribute names `mvm` used, so `mvm`'s `nix/images/<role>#packages.<system>.<attr>`
is `.#legacyPackages.<system>.<role>.<attr>` here — for example
`.#legacyPackages.x86_64-linux.builder-vm.default` or
`.#legacyPackages.aarch64-linux.runtime-overlay.sdk-sidecar-image-musl`. The
QEMU/WebAssembly outputs are under `qemu-wasm`. The kernel needs nothing from
`mvm`, so `kernel/` stays a flake of its own (`nix build ./kernel#workload-vmlinux`).

## Where the mvm code comes from

The binaries inside the images — the guest agent, the runtime helpers, the SDK
library, the builder's init — are compiled from `mvm` source against `mvm`'s
`Cargo.lock`, and their Nix recipes stay in `mvm`. This repository takes `mvm`
as a flake input pinned to one exact commit (`flake.nix`, recorded in
`flake.lock`) and evaluates `mvm`'s `nix/flake.nix` against it exactly as
`mvm`'s own image flakes do, so for the same commit every derivation is the
same one `mvm` builds. Nothing tracks `mvm`'s `main`, and nothing looks for a
checkout next to this one. [SOURCES.md](SOURCES.md) records which files were
copied from `mvm` and every intended difference;
`scripts/check-source-drift.sh` fails when a copy differs from `mvm` at the
pinned commit by anything else.

### Building locally

The images are Linux artifacts, so build them on Linux (or through a Linux
remote builder):

```sh
nix build .#legacyPackages.x86_64-linux.runtime-overlay.default
nix build .#legacyPackages.x86_64-linux.initramfs.default
nix build .#legacyPackages.x86_64-linux.default-tenant.default --impure
nix build ./kernel#workload-vmlinux
```

The builder VM also needs three static host binaries built outside Nix. Build
them from the pinned commit, then point the image at them:

```sh
scripts/install-host-toolchain.sh          # zig + cargo-zigbuild at mvm's pins
scripts/build-host-binaries.sh x86_64      # prints MVM_HOST_BIN_DIR=...
MVM_HOST_BIN_DIR=... nix build .#legacyPackages.x86_64-linux.builder-vm.default --impure
```

`MVM_WORKSPACE_PATH` must be unset; the flake refuses to evaluate with it,
because `mvm`'s `nix/flake.nix` would otherwise build from whatever checkout it
names. If Nix has a GitHub token configured that the `tinylabscom` organisation
refuses, fetch anonymously with `NIX_CONFIG='access-tokens ='`.

CI builds every image on both architectures on pull requests that touch the
image sources (`.github/workflows/build.yml`) and keeps the results as workflow
artifacts. It never releases or signs.

`.github/workflows/reproduce.yml` (`scripts/compare-same-commit.sh`) builds the
builder VM and the default microVM a second way on the same runner: from
`mvm`'s own in-tree image flakes at the pinned commit. It fails if the
derivations or output files differ, and reports whether a `--rebuild` of the
final derivation is bit-identical. Today it is not for the prod default image,
whose verity superblock carries a random UUID
([tinylabscom/mvm#3499](https://github.com/tinylabscom/mvm/issues/3499)), so
that check reports without failing until the fix reaches the pin.

### Advancing the mvm pin

1. Change the commit in the `mvm` input URL in `flake.nix` and run
   `nix flake lock` (use a full 40-character SHA of a commit on `mvm`'s `main`).
2. Run `scripts/check-source-drift.sh`. Anything `mvm` changed in the copied
   files since the last pin is reported; carry each change over, or record why
   not in `SOURCES.md`. Before moving, `--against <ref> --mvm-git-dir <clone>`
   shows the same report for a candidate commit.
3. If `mvm` bumped nixpkgs or microvm.nix in its image flakes, update the
   matching input here to the same revision; the drift check compares them.
4. Open a pull request. The build workflow rebuilds everything from the new
   commit.

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

That workflow is planned, not built: it arrives with the extraction plan's W5,
and will select a local `mvm` explicitly rather than by finding a sibling. Today
the flake builds only from the pinned commit.
