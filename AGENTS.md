# mvm-images repository instructions

## Ownership

Build only the canonical Linux layer: builder/default/rootless kernels,
sealed generic workload bases, builder VM image, Stage 0 seeds and
QEMU/WebAssembly smoke pack. Nix reproduces local definitions. No mvm source
input, source mirror, source-drift or pin-advance procedure belongs here.
Never compare outputs with retired in-tree mvm image recipes.

The complete signed runtime ships version-locked with mvmctl: overlay,
initramfs, SDK sidecar, guest agent/helpers, mvm-setpriv and GPU shims.
Base images contain no mvm binaries or mvm-authored /init. Do not add runtime
roles. This repository is never part of mvm's merge queue; image events
follow Linux package/kernel/toolchain changes.

## Image design boundary

Produce deterministic, minimal Linux boot inputs, not application packs,
the MVM guest runtime, a fleet controller, or a substitute security policy.
Keep a single declared source of truth for each image role and input. Build
for matching architecture and generic capability profiles; do not select
an image by product, workload name, or host OS. A signed target-specific
artifact may be reused by digest, but cache reuse does not renew trust or
authorize a launch. Never describe a candidate, attestation, or host-only
check as a reproduced, signed, installed-boot witness.

- Measure source-to-artifact time, first-use preparation, byte size, guest
  memory, and cold/warm boot separately on named hardware. Optimize rebuild
  and distribution without widening the base, skipping verification, or
  moving required runtime admission out of its measured launch boundary.
  Keep the default tenant smaller than the rootless capability profile.
- Preserve the NIC-less guest and authenticated vsock network boundary on
  every supported VMM. No SSH, application credentials, host mounts, or
  unapproved binaries in a generic base. Optional hardware capabilities
  must remain optional unless an admitted policy explicitly requires them.
- Publish both architectures as one immutable, reproducible set with exact
  member digests and fresh revocation evidence. Do not advance a consumer
  lock, publish a new set, or claim platform compatibility before the real
  consumer and boot witnesses required below exist. The future of
  target-specific `.mvmpkg` selection belongs to the pack consumer and
  registry; this repository provides only the matching Linux layer.
- Do not name competitors in new prose, filenames or PR text, or attribute
  work to an assistant. Count handwritten non-test production source for
  this repository's preferred one-million-line ceiling, excluding generated
  and vendored code; preserve tests and necessary security boundaries.

## Base and publication contracts

Maintain separate generic default-tenant/rootless-tenant profiles on x86_64
and aarch64. Select matching kernel/rootfs profiles by generic capabilities,
architecture, boot protocol and format, not backend/host OS/workload names.
Rootless supplies namespaces, delegated cgroup v2/controllers, PTYs,
container filesystems and writable-volume-backed storage without widening
the default kernel. Application packages/services/configuration belong in
application or template repositories.

New sets use schema_version 3/base_image_contract 1. Bases emit exact bytes
`1\n` to `/etc/mvm/base-image-contract` from images/base-image-contract.nix.
Fixed identities are 0/901/987/988/989; builder adds UID 902 and boot ABI 2.
Keep all 17 producer Linux members including dev variants; 13 is only the
consumer minimum. Publish both architectures atomically and immutably.
Preserve action pins, provenance, cosign identity, revocation and
reproduction. SLSA subjects derive from the assembler member table for
artifacts, pack manifests, SBOMs and root; never duplicate that list.
Attestation is not reproduction.

This is staged migration, not release evidence. No compatible CLI v0.23.1 is
published yet. Compatible consumer lands before CLI tag; CLI and signed full
runtime must actually publish and installed boots be witnessed before
Linux-only publication. Only afterward may mvm advance images.lock.
Historical signed schema 2 sets/current lock remain untouched. No artificial
set-version cutoff, invented release/run IDs or manual bypass.

## Permanent networking invariant

No guest NIC, TAP, TUN, bridge, veth, macvlan, SLIRP, passt, vpnkit, CNI
dataplane, raw-packet tunnel, guest NAT or guest firewall path. No fallback.
Loopback serves local adapters only. Proxy-aware TCP/UDP, controlled DNS,
mediated ping, typed connectors and ingress use a single authenticated
FlowMux session over vsock; host endpoints own sockets and signed policy.
Rootless preserves verified boot, storage, egress and admission.
The canonical contract is the sibling
`../mvm/public/src/content/docs/guides/networking.md` and published
[networking guide](https://github.com/tinylabscom/mvm/blob/main/public/src/content/docs/guides/networking.md).

## Working tree and validation

Preserve unrelated changes; do not regenerate locks unless requested.
Host Python/static/just checks are fast validation, not Linux build/boot
evidence. Nix and real boots require a configured Linux execution boundary.
Report missing environment accurately. Tests never start mvm, mvmctl or
bin/dev. Boot tests invoke VMMs directly with explicit devices, vsock, no NIC,
a probe-owned init and no runtime overlay or mvm-built bytes. Rootless smoke
directly witnesses UID 901, namespaces, delegated cgroup v2, crun/
fuse-overlayfs and loopback-only networking.

New Linux roles/capabilities update README, producer, both-architecture tests,
build and reproduction workflows. Instruction edits invalidate detached
Sigstore bundles; require real authorized re-signing, never forge signatures.
