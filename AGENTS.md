# mvm-images repository instructions

## Ownership

This repository builds the Linux layer `mvm` boots, and nothing else:

- the kernels: builder, `default-tenant` workload and `rootless-tenant`
  workload;
- the base `default-tenant` and `rootless-tenant` root filesystems;
- the builder VM image;
- the Stage 0 seeds a cold host bootstraps from.

They are built by Nix, reproduced, cosign-signed and published together as
one image set. `mvm` consumes that set through its digest-pinned image lock.
Do not add or preserve a second canonical definition or publication path for
any of them in `mvm`.

The guest runtime belongs to `mvm`: the runtime overlay, the initramfs, the
SDK sidecar, the guest agent and its helpers, `mvm-setpriv` and the GPU shims.
`mvm` ships it with each `mvmctl` release, version-locked to the CLI, and
`mvmctl` assembles the overlay, initramfs and sidecar from it at boot. A guest
change is then a change to `mvm` alone and never an image event here. The
decision and the measurements behind it are in
[tinylabscom/mvm#4100](https://github.com/tinylabscom/mvm/issues/4100).

The tree has not caught up yet. Today it still takes `mvm` as a pinned flake
input, compiles mvm's guest binaries from it, builds the `runtime-overlay` and
`initramfs` roles and both SDK sidecars, and composes both tenant root
filesystems with mvm's `mkGuest`, so they carry mvm binaries and an
mvm-authored `/init`. The builder image is composed with `mkGuest` too but
carries no mvm binary: at builder boot ABI 2, `mvm-host-vm-init`,
`mvm-builderd` and `mvm-setpriv` all arrive in `mvmctl`'s boot payload.
Removing every one of those dependencies is
[tinylabscom/mvm-images#49](https://github.com/tinylabscom/mvm-images/issues/49).
Until it lands:

- Add no new dependency on `mvm`'s source: no new read of the `mvm` input, no
  new role or output built from it, no new file mirrored from it. A change to
  an existing dependency should shrink it, never widen it.
- Keep publishing the guest-runtime roles. Every `mvmctl` released so far
  refuses a set without them, so the first set that drops them waits for an
  `mvm` release that no longer requires them
  ([tinylabscom/mvm#4105](https://github.com/tinylabscom/mvm/issues/4105)).

Do not compare canonical output bytes with retired in-tree image recipes in
`mvm`; reproducibility rebuilds the definitions in this repository.

This repository is never part of `mvm`'s merge queue. An image set changes
when a kernel, a package or a toolchain moves, not because an `mvm` change
merged, and no `mvm` merge may need to build, publish or wait for anything
here. Two edges remain today, both removed under
[tinylabscom/mvm#4108](https://github.com/tinylabscom/mvm/issues/4108): `mvm`'s
dispatch-only guest-image-boot lane builds the runtime overlay from a checkout
of this repository, and its merge-queue `boot-latency` lane boots the published
set's runtime overlay. Do not add another such edge.

Workload-specific packages, services, configuration and tests belong in their
application or template repository. Do not create workload-named images or
kernels here. Express reusable needs as generic image capabilities.

## Workload base-image postures

Maintain two separate generic workload bases for both `x86_64` and `aarch64`:

- `default-tenant`: the smallest sealed image for one admitted workload. Its
  entrypoint is unprivileged and its kernel may omit namespace, cgroup and
  in-guest container-networking facilities.
- `rootless-tenant`: a sealed, unprivileged base for workloads that run their
  own rootless container stack or supervisor. It provides the generic
  capability floor for user/mount/PID/IPC/UTS namespaces, cgroup v2 delegation
  and controllers, container filesystems, PTYs, and writable-volume-backed
  container storage.

The rootless image must preserve the ordinary verified-boot, vsock, storage,
egress and admission boundaries. It must not contain Kubernetes or any other
consumer-specific package, service, configuration or name. Keep it separate
from `default-tenant`; do not widen the default kernel merely because a
rootless consumer needs more facilities.

## Permanent networking invariant

No workload or builder base image has a guest NIC. Ever. Do not add, enable,
attach or assume a NIC, TAP, TUN, bridge, veth pair, macvlan, SLIRP, passt,
vpnkit, CNI dataplane, raw-packet tunnel, guest NAT or guest firewall path.
Do not add a second networking implementation as a development fallback.

Guest loopback is only for local adapters. Proxy-aware TCP/UDP, controlled DNS,
mediated ping, typed connectors and declared ingress use the single
authenticated FlowMux session over vsock. The host endpoint owns external
sockets and applies signed policy. This must match the canonical contract in
the sibling checkout at `../mvm/public/src/content/docs/guides/networking.md`
and in the published
[`mvm` networking guide](https://github.com/tinylabscom/mvm/blob/main/public/src/content/docs/guides/networking.md).

A rootless consumer must use the NIC-less model (for example, host networking
inside its process namespace plus the injected loopback proxy). Never make an
otherwise incompatible CNI or container network work by adding guest devices.

## Image contract

- Publish roles atomically in one immutable image set for both guest
  architectures.
- Emit workload kernel/rootfs roles with an explicit `default_tenant` or
  `rootless_tenant` profile, and require consumers to select both halves from
  the same profile.
- Describe selection through manifest capabilities, architecture, boot
  protocol and artifact format—not host OS, backend name or workload name.
- Do not advertise or require a network-device capability. The networking
  contract is always loopback adapters plus FlowMux over vsock.
- Make `mvm` select and verify published roles through its image lock. A host
  or CLI change must not silently rebuild a canonical base image.
- Preserve reproducibility, source provenance, immutable action pins, signing
  identity, revocation behavior and cross-backend boot evidence.
- Any new role or capability must update the manifest producer, build and
  reproduction workflows, both-architecture tests, and `README.md`.
- Base root filesystems carry no mvm binaries and no mvm-authored `/init`.
  This is the target: the existing `default-tenant` and `rootless-tenant`
  images still do, because `mkGuest` composes them, until
  [tinylabscom/mvm-images#49](https://github.com/tinylabscom/mvm-images/issues/49)
  lands. Do not add an mvm binary to any base image in the meantime.
- Do not add a role whose bytes come from `mvm`'s source. A new role is part
  of the Linux layer or it does not belong here.
- Published sets carry a build-provenance attestation, in SLSA's provenance
  format, for each member artifact, pack manifest and SBOM and for the
  `image-set.json` root, under the release workflow identity
  ([tinylabscom/mvm-images#50](https://github.com/tinylabscom/mvm-images/issues/50)).
  The subjects come from `scripts/assemble-release.py`'s member table; never
  list them a second time in a workflow. Sets up to `image-set/v0.2.4` predate
  it and carry none. The attestation is SLSA Build Level 2 and does not stand
  in for reproduction.

## Working tree

Preserve unrelated user changes. In particular, do not overwrite a local mvm
pin advance or regenerate lock files unless the task explicitly requires it.

## Tests

BDD and end-to-end tests are owned and run here. They must not start `mvm`,
`mvmctl`, `bin/dev`, or any other host-runtime process from the `mvm`
repository. Boot tests invoke the VMM directly against artifacts built here.
Every VMM plan must use an explicit device list, attach vsock, and contain no
network interface or NIC/TAP/TUN fallback. Keep the fast Gherkin contracts in
the ordinary pull-request gate and run real boot probes on capable runners.
The rootless smoke variant must directly witness uid 1000, namespace creation,
delegated cgroup v2, its generic OCI tools, and loopback-only networking. It
must not use `mvm` as a test harness.
Today the rootless witness attaches the runtime overlay as a second block
device; under #49 boot witnesses switch to a small test init and stop
depending on any `mvm`-built bytes. Do not add a new witness that needs the
overlay.
